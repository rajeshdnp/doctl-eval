"""
Stage 8: FastAPI backend.

Design:
- POST /api/run uses SSE (Server-Sent Events) to stream progress as issues complete.
  This avoids the user staring at a blank screen during a 5-minute run. The SSE
  stream emits progress events; the final event includes the run_id for result lookup.
- /api/sweep serves pre-computed results instantly — no inference triggered.
  This is the "recommendation" endpoint used by the banner and sweep overview tab.
- CORS is restricted to localhost:5173 in dev. In production (container), the React
  app is served from the same origin so CORS headers are irrelevant.
- The app mounts the React static build at "/" — single container serves both API and UI.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config import get_config
from src.inference.runner import run_eval
from src.models import Classification, Issue, LabelEnum
from src.scoring.metrics import (
    compare_models,
    compute_model_metrics,
    compute_operational_metrics,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="doctl-eval", version="1.0.0")

# CORS for Vite dev server (localhost:5173)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:8080"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Paths
DATA_DIR = Path("data")
ISSUES_PATH = DATA_DIR / "issues.json"
GROUND_TRUTH_PATH = DATA_DIR / "ground_truth.json"
SWEEP_RESULTS_PATH = DATA_DIR / "sweep_results.json"
RUNS_DIR = DATA_DIR / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)


# ── Startup validation ─────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup_validation() -> None:
    """Log warnings on startup if required data files are missing."""
    if not ISSUES_PATH.exists():
        logger.warning(
            "data/issues.json not found. Run 'python -m src.ingestion.github' first. "
            "API will start but /api/run will fail."
        )
    if not GROUND_TRUTH_PATH.exists():
        logger.warning(
            "data/ground_truth.json not found. Run 'python -m src.ground_truth.builder'. "
            "Scoring will not work."
        )


# ── Request/Response models ────────────────────────────────────────────────────
class RunRequest(BaseModel):
    model_a: str
    model_b: str
    concurrency: int | None = None


# ── Helpers ────────────────────────────────────────────────────────────────────
def _load_issues() -> list[Issue]:
    if not ISSUES_PATH.exists():
        raise HTTPException(status_code=503, detail="Issues corpus not loaded. Run ingestion.")
    raw = json.loads(ISSUES_PATH.read_text())
    return [Issue(**item) for item in raw]


def _load_ground_truth() -> dict:  # type: ignore[type-arg]
    if not GROUND_TRUTH_PATH.exists():
        raise HTTPException(status_code=503, detail="Ground truth not built.")
    return json.loads(GROUND_TRUTH_PATH.read_text())


def _model_display_name(slug: str) -> str:
    """Convert model slug to a human-readable display name."""
    mapping = {
        "anthropic-claude-haiku-4.5": "Claude Haiku 4.5",
        "llama3.3-70b-instruct": "Llama 3.3 70B",
        "openai-gpt-oss-120b": "GPT OSS 120B",
        "openai-gpt-oss-20b": "GPT OSS 20B",
    }
    if slug in mapping:
        return mapping[slug]
    # Generic: title-case the slug
    return slug.replace("-", " ").replace("_", " ").title()


def _model_role(slug: str, frontier: str | None, model_a: str | None, model_b: str | None) -> str:
    if slug == frontier:
        return "frontier_baseline"
    if slug == model_a:
        return "open_source"
    if slug == model_b:
        return "budget"
    return "mid_range"


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health() -> dict:  # type: ignore[type-arg]
    corpus_loaded = ISSUES_PATH.exists()
    sweep_available = SWEEP_RESULTS_PATH.exists()
    corpus_size = 0
    scored_size = 0

    if corpus_loaded:
        try:
            issues = json.loads(ISSUES_PATH.read_text())
            corpus_size = len(issues)
        except Exception:
            pass

    if GROUND_TRUTH_PATH.exists():
        try:
            gt = json.loads(GROUND_TRUTH_PATH.read_text())
            scored_size = gt.get("scored_count", 0)
        except Exception:
            pass

    return {
        "status": "ok",
        "corpus_loaded": corpus_loaded,
        "sweep_available": sweep_available,
        "corpus_size": corpus_size,
        "scored_size": scored_size,
    }


@app.get("/api/models")
async def get_models() -> list[dict]:  # type: ignore[type-arg]
    """Return configured models with pricing info."""
    config = get_config()
    models = config.models.sweep or []

    # Get recommendation for role assignment
    frontier = model_a = model_b = None
    if SWEEP_RESULTS_PATH.exists():
        sweep = json.loads(SWEEP_RESULTS_PATH.read_text())
        rec = sweep.get("recommendation", {})
        frontier = rec.get("frontier_baseline")
        model_a = rec.get("model_a")
        model_b = rec.get("model_b")

    result = []
    for slug in models:
        try:
            pricing = config.get_pricing(slug)
        except ValueError:
            pricing = {"input": 0.0, "output": 0.0}

        result.append({
            "slug": slug,
            "display_name": _model_display_name(slug),
            "input_price_per_1m": pricing.get("input", 0.0),
            "output_price_per_1m": pricing.get("output", 0.0),
            "role": _model_role(slug, frontier, model_a, model_b),
        })

    return result


@app.get("/api/corpus")
async def get_corpus() -> dict:  # type: ignore[type-arg]
    """Return corpus metadata from ground_truth.json."""
    gt = _load_ground_truth()

    # Fingerprint from issues.sha256
    sha_path = DATA_DIR / "issues.sha256"
    fingerprint = sha_path.read_text().strip() if sha_path.exists() else "unknown"

    return {
        "total": gt.get("scored_count", 0) + gt.get("unscored_count", 0),
        "scored": gt.get("scored_count", 0),
        "unscored": gt.get("unscored_count", 0),
        "coverage_pct": gt.get("coverage_pct", 0.0),
        "fingerprint": fingerprint,
        "class_distribution": gt.get("class_distribution", {}),
        "caveats": gt.get("caveats", []),
        "low_support_classes": gt.get("low_support_classes", []),
    }


@app.get("/api/sweep")
async def get_sweep() -> dict:  # type: ignore[type-arg]
    """Return pre-computed sweep results (instant, no inference)."""
    if not SWEEP_RESULTS_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="Sweep results not available. Run 'python scripts/run_sweep.py' first.",
        )
    data = json.loads(SWEEP_RESULTS_PATH.read_text())
    # Exclude full_results from this endpoint (too large — available at /api/sweep/full)
    return {k: v for k, v in data.items() if k != "full_results"}


@app.post("/api/run")
async def run_evaluation(request: RunRequest) -> StreamingResponse:
    """
    Run inference for model_a and model_b, stream SSE progress.

    Both models run concurrently via separate asyncio tasks. Progress events
    are queued and yielded as SSE. The final event includes run_id for polling.
    """
    config = get_config()
    issues = _load_issues()
    gt = _load_ground_truth()

    # Override concurrency if specified
    effective_concurrency = request.concurrency or config.concurrency

    run_id = str(uuid.uuid4())[:8]
    progress_queue: asyncio.Queue[dict] = asyncio.Queue()  # type: ignore[type-arg]

    def make_progress_cb(model: str) -> object:
        loop = asyncio.get_running_loop()  # capture running loop; get_event_loop() deprecated in 3.10+
        def cb(completed: int, total: int, mdl: str, cost: float) -> None:
            loop.call_soon_threadsafe(
                progress_queue.put_nowait,
                {
                    "type": "progress",
                    "model": model,
                    "completed": completed,
                    "total": total,
                    "current_cost": round(cost, 6),
                },
            )

        return cb

    async def run_both() -> dict:  # type: ignore[type-arg]
        """Run both models concurrently and compute results."""
        wall_start = time.monotonic()

        task_a = asyncio.create_task(
            run_eval(issues, request.model_a, config, on_progress=make_progress_cb(request.model_a))  # type: ignore[arg-type]
        )
        task_b = asyncio.create_task(
            run_eval(issues, request.model_b, config, on_progress=make_progress_cb(request.model_b))  # type: ignore[arg-type]
        )

        (results_a, manifest_a), (results_b, manifest_b) = await asyncio.gather(task_a, task_b)
        wall_seconds = time.monotonic() - wall_start

        # Ground truth for scoring
        scored_items = gt.get("scored_issues", [])
        gt_pairs = [(item["id"], LabelEnum(item["ground_truth_label"])) for item in scored_items]

        # Issues metadata for disagreement context
        issues_meta = {i.id: {"number": i.number, "title": i.title} for i in issues}

        # Score both models
        metrics_a = compute_model_metrics(results_a, gt_pairs, request.model_a)
        metrics_b = compute_model_metrics(results_b, gt_pairs, request.model_b)

        # Comparison
        comparison = compare_models(results_a, results_b, gt_pairs, issues_meta)

        # Operational metrics
        ops_a = compute_operational_metrics(
            results_a, wall_seconds, effective_concurrency, metrics_a["n_correct"]
        )
        ops_b = compute_operational_metrics(
            results_b, wall_seconds, effective_concurrency, metrics_b["n_correct"]
        )

        # Unscored view data
        scored_ids = {item["id"] for item in scored_items}
        unscored_ids = {i.id for i in issues if i.id not in scored_ids}

        a_unscored = [c for c in results_a if c.issue_id in unscored_ids and c.label]
        b_unscored = [c for c in results_b if c.issue_id in unscored_ids and c.label]

        def class_dist(clfs: list[Classification]) -> dict[str, int]:
            d: dict[str, int] = {}
            for c in clfs:
                if c.label:
                    d[c.label.value] = d.get(c.label.value, 0) + 1
            return d

        a_unscored_ids = {c.issue_id for c in a_unscored}
        b_unscored_ids = {c.issue_id for c in b_unscored}
        common_unscored = sorted(a_unscored_ids & b_unscored_ids)

        a_unscored_map = {c.issue_id: c for c in a_unscored}
        b_unscored_map = {c.issue_id: c for c in b_unscored}

        unscored_agreement = sum(
            1 for iid in common_unscored
            if a_unscored_map.get(iid, None) and b_unscored_map.get(iid, None)
            and a_unscored_map[iid].label == b_unscored_map[iid].label
        )
        unscored_agreement_rate = (
            unscored_agreement / len(common_unscored) if common_unscored else 0.0
        )

        def _label_val(clf_map: dict, iid: int) -> str | None:
            clf = clf_map.get(iid)
            return clf.label.value if clf and clf.label else None

        unscored_disagreements = [
            {
                "issue_id": iid,
                "issue_number": issues_meta.get(iid, {}).get("number"),
                "title": issues_meta.get(iid, {}).get("title"),
                "model_a_label": _label_val(a_unscored_map, iid),
                "model_b_label": _label_val(b_unscored_map, iid),
            }
            for iid in common_unscored
            if a_unscored_map.get(iid) and b_unscored_map.get(iid)
            and a_unscored_map[iid].label != b_unscored_map[iid].label
        ]

        # Save run
        manifest_a.model_b = request.model_b
        manifest_a.scored_issues = len(scored_items)

        run_data = {
            "run_id": run_id,
            "manifest": manifest_a.model_dump(mode="json"),
            "scored": {
                "model_a": metrics_a,
                "model_b": metrics_b,
                "comparison": comparison.model_dump(mode="json"),
            },
            "unscored": {
                "model_a_predictions": [c.model_dump() for c in a_unscored],
                "model_b_predictions": [c.model_dump() for c in b_unscored],
                "agreement_rate": unscored_agreement_rate,
                "kappa": comparison.cohens_kappa,
                "per_class_distributions": {
                    "model_a": class_dist(a_unscored),
                    "model_b": class_dist(b_unscored),
                },
                "disagreements": unscored_disagreements,
            },
            "operational": {
                "model_a": ops_a.model_dump(mode="json"),
                "model_b": ops_b.model_dump(mode="json"),
            },
        }

        run_path = RUNS_DIR / f"{run_id}.json"
        run_path.write_text(json.dumps(run_data, indent=2, default=str))
        return run_data

    async def event_generator() -> object:
        """Generate SSE events: progress during run, then final result."""
        # Start background task
        run_task = asyncio.create_task(run_both())

        len(issues) * 2  # Two models

        try:
            while not run_task.done():
                try:
                    event = await asyncio.wait_for(progress_queue.get(), timeout=0.2)
                    yield f"data: {json.dumps(event)}\n\n"
                except TimeoutError:
                    # Heartbeat to keep connection alive
                    yield ": heartbeat\n\n"

            # Drain remaining events
            while not progress_queue.empty():
                event = progress_queue.get_nowait()
                yield f"data: {json.dumps(event)}\n\n"

            # Get result
            run_data = run_task.result()
            total_cost_a = run_data["operational"]["model_a"]["total_cost_usd"]
            total_cost_b = run_data["operational"]["model_b"]["total_cost_usd"]

            yield f"data: {json.dumps({'type': 'done', 'run_id': run_id, 'total_cost': total_cost_a + total_cost_b})}\n\n"

        except Exception as exc:
            logger.exception(f"Run {run_id} failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering for SSE
        },
    )


import re as _re
_RUN_ID_RE = _re.compile(r"^[a-f0-9]{8}$")


def _validate_run_id(run_id: str) -> None:
    """Guard against path traversal: run_ids are 8 lowercase hex chars."""
    if not _RUN_ID_RE.match(run_id):
        raise HTTPException(status_code=400, detail="Invalid run_id format")


@app.get("/api/results/{run_id}")
async def get_results(run_id: str) -> dict:  # type: ignore[type-arg]
    _validate_run_id(run_id)
    run_path = RUNS_DIR / f"{run_id}.json"
    if not run_path.exists():
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return json.loads(run_path.read_text())


@app.get("/api/results/{run_id}/issue/{issue_id}/raw")
async def get_raw_output(run_id: str, issue_id: int) -> dict:  # type: ignore[type-arg]
    """Return raw model outputs for a specific issue in a run (for debugging).

    Searches both the unscored predictions list (stored inline) AND the per-model
    cache files on disk (for scored issues, which are not stored inline in run_data
    to keep the run JSON a manageable size).
    """
    _validate_run_id(run_id)
    run_path = RUNS_DIR / f"{run_id}.json"
    if not run_path.exists():
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    run_data = json.loads(run_path.read_text())
    manifest = run_data.get("manifest", {})
    model_a = manifest.get("model_a", "")
    model_b = manifest.get("model_b", "")

    # Find issue
    issues = _load_issues()
    issue = next((i for i in issues if i.id == issue_id), None)
    if not issue:
        raise HTTPException(status_code=404, detail=f"Issue {issue_id} not found in corpus")

    a_raw = None
    b_raw = None
    a_label = None
    b_label = None

    # Step 1: check inline unscored predictions
    for model_key, preds_key in [("model_a", "model_a_predictions"), ("model_b", "model_b_predictions")]:
        preds = run_data.get("unscored", {}).get(preds_key, [])
        for clf in preds:
            if clf.get("issue_id") == issue_id:
                if model_key == "model_a":
                    a_raw = clf.get("raw_response")
                    a_label = clf.get("label")
                else:
                    b_raw = clf.get("raw_response")
                    b_label = clf.get("label")

    # Step 2: fall back to cache files for scored issues (not stored inline)
    from src.inference.prompt import get_prompt_hash
    prompt_hash = get_prompt_hash()
    cache_dir = Path("data/cache")

    for model_slug, target_raw, target_label, which in [
        (model_a, a_raw, a_label, "a"),
        (model_b, b_raw, b_label, "b"),
    ]:
        if target_raw is not None:
            continue  # already found above
        slug_safe = model_slug.replace("/", "-").replace(":", "_")
        cache_file = cache_dir / f"{issue_id}__{slug_safe}__{prompt_hash}.json"
        if cache_file.exists():
            cached = json.loads(cache_file.read_text())
            if which == "a":
                a_raw = cached.get("raw_response")
                a_label = cached.get("label")
            else:
                b_raw = cached.get("raw_response")
                b_label = cached.get("label")

    return {
        "issue": issue.model_dump(),
        "model_a_raw": a_raw,
        "model_b_raw": b_raw,
        "model_a_label": a_label,
        "model_b_label": b_label,
    }


# ── Static frontend (must be last — catches everything not matched above) ──────
# Mounted conditionally so the API works without building the frontend
_frontend_dist = Path("frontend/dist")
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
