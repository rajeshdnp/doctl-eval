"""
Canonical data contracts for doctl-eval.

All modules import from here — never define duplicate types in sub-modules.
Changes here affect the cache key (via prompt_version + model fields), so
bump prompt_version in config.yaml when changing Classification schema.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class LabelEnum(StrEnum):
    """Six-class taxonomy for GitHub issue classification.

    Design: We chose 6 classes to cover the dominant issue types in doctl while
    keeping the task tractable for smaller models. 'other' is intentionally broad —
    it absorbs spam, duplicates, and genuine ambiguity. In production, 'other'
    predictions should trigger a human review queue.
    """

    bug = "bug"
    enhancement = "enhancement"
    question = "question"
    documentation = "documentation"
    security = "security"
    other = "other"


class Issue(BaseModel):
    """A GitHub issue after PR filtering and body truncation."""

    id: int
    number: int
    title: str
    body: str | None  # Truncated to 2000 chars at ingestion time
    labels: list[str]  # Raw GitHub label names (e.g. ["bug", "good-first-issue"])
    state: str  # "open" or "closed"
    created_at: str
    updated_at: str


class Classification(BaseModel):
    """
    A single model's classification of a single issue.

    Cache key: (issue_id, model, prompt_version) — all three fields must change
    together for a re-run to produce new results. This is enforced in client.py.

    from_cache=True means cost_usd reflects the recorded cost from the original
    API call, but NO new tokens were consumed in this run. The RunManifest
    reports both billed-cost and cached-cost separately via the scoring module.
    """

    issue_id: int
    model: str
    prompt_version: str
    label: LabelEnum | None  # None if errored — goes to unscored set
    reasoning: str | None
    raw_response: str  # Full raw model output — always saved, enables offline debugging
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float  # (prompt_tokens*input_rate + completion_tokens*output_rate)/1e6
    latency_ms: float
    attempt_count: int
    error_type: (
        Literal["rate_limit", "timeout", "server_error", "parse_error", "refusal"] | None
    )
    from_cache: bool  # True = this was a cache hit (no new API cost incurred)


class RunManifest(BaseModel):
    """
    Immutable record of a single evaluation run.

    Stored alongside results so any reported number can be replayed:
    git checkout {git_sha} && CONCURRENCY={concurrency} python scripts/run_sweep.py
    with the same issues.json (verified by dataset_fingerprint) and prompt
    (verified by prompt_hash).
    """

    run_id: str
    git_sha: str
    timestamp: str  # ISO 8601 UTC
    model_a: str
    model_b: str
    prompt_version: str
    prompt_hash: str  # SHA-256[:16] of prompt file content
    temperature: float
    concurrency: int
    dataset_fingerprint: str  # SHA-256 of issues.json
    pricing_table: dict  # type: ignore[type-arg]  # Snapshot of rates used for this run
    total_issues: int
    scored_issues: int  # Issues with ground-truth labels (filled in by scoring module)


class ModelSummary(BaseModel):
    """
    Aggregated metrics for one model across the full corpus.

    cost_per_correct_classification is the primary business metric — it combines
    both cost efficiency and accuracy into a single number. A cheap model with
    bad accuracy can be MORE expensive per correct answer than a pricier model.
    Show this prominently in the recommendation.
    """

    model: str
    accuracy: float
    macro_f1: float
    weighted_f1: float
    avg_cost_usd: float  # Per call, averaged over all issues
    total_cost_usd: float
    cost_per_correct_classification: float  # KEY: total_cost / n_correct
    p50_ms: float
    p95_ms: float
    throughput_rps: float
    error_rate: float


class SweepMetadata(BaseModel):
    """Metadata block in sweep_results.json."""

    sweep_date: str
    models_evaluated: list[str]
    dataset_fingerprint: str
    prompt_version: str
    total_issues: int
    scored_issues: int


class SweepRecommendation(BaseModel):
    """Auto-generated recommendation from sweep results."""

    model_a: str  # Best balanced model
    model_b: str  # Best budget model
    frontier_baseline: str
    cost_savings_vs_frontier_pct: float
    accuracy_delta_vs_frontier_pct: float  # Negative = accuracy loss vs frontier
    rationale: str


class SweepResults(BaseModel):
    """Top-level structure of data/sweep_results.json."""

    metadata: SweepMetadata
    model_summaries: list[ModelSummary]  # Sorted by cost_per_correct_classification ASC
    recommendation: SweepRecommendation
    # full_results omitted from this model — too large, loaded on demand


class PerClassMetrics(BaseModel):
    """Per-label scoring metrics."""

    precision: float
    recall: float
    f1: float
    support: int
    f1_ci_lower: float = Field(default=0.0)
    f1_ci_upper: float = Field(default=0.0)


class AccuracyCI(BaseModel):
    """Wilson score confidence interval for accuracy."""

    lower: float
    upper: float
    method: str = "wilson"


class McNemar(BaseModel):
    """McNemar's test result for paired classifier comparison."""

    contingency_table: list[list[int]]  # [[both_right, a_right_b_wrong], [a_wrong_b_right, both_wrong]]
    chi2: float
    p_value: float
    is_significant: bool
    verdict: str


class ModelComparison(BaseModel):
    """Head-to-head statistical comparison between two models."""

    agreement_rate: float
    cohens_kappa: float
    mcnemar: McNemar
    disagreements: list[dict]  # type: ignore[type-arg]


class OperationalMetrics(BaseModel):
    """Runtime performance metrics for one model in one run."""

    p50_latency_ms: float
    p95_latency_ms: float
    wall_clock_seconds: float
    throughput_rps: float
    total_cost_usd: float
    avg_cost_per_call_usd: float
    concurrency: int  # Always shown alongside latency — required by exercise
    error_breakdown: dict[str, int]  # {error_type: count}
    error_rate: float
    cache_hit_rate: float
    cost_per_correct_classification: float
