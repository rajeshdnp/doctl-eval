"""
Stage 1: Verify live DigitalOcean Serverless Inference model slugs.

Run this BEFORE any other stage that touches model slugs.
Output: colored table of slug status + recommended 4-model sweep config.

Usage:
    python scripts/verify_models.py

Requires:
    MODEL_ACCESS_KEY environment variable (or .env file)

Design: We never hardcode model slugs from tutorials because DO SI model availability
changes. This script is the single source of truth for what's actually live. The output
tells you exactly what to put in config.yaml models.sweep.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import openai
from rich.console import Console
from rich.table import Table
from rich import print as rprint

console = Console()

# Expected slugs from the exercise brief — verified against DO SI docs as of 2025
EXPECTED_SLUGS = [
    "anthropic-claude-haiku-4.5",
    "llama3.3-70b-instruct",
    "openai-gpt-oss-120b",
    "openai-gpt-oss-20b",
]

# Role assignments for sweep config — ordered by expected cost (most→least)
ROLE_PRIORITY = {
    "frontier_baseline": [
        "anthropic-claude-haiku-4.5",
        "anthropic-claude-3-haiku",
        "gpt-4o",
    ],
    "best_open_source": [
        "llama3.3-70b-instruct",
        "llama-3.3-70b",
        "llama3-70b",
        "meta-llama-3.1-70b-instruct",
    ],
    "mid_range": [
        "openai-gpt-oss-120b",
        "mixtral-8x22b",
        "mistral-large",
    ],
    "budget": [
        "openai-gpt-oss-20b",
        "llama3-8b-instruct",
        "mistral-7b",
        "phi-3-mini",
    ],
}

# Reference pricing from the exercise brief — shown in table for comparison
REFERENCE_PRICING: dict[str, dict[str, float]] = {
    "anthropic-claude-haiku-4.5": {"input": 1.00, "output": 5.00},
    "llama3.3-70b-instruct": {"input": 0.65, "output": 0.65},
    "openai-gpt-oss-120b": {"input": 0.10, "output": 0.70},
    "openai-gpt-oss-20b": {"input": 0.05, "output": 0.45},
}


def find_best_match(live_slugs: list[str], candidates: list[str]) -> str | None:
    """Return the first candidate from the priority list that is live."""
    for candidate in candidates:
        for slug in live_slugs:
            if candidate.lower() in slug.lower() or slug.lower() in candidate.lower():
                return slug
    return None


def main() -> None:
    api_key = os.environ.get("MODEL_ACCESS_KEY")
    if not api_key:
        console.print("[red]ERROR:[/red] MODEL_ACCESS_KEY not set. Copy .env.example → .env and add your key.")
        sys.exit(1)

    console.print("\n[bold cyan]doctl-eval — DigitalOcean Serverless Inference Model Verification[/bold cyan]\n")

    client = openai.OpenAI(
        base_url="https://inference.do-ai.run/v1",
        api_key=api_key,
    )

    console.print("Fetching live model list from https://inference.do-ai.run/v1/models ...")
    try:
        models_response = client.models.list()
        live_slugs = sorted([m.id for m in models_response.data])
    except Exception as e:
        console.print(f"[red]ERROR:[/red] Could not reach DO SI API: {e}")
        sys.exit(1)

    console.print(f"Found [bold]{len(live_slugs)}[/bold] live models.\n")

    # ── Table 1: Expected slug status ──────────────────────────────────────────
    table = Table(
        title="Expected Model Status",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Slug", style="cyan", no_wrap=True)
    table.add_column("Status", justify="center")
    table.add_column("Input $/1M", justify="right")
    table.add_column("Output $/1M", justify="right")

    for slug in EXPECTED_SLUGS:
        is_live = slug in live_slugs
        status = "[green]LIVE ✓[/green]" if is_live else "[red]MISSING ✗[/red]"
        pricing = REFERENCE_PRICING.get(slug, {})
        input_price = f"${pricing.get('input', '?'):.2f}" if pricing else "?"
        output_price = f"${pricing.get('output', '?'):.2f}" if pricing else "?"
        table.add_row(slug, status, input_price, output_price)

    console.print(table)

    # ── Table 2: All available models ─────────────────────────────────────────
    all_table = Table(
        title="\nAll Available Models (alphabetical)",
        show_header=True,
        header_style="bold blue",
    )
    all_table.add_column("Model ID", style="dim")

    for slug in live_slugs:
        highlight = "[bold cyan]" if slug in EXPECTED_SLUGS else ""
        end = "[/bold cyan]" if slug in EXPECTED_SLUGS else ""
        all_table.add_row(f"{highlight}{slug}{end}")

    console.print(all_table)

    # ── Recommended sweep config ───────────────────────────────────────────────
    console.print("\n[bold yellow]Recommended 4-model sweep config:[/bold yellow]\n")

    recommended: dict[str, str | None] = {}
    for role, candidates in ROLE_PRIORITY.items():
        match = find_best_match(live_slugs, candidates)
        recommended[role] = match

    sweep_slugs = [v for v in recommended.values() if v]

    rec_table = Table(show_header=True, header_style="bold green")
    rec_table.add_column("Role", style="green")
    rec_table.add_column("Recommended Slug", style="cyan")
    rec_table.add_column("Found?", justify="center")

    for role, slug in recommended.items():
        status = "[green]✓[/green]" if slug else "[red]NOT FOUND[/red]"
        rec_table.add_row(role, slug or "—", status)

    console.print(rec_table)

    # ── Instructions ──────────────────────────────────────────────────────────
    console.print("\n[bold]Next steps:[/bold]")
    console.print("1. Update [cyan]config.yaml[/cyan] models.sweep with the confirmed-live slugs:")
    console.print(f"   sweep: {sweep_slugs}")

    if recommended.get("best_open_source"):
        console.print(f"   default_a: \"{recommended['best_open_source']}\"")
    if recommended.get("budget"):
        console.print(f"   default_b: \"{recommended['budget']}\"")

    console.print("\n2. If any expected slug is MISSING, check the 'All Available Models' table")
    console.print("   for alternatives and update pricing in config.yaml accordingly.\n")
    console.print("3. Commit config.yaml before proceeding to Stage 2.\n")

    # Check if any missing slugs have close alternatives
    for slug in EXPECTED_SLUGS:
        if slug not in live_slugs:
            slug_base = slug.split("-")[0]
            alternatives = [s for s in live_slugs if slug_base in s.lower()]
            if alternatives:
                console.print(f"[yellow]Suggestion:[/yellow] '{slug}' not found. "
                               f"Closest alternatives: {alternatives[:3]}")


if __name__ == "__main__":
    main()
