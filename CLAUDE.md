# doctl-eval — LLM Evaluation Harness

## Scenario

A customer runs GitHub issue classification at high volume against an expensive frontier model.
They suspect they are overpaying. We build the evaluation that tells them what to actually run
in production. The review session IS a customer recommendation delivery.

## 8 Core Principles

1. **REPRODUCIBLE** — Every run emits a RunManifest (git SHA, models, prompt hash, concurrency,
   dataset fingerprint, pricing table, timestamp). Any reported number must be replayable.

2. **CACHE-FIRST** — issues.json fetched once; raw LLM responses cached by
   (issue_id, model, prompt_version). Re-runs cost $0 and are deterministic.

3. **RUNTIME-CONFIGURED** — CONCURRENCY is a runtime env var — never baked into the image.
   Models, concurrency, temperature, pricing table in config.yaml + .env.
   "Configurable without rebuilding the container" is an explicit exercise requirement.

4. **PER-ISSUE INFERENCE (HARD RULE)** — Each issue = one inference request. Never batch
   multiple issues into a single prompt. The customer needs per-call cost, per-call latency,
   and individually-retryable failures.

5. **COST IS FIRST-CLASS** — cost_usd = (prompt_tokens × input_rate + completion_tokens ×
   output_rate) / 1_000_000. Both rates from config. Report cost/call AND cost/correct-
   classification (the key business metric).

6. **TYPED ERROR TAXONOMY** — Distinguish rate_limit · timeout · server_error · parse_error ·
   refusal. Retry only rate_limit / server_error / timeout. Failed items return null label with
   error_type set. Never crash the batch on a single failure.

7. **CLEAN SEPARATION** — ingestion → ground_truth → inference → scoring → api → frontend.
   Each module has a clear input/output contract. Never mix concerns.

8. **PROVIDER-AGNOSTIC** — Use the OpenAI Python SDK with base_url pointing at DO.
   The methodology must port cleanly to any inference provider.

## Data Contracts

```python
from pydantic import BaseModel
from enum import Enum
from typing import Literal

class LabelEnum(str, Enum):
    bug = "bug"
    enhancement = "enhancement"
    question = "question"
    documentation = "documentation"
    security = "security"
    other = "other"

class Issue(BaseModel):
    id: int
    number: int
    title: str
    body: str | None
    labels: list[str]          # Raw GitHub label names
    state: str                 # "open" or "closed"
    created_at: str
    updated_at: str

class Classification(BaseModel):
    issue_id: int
    model: str
    prompt_version: str
    label: LabelEnum | None    # None if errored
    reasoning: str | None
    raw_response: str          # Full raw model output — always saved
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float            # (prompt_tokens*input_rate + completion_tokens*output_rate)/1e6
    latency_ms: float
    attempt_count: int
    error_type: Literal["rate_limit","timeout","server_error","parse_error","refusal"] | None
    from_cache: bool           # True = this was a cache hit (cost is $0 actual)

class RunManifest(BaseModel):
    run_id: str
    git_sha: str
    timestamp: str
    model_a: str
    model_b: str
    prompt_version: str
    prompt_hash: str           # SHA-256 of prompt file content
    temperature: float
    concurrency: int
    dataset_fingerprint: str   # SHA-256 of issues.json
    pricing_table: dict        # Snapshot of rates used
    total_issues: int
    scored_issues: int         # Issues with ground-truth labels

class ModelSummary(BaseModel):
    model: str
    accuracy: float
    macro_f1: float
    weighted_f1: float
    avg_cost_usd: float
    total_cost_usd: float
    cost_per_correct_classification: float  # KEY business metric
    p50_ms: float
    p95_ms: float
    throughput_rps: float
    error_rate: float
```

## Design Reminders

> Comment every non-obvious design decision. You will defend these under questioning.

- `data/cache/` is gitignored — raw LLM responses only, rebuild from cache is free
- `data/issues.json` IS committed — stable corpus, never re-fetch unless --refresh
- `data/ground_truth.json` IS committed — reproducible labeled set
- `data/sweep_results.json` IS committed — the evidence behind the recommendation
- Temperature is always 0.0 for deterministic, structured output
- Never hardcode model slugs — always verify live via `/v1/models` first
