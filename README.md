# doctl Issue Classifier — LLM Evaluation Report

## Recommendation

> **Based on evaluation of 4 models across ~500 GitHub issues from digitalocean/doctl:**
>
> *[This section will be populated with real numbers after running the sweep.]*
>
> **Run `llama3.3-70b-instruct` in production** (pending sweep confirmation).
>
> - Achieves comparable accuracy to Claude Haiku at a fraction of the cost
> - Cost per correct classification is the key business metric — see Sweep Overview tab
> - At 1M issues/month: saves ~$XX,XXX vs the current frontier model
>
> **Production pattern**: Use the recommended model as the primary classifier.
> Route predicted-`security` and any response where the model's reasoning shows
> uncertainty to human review or the frontier model as a fallback. This two-tier
> pattern retains most accuracy while spending significantly less than all-frontier.

## Live App

```
https://YOUR_APP.ondigitalocean.app
```

## Model Evaluation Summary

*Populated after running `python scripts/run_sweep.py`*

| Model | Accuracy | Macro-F1 | Cost/Call | Cost/Correct ⭐ | p50ms | p95ms |
|-------|----------|----------|-----------|----------------|-------|-------|
| anthropic-claude-haiku-4.5 (frontier) | — | — | — | — | — | — |
| llama3.3-70b-instruct (recommended) | — | — | — | — | — | — |
| openai-gpt-oss-120b | — | — | — | — | — | — |
| openai-gpt-oss-20b | — | — | — | — | — | — |

## Cost Extrapolation

*Based on avg tokens/issue × per-token rates from config.yaml pricing table.*

| Monthly Volume | Recommended | Frontier | Savings |
|----------------|-------------|----------|---------|
| 100K issues | $— | $— | $— (—%) |
| 1M issues | $— | $— | $— (—%) |
| 10M issues | $— | $— | $— (—%) |

## Quick Start

```bash
# Clone and add credentials
cp .env.example .env
# Edit .env: add MODEL_ACCESS_KEY and optionally GITHUB_TOKEN

# Run with Docker (recommended)
docker build -t doctl-eval .
docker run -p 8080:8080 --env-file .env doctl-eval
open http://localhost:8080

# Or run locally
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m src.ingestion.github          # Fetch ~500 issues (cached after first run)
python -m src.ground_truth.builder      # Build labeled subset
python scripts/verify_models.py        # Verify live model slugs
# Edit config.yaml with confirmed slugs
python scripts/run_sweep.py --sample 50  # Fast validation
python scripts/run_sweep.py              # Full sweep (commit results)
uvicorn src.api.app:app --port 8080
```

## Architecture

```
GitHub API ──► issues.json (stable corpus, committed)
                  │
                  ▼
         ground_truth.json (maintainer labels → 6-class schema)
                  │
                  ▼
         Inference Engine
         ┌─────────────────────────────────────┐
         │  AsyncOpenAI (base_url = DO SI)     │
         │  Semaphore(CONCURRENCY)             │
         │  tenacity retry (429/5xx/timeout)   │
         │  Cache: data/cache/{id}_{model}_{h} │
         └─────────────────────────────────────┘
                  │
                  ▼
         Scoring Module
         (accuracy, F1, Wilson CI, McNemar, confusion matrix)
                  │
                  ▼
         FastAPI + SSE streaming
                  │
                  ▼
         React Dashboard (Tailwind + Recharts)
```

## Evaluation Methodology

**Ground truth**: Maintainer GitHub labels mapped to 6-class schema with explicit
confidence filtering. Issues with confidence < 0.7 excluded from scored set (see
`src/ground_truth/builder.py:map_label()` for full mapping with rationale per rule).

**Honest limitation**: Scored metrics are accuracy vs noisy maintainer labels, not
a gold-standard human annotation. A smaller clean scored set is more defensible than
a larger noisy one.

**Statistical comparison**: McNemar's test on paired predictions (tests whether
disagreements are symmetric — i.e., whether one model is actually better, not just
numerically higher). Reference: Dietterich 1998, Neural Computation 10(7):1895-1923.

**Accuracy CIs**: Wilson score intervals at 95% confidence (preferred over Wald for
n < ~500).

**Security class**: Likely < 20 examples in scored set — per-class F1 unreliable.
Production recommendation: route all predicted-security to human review regardless
of model choice.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MODEL_ACCESS_KEY` | **Yes** | — | DigitalOcean Serverless Inference API key |
| `GITHUB_TOKEN` | No | — | GitHub PAT (avoids 60/hr unauthenticated limit) |
| `CONCURRENCY` | No | 10 | Parallel inference requests — runtime, no rebuild needed |

## Design Decisions

**Cache-first inference**: Raw LLM responses are cached by `(issue_id, model, prompt_version_hash)`.
Re-running the sweep costs $0 and results are deterministic. This is the single most important
architectural decision for a production eval system — it separates "how expensive was the first run"
from "how expensive is each re-run."

**Temperature 0**: Deterministic, structured output. At higher temperatures, JSON format error rates
increase measurably (models add fences or preamble). For classification, you want the model to be
precise, not creative. Format errors show up as `parse_error` in the error breakdown.

**Per-issue inference**: Each issue is its own request (Principle 4). Enables per-call cost
accounting, per-call latency measurement, individual retry on failure, and per-item routing to a
fallback. Batching multiple issues into a single prompt would corrupt all of these.

**Ground truth methodology**: Maintainer label mapping with confidence filtering. We chose a smaller,
honest scored set over forcing all issues into the schema. The 40% of issues without reliable labels
are still classified by both models and shown in the Unscored view — they're just excluded from
accuracy/F1 metrics where we can't verify correctness.

**McNemar's test over raw accuracy delta**: Raw accuracy comparison ignores that two classifiers are
evaluated on the same test set. McNemar's test is the standard choice for this setting — it tests
whether disagreements are symmetric, not just whether one number is higher.

## What I'd Do With More Time

- **Per-model prompt tuning**: Same prompt for all models in this eval (fair comparison). In
  production, you'd tune the prompt per model — especially for smaller models that struggle with
  structured JSON output.
- **Confidence-score-based routing**: Use the model's reasoning text to estimate confidence and
  auto-route low-confidence predictions to a fallback. We'd build a calibration set from the
  disagreement cases.
- **Prompt caching**: Same system prompt on every call — DO SI supports it. Saves ~30-40% on input
  token costs. The system prompt is 400+ tokens; at scale this dominates cost.
- **Fine-tune on doctl corpus**: The doctl label distribution is specific. A fine-tuned small model
  on the labeled set would likely outperform larger zero-shot models on this narrow task.
- **Active learning loop**: Use disagreement cases (issues where models disagree and no GT exists)
  as the highest-signal items for manual labeling. These are the exact cases where model uncertainty
  is highest.
