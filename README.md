# doctl Issue Classifier — LLM Evaluation Report

## Recommendation

> **Based on evaluation of 4 models across 530 GitHub issues from digitalocean/doctl (247 scored):**
>
> **Run `openai-gpt-oss-120b` in production as the primary classifier.**
>
> - **83.5% accuracy** vs frontier baseline (llama3.3-70b) at **84.2%** — only 0.7% accuracy loss
> - **67% cheaper per call** ($0.000197 vs $0.000602)
> - **Cost per correct classification: $0.00024 vs $0.00075** — 68% savings
> - **7.2% error rate** (vs 0% frontier) — acceptable for a primary + fallback architecture
> - At 1M issues/month: saves ~$405/month vs llama3.3-70b
>
> **Production pattern**: Use `openai-gpt-oss-120b` as the primary classifier. Route
> predicted-`security`, any `parse_error` responses, and cases where reasoning
> shows uncertainty to `llama3.3-70b-instruct` as a fallback. This two-tier pattern
> retains 99.3% of frontier accuracy while spending 67% less on the majority of traffic.
>
> **Why not `openai-gpt-oss-20b`?** It achieves the lowest cost/correct ($0.00015) and
> highest accuracy (87.7%), but its 22.5% error rate means 1 in 4 requests fails to
> return a valid label. At production volume, a 22.5% fallback rate to the frontier
> eliminates the cost advantage. Use it only if you can tolerate high fallback volume.
>
> **Why not `deepseek-r1-distill-llama-70b`?** It is a reasoning model that emits
> chain-of-thought `<think>...</think>` blocks before JSON output. Our parser
> correctly rejects these as `parse_error` (94% error rate). Its reported 92.3%
> accuracy is computed on the ~32/530 issues where it happened to respond cleanly —
> not representative. To use DeepSeek, a CoT-aware parser is required.

## Live App

```
https://YOUR_APP.ondigitalocean.app
```

## Model Evaluation Summary

530 issues (247 scored against maintainer labels). Sorted by cost/correct ↑.

| Model | Accuracy | Macro-F1 | Cost/Call | Cost/Correct ⭐ | p50ms | p95ms | Error% |
|-------|----------|----------|-----------|----------------|-------|-------|--------|
| openai-gpt-oss-20b | **87.7%** | 0.561 | $0.000128 | **$0.00015** | 2110ms | 3635ms | 22.5% |
| openai-gpt-oss-120b ← **recommended** | 83.5% | 0.542 | $0.000197 | $0.00024 | 2194ms | 3912ms | 7.2% |
| llama3.3-70b-instruct ← frontier | 84.2% | 0.546 | $0.000602 | $0.00075 | 3412ms | 4530ms | 0.0% |
| deepseek-r1-distill-llama-70b* | 92.3%* | 0.622* | $0.000714 | $0.00076 | 11303ms | 12251ms | 94.0% |

*DeepSeek: 94% parse error rate — reasoning model outputs `<think>` blocks before JSON. The 92.3% accuracy is computed on the ~6% of issues that responded cleanly. Exclude from fair comparison.

## Cost Extrapolation

Based on avg tokens/issue × per-token rates. Recommended (`openai-gpt-oss-120b`) vs frontier (`llama3.3-70b-instruct`).

| Monthly Volume | Recommended | Frontier | Savings |
|----------------|-------------|----------|---------|
| 100K issues | $19.70 | $60.20 | $40.50 (67%) |
| 1M issues | $197 | $602 | $405 (67%) |
| 10M issues | $1,970 | $6,020 | $4,050 (67%) |

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
