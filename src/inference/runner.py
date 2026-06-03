"""
Inference runner — orchestrates classification of all issues for one model.

Design:
- asyncio.Semaphore inside each classify() call controls concurrency.
  The semaphore is created here and passed down so the runner controls it.
- asyncio.as_completed() gives us progress updates as items finish rather
  than waiting for the full batch before processing any results.
- return_exceptions=False in gather() would kill the whole batch on one error.
  We use as_completed() + individual try/except instead for per-item resilience.
- Wall clock time measures end-to-end real latency including queue time, not just
  API call time. This is the number that matters for production throughput planning.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import pathlib
import subprocess
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import numpy as np

from src.config import Config
from src.inference.client import InferenceClient
from src.inference.prompt import PROMPT_VERSION, get_prompt_hash
from src.models import Classification, Issue, RunManifest

logger = logging.getLogger(__name__)

ISSUES_FINGERPRINT_PATH = pathlib.Path("data/issues.sha256")


def _get_git_sha() -> str:
    """Get current git SHA for RunManifest reproducibility. Returns 'unknown' if not in a git repo."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def _get_dataset_fingerprint() -> str:
    """Read pre-computed SHA-256 fingerprint from data/issues.sha256."""
    if ISSUES_FINGERPRINT_PATH.exists():
        return ISSUES_FINGERPRINT_PATH.read_text().strip()
    # Fallback: compute from issues.json if fingerprint file is missing
    issues_path = pathlib.Path("data/issues.json")
    if issues_path.exists():
        content = issues_path.read_bytes()
        return hashlib.sha256(content).hexdigest()
    return "unknown"


async def run_eval(
    issues: list[Issue],
    model: str,
    config: Config,
    on_progress: Callable[[int, int, str, float], None] | None = None,
) -> tuple[list[Classification], RunManifest]:
    """
    Classify all issues with the given model.

    Args:
        issues: Full issue corpus (scored + unscored)
        model: Model slug (must be in config.yaml pricing table)
        config: Loaded config (includes concurrency, pricing, temperature)
        on_progress: Optional callback(completed, total, model, current_cost) for UI updates

    Returns:
        (classifications, manifest) — classifications in completion order (not input order)
        Use issue_id to join back to the original issues list.
    """
    client = InferenceClient(model, config)
    semaphore = asyncio.Semaphore(config.concurrency)
    wall_start = asyncio.get_event_loop().time()

    results: list[Classification] = []
    completed = 0
    current_cost = 0.0

    tasks = [
        asyncio.create_task(client.classify(issue, semaphore))
        for issue in issues
    ]

    for future in asyncio.as_completed(tasks):
        try:
            result = await future
        except Exception as exc:
            # Catch-all for unexpected errors not handled in client.py
            # (should be rare since client handles rate limits, timeouts, parse errors)
            logger.error(f"Unexpected error classifying issue: {exc}")
            result = Classification(
                issue_id=0,
                model=model,
                prompt_version=PROMPT_VERSION,
                label=None,
                reasoning=None,
                raw_response=str(exc),
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=0.0,
                latency_ms=0.0,
                attempt_count=1,
                error_type="server_error",
                from_cache=False,
            )

        results.append(result)
        completed += 1
        current_cost += result.cost_usd

        if on_progress:
            on_progress(completed, len(issues), model, current_cost)

    wall_seconds = asyncio.get_event_loop().time() - wall_start

    # Operational metrics for manifest (scored_issues filled in by scoring module)
    successful = [r for r in results if r.label is not None]
    non_cached = [r for r in successful if not r.from_cache]
    latencies = [r.latency_ms for r in non_cached]

    float(np.percentile(latencies, 50)) if latencies else 0.0
    float(np.percentile(latencies, 95)) if latencies else 0.0
    len(non_cached) / wall_seconds if wall_seconds > 0 and non_cached else 0.0

    manifest = RunManifest(
        run_id=str(uuid.uuid4())[:8],
        git_sha=_get_git_sha(),
        timestamp=datetime.now(UTC).isoformat(),
        model_a=model,
        model_b="",  # Filled in by the orchestrator for dual-model runs
        prompt_version=PROMPT_VERSION,
        prompt_hash=get_prompt_hash(),
        temperature=config.inference.temperature,
        concurrency=config.concurrency,
        dataset_fingerprint=_get_dataset_fingerprint(),
        pricing_table=config.pricing,
        total_issues=len(issues),
        scored_issues=0,  # Filled in by the scoring module after ground truth matching
    )

    return results, manifest
