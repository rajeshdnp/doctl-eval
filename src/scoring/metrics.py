"""
Stage 7: Scoring module — statistical metrics for model evaluation.

Key choices explained (you will be asked about these):

Wilson CI for accuracy: Wald intervals can have coverage < nominal at small n.
  Wilson is preferred for n < ~500 (our scored set is typically 100-400 issues).
  From statsmodels.stats.proportion.proportion_confint.

F1 CIs via bootstrap: No closed-form for F1. We use n=1000 bootstrap resamples
  with seed=42 for reproducibility.

McNemar's test: Standard choice when two classifiers share the same test set.
  Tests whether disagreements are symmetric (i.e., one model is actually better).
  Reference: Dietterich 1998, Neural Computation 10(7):1895-1923.
  We use the continuity-corrected version (correction=True) for small cell counts.

Cohen's kappa: Inter-rater agreement corrected for chance. Reported alongside raw
  agreement rate (not as a standalone verdict) because kappa can behave anomalously
  under skewed class distributions.

cost_per_correct_classification: The business metric. A cheap model with bad
  accuracy can be MORE expensive per correct answer. Show this prominently.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import numpy as np
from sklearn.metrics import (
    classification_report,
    cohen_kappa_score,
    confusion_matrix,
)
from sklearn.utils import resample
from statsmodels.stats.contingency_tables import mcnemar as mcnemar_test
from statsmodels.stats.proportion import proportion_confint

from src.models import (
    AccuracyCI,
    Classification,
    LabelEnum,
    McNemar,
    ModelComparison,
    OperationalMetrics,
    PerClassMetrics,
)

logger = logging.getLogger(__name__)

LOW_SUPPORT_THRESHOLD = 20
BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 42
ALL_LABELS = [e.value for e in LabelEnum]


def compute_model_metrics(
    classifications: list[Classification],
    ground_truth: list[tuple[int, LabelEnum]],
    model_name: str,
) -> dict[str, Any]:
    """
    Compute all scoring metrics for one model against ground truth.

    Only issues present in both classifications and ground_truth are scored.
    Issues where label=None (errors) are counted toward error_rate but excluded
    from accuracy/F1 calculations (they'd be wrong regardless).
    """
    # Build GT lookup: issue_id → true_label
    gt_map = {issue_id: label for issue_id, label in ground_truth}

    # Match classifications to GT — only scored issues
    y_true: list[str] = []
    y_pred: list[str] = []
    matched_costs: list[float] = []

    for clf in classifications:
        if clf.issue_id in gt_map and clf.label is not None:
            y_true.append(gt_map[clf.issue_id].value)
            y_pred.append(clf.label.value)
            matched_costs.append(clf.cost_usd)

    n_total = len(y_true)
    if n_total == 0:
        logger.warning(f"No scored issues found for model {model_name}")
        return _empty_metrics(model_name)

    n_correct = sum(1 for t, p in zip(y_true, y_pred, strict=False) if t == p)
    accuracy = n_correct / n_total

    # Wilson score CI for accuracy (preferred over Wald for small n)
    ci_low, ci_high = proportion_confint(
        count=n_correct,
        nobs=n_total,
        alpha=0.05,
        method="wilson",
    )
    accuracy_ci = AccuracyCI(lower=float(ci_low), upper=float(ci_high), method="wilson")

    # Per-class metrics from sklearn
    report = classification_report(
        y_true,
        y_pred,
        labels=ALL_LABELS,
        output_dict=True,
        zero_division=0,
    )

    # Bootstrap F1 CIs per class
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    per_class: dict[str, PerClassMetrics] = {}

    for label in ALL_LABELS:
        cls_stats = report.get(label, {})
        f1_base = float(cls_stats.get("f1-score", 0.0))
        support = int(cls_stats.get("support", 0))

        # Bootstrap F1 CI
        boot_f1s: list[float] = []
        if support > 0:
            indices = np.arange(n_total)
            for _ in range(BOOTSTRAP_N):
                boot_idx = resample(indices, random_state=int(rng.integers(0, 2**31)))
                boot_true = [y_true[i] for i in boot_idx]
                boot_pred = [y_pred[i] for i in boot_idx]
                boot_report = classification_report(
                    boot_true,
                    boot_pred,
                    labels=ALL_LABELS,
                    output_dict=True,
                    zero_division=0,
                )
                boot_f1s.append(float(boot_report.get(label, {}).get("f1-score", 0.0)))

        f1_ci_lower = float(np.percentile(boot_f1s, 2.5)) if boot_f1s else 0.0
        f1_ci_upper = float(np.percentile(boot_f1s, 97.5)) if boot_f1s else 0.0

        per_class[label] = PerClassMetrics(
            precision=float(cls_stats.get("precision", 0.0)),
            recall=float(cls_stats.get("recall", 0.0)),
            f1=f1_base,
            support=support,
            f1_ci_lower=f1_ci_lower,
            f1_ci_upper=f1_ci_upper,
        )

    macro_f1 = float(report.get("macro avg", {}).get("f1-score", 0.0))
    micro_f1 = float(report.get("micro avg", {}).get("f1-score", 0.0))
    weighted_f1 = float(report.get("weighted avg", {}).get("f1-score", 0.0))

    # Confusion matrix
    cm = confusion_matrix(y_true, y_pred, labels=ALL_LABELS)
    cm_raw: dict[str, dict[str, int]] = {}
    cm_norm: dict[str, dict[str, float]] = {}

    for i, true_label in enumerate(ALL_LABELS):
        row_sum = cm[i].sum()
        cm_raw[true_label] = {
            pred_label: int(cm[i][j]) for j, pred_label in enumerate(ALL_LABELS)
        }
        cm_norm[true_label] = {
            pred_label: float(cm[i][j] / row_sum) if row_sum > 0 else 0.0
            for j, pred_label in enumerate(ALL_LABELS)
        }

    # Cost per correct classification (KEY business metric — see README)
    total_cost = sum(matched_costs)
    cost_per_correct = total_cost / n_correct if n_correct > 0 else float("inf")

    # Security class warning
    security_support = per_class.get("security", PerClassMetrics(
        precision=0, recall=0, f1=0, support=0
    )).support
    security_warning = None
    if security_support < LOW_SUPPORT_THRESHOLD:
        security_warning = (
            f"Only {security_support} security examples in scored set. "
            "Per-class F1 unreliable. "
            "Recommend routing all predicted-security to human review."
        )

    return {
        "model": model_name,
        "n_scored": n_total,
        "n_correct": n_correct,
        "overall_accuracy": accuracy,
        "accuracy_ci": accuracy_ci.model_dump(),
        "per_class": {k: v.model_dump() for k, v in per_class.items()},
        "macro_f1": macro_f1,
        "micro_f1": micro_f1,
        "weighted_f1": weighted_f1,
        "confusion_matrix_raw": cm_raw,
        "confusion_matrix_normalized": cm_norm,
        "total_cost_usd": total_cost,
        "cost_per_correct_classification": cost_per_correct,
        "security_warning": security_warning,
    }


def compare_models(
    results_a: list[Classification],
    results_b: list[Classification],
    ground_truth: list[tuple[int, LabelEnum]],
    issues_meta: dict[int, dict[str, Any]] | None = None,
) -> ModelComparison:
    """
    Head-to-head statistical comparison between two models.

    McNemar's test: Tests whether one model is significantly better on the same
    test set. The null hypothesis is that both models have the same error rate.
    We use the continuity-corrected version (Yates correction) for small cell counts.
    """
    gt_map = {issue_id: label for issue_id, label in ground_truth}

    # Build paired predictions — only issues both models successfully classified
    a_map = {c.issue_id: c for c in results_a if c.label is not None}
    b_map = {c.issue_id: c for c in results_b if c.label is not None}

    common_ids = sorted(set(a_map.keys()) & set(b_map.keys()))

    a_preds = [a_map[iid].label.value for iid in common_ids]
    b_preds = [b_map[iid].label.value for iid in common_ids]

    # Agreement on unscored issues (both models, all issues)
    agreement_count = sum(1 for a, b in zip(a_preds, b_preds, strict=False) if a == b)
    agreement_rate = agreement_count / len(common_ids) if common_ids else 0.0

    # Cohen's kappa: inter-model agreement corrected for chance
    # Note: can behave anomalously under skewed class distributions —
    # report alongside raw agreement_rate, not standalone.
    # Guard: returns NaN when both predictions contain only one unique label —
    # in that case perfect agreement is trivial (100% predicted same class), so
    # we report 1.0 if agreement is perfect, else 0.0.
    import math
    try:
        raw_kappa = cohen_kappa_score(a_preds, b_preds)
        kappa = 0.0 if math.isnan(raw_kappa) else float(raw_kappa)
        if math.isnan(raw_kappa) and agreement_rate == 1.0:
            kappa = 1.0  # trivial perfect agreement on single class
    except Exception:
        kappa = 0.0

    # McNemar's test — requires GT labels
    mcnemar_result = _run_mcnemar(a_map, b_map, gt_map)

    # Disagreements list — sorted: GT-confirmed disagreements first
    disagreements = _build_disagreements(
        a_map, b_map, gt_map, common_ids, issues_meta or {}
    )

    return ModelComparison(
        agreement_rate=agreement_rate,
        cohens_kappa=kappa,
        mcnemar=mcnemar_result,
        disagreements=disagreements,
    )


def _run_mcnemar(
    a_map: dict[int, Classification],
    b_map: dict[int, Classification],
    gt_map: dict[int, LabelEnum],
) -> McNemar:
    """Build McNemar contingency table and run the test."""
    scored_ids = sorted(set(a_map.keys()) & set(b_map.keys()) & set(gt_map.keys()))

    if len(scored_ids) < 4:
        return McNemar(
            contingency_table=[[0, 0], [0, 0]],
            chi2=0.0,
            p_value=1.0,
            is_significant=False,
            verdict="Insufficient data for McNemar's test (need at least 4 scored paired items)",
        )

    # 2x2 contingency table:
    # [[both_right, a_right_b_wrong], [a_wrong_b_right, both_wrong]]
    both_right = 0
    a_right_b_wrong = 0  # b = A correct, B wrong
    a_wrong_b_right = 0  # c = A wrong, B correct
    both_wrong = 0

    for iid in scored_ids:
        true_label = gt_map[iid].value
        a_correct = a_map[iid].label is not None and a_map[iid].label.value == true_label
        b_correct = b_map[iid].label is not None and b_map[iid].label.value == true_label

        if a_correct and b_correct:
            both_right += 1
        elif a_correct and not b_correct:
            a_right_b_wrong += 1
        elif not a_correct and b_correct:
            a_wrong_b_right += 1
        else:
            both_wrong += 1

    table = [[both_right, a_right_b_wrong], [a_wrong_b_right, both_wrong]]

    try:
        result = mcnemar_test(table, exact=False, correction=True)
        chi2 = float(result.statistic)
        p_value = float(result.pvalue)
    except Exception as exc:
        logger.warning(f"McNemar's test failed: {exc}")
        chi2 = 0.0
        p_value = 1.0

    is_significant = p_value < 0.05

    if is_significant:
        # Determine which model is better based on the contingency table
        if a_right_b_wrong > a_wrong_b_right:
            verdict = f"Model A is significantly more accurate (p={p_value:.3f})"
        else:
            verdict = f"Model B is significantly more accurate (p={p_value:.3f})"
    else:
        verdict = f"No statistically significant difference (p={p_value:.3f})"

    return McNemar(
        contingency_table=table,
        chi2=chi2,
        p_value=p_value,
        is_significant=is_significant,
        verdict=verdict,
    )


def _build_disagreements(
    a_map: dict[int, Classification],
    b_map: dict[int, Classification],
    gt_map: dict[int, LabelEnum],
    common_ids: list[int],
    issues_meta: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build sorted disagreement list — GT-confirmed disagreements first."""
    disagreements = []

    for iid in common_ids:
        a_clf = a_map[iid]
        b_clf = b_map[iid]

        if a_clf.label == b_clf.label:
            continue  # Agreement — skip

        meta = issues_meta.get(iid, {})
        gt_label = gt_map.get(iid)

        disagreements.append({
            "issue_id": iid,
            "issue_number": meta.get("number"),
            "title": meta.get("title"),
            "model_a_label": a_clf.label.value if a_clf.label else None,
            "model_b_label": b_clf.label.value if b_clf.label else None,
            "ground_truth_label": gt_label.value if gt_label else None,
            "model_a_reasoning": a_clf.reasoning,
            "model_b_reasoning": b_clf.reasoning,
            # GT-confirmed disagreements first — most actionable for review
            "_has_gt": gt_label is not None,
        })

    # Sort: GT-confirmed disagreements first, then by issue_id
    disagreements.sort(key=lambda d: (not d["_has_gt"], d.get("issue_id", 0)))

    # Remove sorting helper key
    for d in disagreements:
        d.pop("_has_gt", None)

    return disagreements


def compute_operational_metrics(
    classifications: list[Classification],
    wall_clock_seconds: float,
    concurrency: int,
    n_correct: int,
) -> OperationalMetrics:
    """
    Compute runtime performance metrics.

    Cached calls are excluded from latency calculations (they have artificial
    near-zero latency that would skew the distribution). We still count them
    in throughput since they represent completed work from the user's perspective.
    The cache_hit_rate shows how much of the run was free.
    """
    total = len(classifications)
    successful = [c for c in classifications if c.label is not None]
    non_cached = [c for c in successful if not c.from_cache]
    cached = [c for c in classifications if c.from_cache]
    errors = [c for c in classifications if c.label is None]

    # Latency from non-cached calls only
    latencies = [c.latency_ms for c in non_cached]
    p50_ms = float(np.percentile(latencies, 50)) if latencies else 0.0
    p95_ms = float(np.percentile(latencies, 95)) if latencies else 0.0

    throughput = len(successful) / wall_clock_seconds if wall_clock_seconds > 0 else 0.0
    total_cost = sum(c.cost_usd for c in classifications)
    avg_cost = total_cost / total if total > 0 else 0.0

    # Error breakdown by type
    error_breakdown: dict[str, int] = defaultdict(int)
    for c in errors:
        if c.error_type:
            error_breakdown[c.error_type] += 1

    error_rate = len(errors) / total if total > 0 else 0.0
    cache_hit_rate = len(cached) / total if total > 0 else 0.0
    cost_per_correct = total_cost / n_correct if n_correct > 0 else float("inf")

    return OperationalMetrics(
        p50_latency_ms=p50_ms,
        p95_latency_ms=p95_ms,
        wall_clock_seconds=wall_clock_seconds,
        throughput_rps=throughput,
        total_cost_usd=total_cost,
        avg_cost_per_call_usd=avg_cost,
        concurrency=concurrency,
        error_breakdown=dict(error_breakdown),
        error_rate=error_rate,
        cache_hit_rate=cache_hit_rate,
        cost_per_correct_classification=cost_per_correct,
    )


def _empty_metrics(model_name: str) -> dict[str, Any]:
    """Return zero-value metrics dict when no scored issues are available."""
    return {
        "model": model_name,
        "n_scored": 0,
        "n_correct": 0,
        "overall_accuracy": 0.0,
        "accuracy_ci": {"lower": 0.0, "upper": 0.0, "method": "wilson"},
        "per_class": {
            label: {"precision": 0.0, "recall": 0.0, "f1": 0.0, "support": 0,
                    "f1_ci_lower": 0.0, "f1_ci_upper": 0.0}
            for label in ALL_LABELS
        },
        "macro_f1": 0.0,
        "micro_f1": 0.0,
        "weighted_f1": 0.0,
        "confusion_matrix_raw": {l: {p: 0 for p in ALL_LABELS} for l in ALL_LABELS},
        "confusion_matrix_normalized": {l: {p: 0.0 for p in ALL_LABELS} for l in ALL_LABELS},
        "total_cost_usd": 0.0,
        "cost_per_correct_classification": float("inf"),
        "security_warning": None,
    }
