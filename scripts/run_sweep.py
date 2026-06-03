"""
Stage 6: Multi-model sweep script.

Runs inference across all models in config.yaml models.sweep, scores each against
ground truth, and produces data/sweep_results.json.

This is the artifact that proves the two recommended models were chosen from a wider
field. "What else did you consider?" expects DATA in the results, not hand-waving.

The caching from Stage 5 means running the sweep a second time costs $0.
Always do a --sample 50 dry run first to validate the pipeline end-to-end.

Usage:
    python scripts/run_sweep.py
    python scripts/run_sweep.py --sample 50         # Fast validation
    python scripts/run_sweep.py --models llama3.3-70b-instruct openai-gpt-oss-20b
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import click
from rich.console import Console
from rich.table import Table

from src.config import get_config
from src.ground_truth.builder import build_ground_truth
from src.inference.runner import run_eval
from src.models import (
    Issue,
    LabelEnum,
    ModelSummary,
    SweepMetadata,
    SweepRecommendation,
)
from src.scoring.metrics import (
    compute_model_metrics,
    compute_operational_metrics,
)

console = Console()

SWEEP_RESULTS_PATH = Path("data/sweep_results.json")
SWEEP_DIR = Path("data/sweep")


async def run_model(
    model: str,
    issues: list[Issue],
    ground_truth_items: list[dict[str, Any]],
    config: Any,
) -> tuple[str, dict[str, Any], dict[str, Any], list[dict[str, Any]], float]:
    """Run eval for one model. Returns (model, metrics, operational, classifications, wall_s)."""
    import time

    console.print(f"\n[cyan]Running model: {model}[/cyan]")
    gt = [(item["id"], LabelEnum(item["ground_truth_label"])) for item in ground_truth_items]

    wall_start = time.monotonic()

    def progress(completed: int, total: int, mdl: str, cost: float) -> None:
        if completed % 50 == 0 or completed == total:
            console.print(
                f"  [{mdl}] {completed}/{total} "
                f"(cost so far: ${cost:.4f})"
            )

    classifications, manifest = await run_eval(issues, model, config, on_progress=progress)
    wall_seconds = time.monotonic() - wall_start

    # Score against GT
    metrics = compute_model_metrics(classifications, gt, model)
    n_correct = metrics["n_correct"]
    n_scored = metrics["n_scored"]

    # Operational metrics
    ops = compute_operational_metrics(classifications, wall_seconds, config.concurrency, n_correct)

    clf_dicts = [c.model_dump() for c in classifications]

    console.print(
        f"  ✓ {model}: accuracy={metrics['overall_accuracy']:.3f}, "
        f"cost/correct=${metrics['cost_per_correct_classification']:.5f}, "
        f"errors={ops.error_rate:.1%}"
    )

    return model, metrics, ops.model_dump(), clf_dicts, wall_seconds


def build_model_summary(
    model: str,
    metrics: dict[str, Any],
    ops: dict[str, Any],
) -> ModelSummary:
    """Combine scoring metrics + operational metrics into a ModelSummary."""
    return ModelSummary(
        model=model,
        accuracy=metrics.get("overall_accuracy", 0.0),
        macro_f1=metrics.get("macro_f1", 0.0),
        weighted_f1=metrics.get("weighted_f1", 0.0),
        avg_cost_usd=ops.get("avg_cost_per_call_usd", 0.0),
        total_cost_usd=ops.get("total_cost_usd", 0.0),
        cost_per_correct_classification=metrics.get("cost_per_correct_classification", float("inf")),
        p50_ms=ops.get("p50_latency_ms", 0.0),
        p95_ms=ops.get("p95_latency_ms", 0.0),
        throughput_rps=ops.get("throughput_rps", 0.0),
        error_rate=ops.get("error_rate", 0.0),
    )


def generate_recommendation(
    summaries: list[ModelSummary],
    frontier_slug: str | None = None,
) -> SweepRecommendation:
    """
    Auto-generate recommendation based on cost_per_correct_classification.

    The frontier baseline is the most expensive model by avg_cost_usd.
    model_a = best balanced (highest accuracy within 10% cost of the best cost/correct)
    model_b = best budget (lowest cost/correct overall, if different from model_a)
    """
    if not summaries:
        return SweepRecommendation(
            model_a="",
            model_b="",
            frontier_baseline="",
            cost_savings_vs_frontier_pct=0.0,
            accuracy_delta_vs_frontier_pct=0.0,
            rationale="No models evaluated.",
        )

    # Frontier = most expensive model (what customer is currently paying)
    frontier = max(summaries, key=lambda s: s.avg_cost_usd)
    if frontier_slug:
        # Use configured slug if provided
        matches = [s for s in summaries if s.model == frontier_slug]
        if matches:
            frontier = matches[0]

    # Sort by cost_per_correct_classification ascending (best business metric first)
    sorted_summaries = sorted(
        summaries,
        key=lambda s: s.cost_per_correct_classification if s.cost_per_correct_classification != float("inf") else 9999,
    )

    model_a = sorted_summaries[0]  # Best cost/correct

    # model_b = second-best, preferring ones cheaper than model_a
    model_b = sorted_summaries[1] if len(sorted_summaries) > 1 else model_a

    # Cost savings vs frontier
    if frontier.avg_cost_usd > 0 and model_a.avg_cost_usd > 0:
        cost_savings_pct = (
            (frontier.avg_cost_usd - model_a.avg_cost_usd) / frontier.avg_cost_usd * 100
        )
    else:
        cost_savings_pct = 0.0

    # Accuracy delta vs frontier
    accuracy_delta_pct = (model_a.accuracy - frontier.accuracy) * 100

    rationale = (
        f"{model_a.model} achieves {model_a.accuracy:.1%} accuracy "
        f"({accuracy_delta_pct:+.1f}% vs frontier {frontier.model}) "
        f"at {100 - cost_savings_pct:.0f}% of frontier cost. "
        f"Cost per correct classification: ${model_a.cost_per_correct_classification:.5f} "
        f"vs frontier ${frontier.cost_per_correct_classification:.5f}."
    )

    return SweepRecommendation(
        model_a=model_a.model,
        model_b=model_b.model,
        frontier_baseline=frontier.model,
        cost_savings_vs_frontier_pct=round(cost_savings_pct, 2),
        accuracy_delta_vs_frontier_pct=round(accuracy_delta_pct, 2),
        rationale=rationale,
    )


def print_comparison_table(summaries: list[ModelSummary], recommendation: SweepRecommendation) -> None:
    """Print rich comparison table sorted by cost_per_correct_classification."""
    table = Table(
        title="Model Sweep Results (sorted by Cost/Correct ↑)",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Model", style="cyan", no_wrap=True)
    table.add_column("Accuracy", justify="right")
    table.add_column("Macro-F1", justify="right")
    table.add_column("Avg Cost/Call", justify="right")
    table.add_column("Cost/Correct ⭐", justify="right")
    table.add_column("p50ms", justify="right")
    table.add_column("p95ms", justify="right")
    table.add_column("Error%", justify="right")
    table.add_column("Role", justify="center")

    for s in summaries:
        role = ""
        if s.model == recommendation.model_a:
            role = "[green]Rec A[/green]"
        elif s.model == recommendation.model_b:
            role = "[blue]Rec B[/blue]"
        elif s.model == recommendation.frontier_baseline:
            role = "[red]Frontier[/red]"

        cpc = s.cost_per_correct_classification
        cpc_str = f"${cpc:.5f}" if cpc != float("inf") else "∞"

        table.add_row(
            s.model,
            f"{s.accuracy:.3f}",
            f"{s.macro_f1:.3f}",
            f"${s.avg_cost_usd:.6f}",
            cpc_str,
            f"{s.p50_ms:.0f}",
            f"{s.p95_ms:.0f}",
            f"{s.error_rate:.1%}",
            role,
        )

    console.print(table)


@click.command()
@click.option("--sample", default=0, help="Run on N random issues (0 = full corpus, seed=42)")
@click.option("--models", multiple=True, help="Override config sweep list (space-separated slugs)")
def main(sample: int, models: tuple[str, ...]) -> None:
    """Run multi-model sweep and save results to data/sweep_results.json."""
    config = get_config()

    # Load corpus
    issues_path = Path("data/issues.json")
    if not issues_path.exists():
        console.print("[red]ERROR:[/red] data/issues.json not found. Run ingestion first.")
        sys.exit(1)

    raw_issues = json.loads(issues_path.read_text())
    from src.models import Issue as IssueModel

    all_issues = [IssueModel(**i) for i in raw_issues]

    # Optionally sample corpus for fast iteration
    if sample > 0:
        rng = random.Random(42)  # Reproducible sampling
        all_issues = rng.sample(all_issues, min(sample, len(all_issues)))
        console.print(f"[yellow]Sampling {len(all_issues)} issues (seed=42)[/yellow]")

    # Build ground truth
    gt_data = build_ground_truth(all_issues)
    scored_items = gt_data["scored_issues"]
    gt_by_id = {item["id"]: LabelEnum(item["ground_truth_label"]) for item in scored_items}

    # Determine which models to run
    sweep_models = list(models) if models else config.models.sweep
    if not sweep_models:
        console.print(
            "[red]ERROR:[/red] No models in sweep list. "
            "Run scripts/verify_models.py and update config.yaml models.sweep"
        )
        sys.exit(1)

    console.print(f"\nRunning sweep over {len(sweep_models)} models × {len(all_issues)} issues")
    console.print(f"Scored set: {len(scored_items)} issues\n")

    # Run models sequentially to avoid rate limit collisions
    # (Each model can run concurrently internally via semaphore)
    summaries: list[ModelSummary] = []
    all_results: dict[str, list[dict[str, Any]]] = {}
    all_metrics: dict[str, dict[str, Any]] = {}
    all_ops: dict[str, dict[str, Any]] = {}

    SWEEP_DIR.mkdir(parents=True, exist_ok=True)

    for model in sweep_models:
        model_slug, metrics, ops, clf_dicts, wall_s = asyncio.run(
            run_model(model, all_issues, scored_items, config)
        )

        summary = build_model_summary(model_slug, metrics, ops)
        summaries.append(summary)
        all_results[model_slug] = clf_dicts
        all_metrics[model_slug] = metrics
        all_ops[model_slug] = ops

        # Per-model results file
        slug_safe = model_slug.replace("/", "-")
        per_model_path = SWEEP_DIR / f"{slug_safe}.json"
        per_model_path.write_text(json.dumps({
            "model": model_slug,
            "metrics": metrics,
            "operational": ops,
            "classifications": clf_dicts,
        }, indent=2, default=str))

    # Sort summaries by cost_per_correct_classification (business metric sort)
    summaries.sort(
        key=lambda s: s.cost_per_correct_classification
        if s.cost_per_correct_classification != float("inf") else 9999
    )

    recommendation = generate_recommendation(summaries)
    print_comparison_table(summaries, recommendation)

    # Compute dataset fingerprint
    issues_sha_path = Path("data/issues.sha256")
    dataset_fingerprint = issues_sha_path.read_text().strip() if issues_sha_path.exists() else "unknown"

    # Sweep results
    sweep_results = {
        "metadata": SweepMetadata(
            sweep_date=datetime.now(timezone.utc).isoformat(),
            models_evaluated=sweep_models,
            dataset_fingerprint=dataset_fingerprint,
            prompt_version="v1",
            total_issues=len(all_issues),
            scored_issues=len(scored_items),
        ).model_dump(),
        "model_summaries": [s.model_dump(mode="json") for s in summaries],
        "recommendation": recommendation.model_dump(),
        "full_results": all_results,
    }

    SWEEP_RESULTS_PATH.write_text(json.dumps(sweep_results, indent=2, default=str))
    console.print(f"\n[green]Saved sweep results to {SWEEP_RESULTS_PATH}[/green]")
    console.print("[bold]Recommendation:[/bold]", recommendation.rationale)
    console.print("\nCommit data/sweep_results.json — it is a required deliverable.")


if __name__ == "__main__":
    main()
