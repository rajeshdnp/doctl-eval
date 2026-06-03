"""Tests for the scoring metrics module."""

import pytest

from src.models import Classification, LabelEnum
from src.scoring.metrics import (
    _run_mcnemar,
    compute_model_metrics,
    compute_operational_metrics,
)


def make_clf(
    issue_id: int,
    model: str,
    label: LabelEnum | None,
    cost_usd: float = 0.001,
    latency_ms: float = 100.0,
    from_cache: bool = False,
) -> Classification:
    return Classification(
        issue_id=issue_id,
        model=model,
        prompt_version="v1",
        label=label,
        reasoning="test",
        raw_response='{"label":"bug","reasoning":"test"}',
        prompt_tokens=100,
        completion_tokens=20,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        attempt_count=1,
        error_type=None,
        from_cache=from_cache,
    )


class TestAccuracyCI:
    def test_wilson_ci_90_of_100(self) -> None:
        """90/100 correct → Wilson CI should be within [0.82, 0.96]."""
        # Create 100 classifications: 90 correct (bug), 10 wrong (enhancement)
        classifications = (
            [make_clf(i, "model", LabelEnum.bug) for i in range(90)]
            + [make_clf(i + 90, "model", LabelEnum.enhancement) for i in range(10)]
        )
        ground_truth = [(i, LabelEnum.bug) for i in range(100)]

        metrics = compute_model_metrics(classifications, ground_truth, "model")

        assert metrics["overall_accuracy"] == pytest.approx(0.9, abs=0.001)
        ci = metrics["accuracy_ci"]
        assert ci["lower"] > 0.82
        assert ci["upper"] < 0.96
        assert ci["method"] == "wilson"

    def test_perfect_accuracy(self) -> None:
        """100/100 correct → accuracy = 1.0, CI upper ≤ 1.0."""
        classifications = [make_clf(i, "model", LabelEnum.bug) for i in range(50)]
        ground_truth = [(i, LabelEnum.bug) for i in range(50)]
        metrics = compute_model_metrics(classifications, ground_truth, "model")
        assert metrics["overall_accuracy"] == pytest.approx(1.0)
        assert metrics["accuracy_ci"]["upper"] <= 1.0


class TestCostPerCorrect:
    def test_cost_per_correct(self) -> None:
        """10 correct at $0.001 each = $0.001 cost/correct."""
        classifications = [
            make_clf(i, "model", LabelEnum.bug, cost_usd=0.001) for i in range(10)
        ]
        ground_truth = [(i, LabelEnum.bug) for i in range(10)]
        metrics = compute_model_metrics(classifications, ground_truth, "model")
        assert metrics["n_correct"] == 10
        assert metrics["cost_per_correct_classification"] == pytest.approx(0.001, rel=1e-3)

    def test_zero_correct_returns_inf(self) -> None:
        """No correct predictions → cost/correct = infinity."""
        classifications = [
            make_clf(i, "model", LabelEnum.enhancement, cost_usd=0.001) for i in range(5)
        ]
        ground_truth = [(i, LabelEnum.bug) for i in range(5)]
        metrics = compute_model_metrics(classifications, ground_truth, "model")
        assert metrics["cost_per_correct_classification"] == float("inf")


class TestMcNemar:
    def test_mcnemar_significant(self) -> None:
        """
        Table [[80, 5], [20, 10]] should give p < 0.05.
        b=5 (A right, B wrong), c=20 (A wrong, B right) → B is better
        Expected: chi2 > 6.6 for p < 0.01
        """
        a_map = {}
        b_map = {}
        gt_map = {}

        # both right: issues 0-79
        for i in range(80):
            a_map[i] = make_clf(i, "A", LabelEnum.bug)
            b_map[i] = make_clf(i, "B", LabelEnum.bug)
            gt_map[i] = LabelEnum.bug

        # A right, B wrong: issues 80-84
        for i in range(80, 85):
            a_map[i] = make_clf(i, "A", LabelEnum.bug)
            b_map[i] = make_clf(i, "B", LabelEnum.enhancement)
            gt_map[i] = LabelEnum.bug

        # A wrong, B right: issues 85-104
        for i in range(85, 105):
            a_map[i] = make_clf(i, "A", LabelEnum.enhancement)
            b_map[i] = make_clf(i, "B", LabelEnum.bug)
            gt_map[i] = LabelEnum.bug

        # both wrong: issues 105-114
        for i in range(105, 115):
            a_map[i] = make_clf(i, "A", LabelEnum.enhancement)
            b_map[i] = make_clf(i, "B", LabelEnum.question)
            gt_map[i] = LabelEnum.bug

        result = _run_mcnemar(a_map, b_map, gt_map)
        assert result.is_significant is True
        assert result.p_value < 0.05

    def test_mcnemar_not_significant(self) -> None:
        """Symmetric disagreements → p > 0.05."""
        a_map = {}
        b_map = {}
        gt_map = {}

        # Both right: 80
        for i in range(80):
            a_map[i] = make_clf(i, "A", LabelEnum.bug)
            b_map[i] = make_clf(i, "B", LabelEnum.bug)
            gt_map[i] = LabelEnum.bug

        # A right, B wrong: 5
        for i in range(80, 85):
            a_map[i] = make_clf(i, "A", LabelEnum.bug)
            b_map[i] = make_clf(i, "B", LabelEnum.enhancement)
            gt_map[i] = LabelEnum.bug

        # A wrong, B right: 5 (symmetric)
        for i in range(85, 90):
            a_map[i] = make_clf(i, "A", LabelEnum.enhancement)
            b_map[i] = make_clf(i, "B", LabelEnum.bug)
            gt_map[i] = LabelEnum.bug

        result = _run_mcnemar(a_map, b_map, gt_map)
        assert result.is_significant is False
        assert result.p_value > 0.05


class TestConfusionMatrix:
    def test_confusion_matrix_shape(self) -> None:
        """6-class inputs produce a 6×6 confusion matrix."""
        LABELS = ["bug", "enhancement", "question", "documentation", "security", "other"]
        classifications = [make_clf(i, "model", LabelEnum(LABELS[i % 6])) for i in range(30)]
        ground_truth = [(i, LabelEnum(LABELS[i % 6])) for i in range(30)]
        metrics = compute_model_metrics(classifications, ground_truth, "model")

        cm = metrics["confusion_matrix_raw"]
        assert len(cm) == 6
        for row in cm.values():
            assert len(row) == 6


class TestAgreementRate:
    def test_identical_predictions_perfect_agreement(self) -> None:
        """Two models with identical predictions → agreement 1.0."""
        from src.scoring.metrics import compare_models

        classifications = [make_clf(i, "A", LabelEnum.bug) for i in range(20)]
        classifications_b = [make_clf(i, "B", LabelEnum.bug) for i in range(20)]
        gt = [(i, LabelEnum.bug) for i in range(20)]

        comparison = compare_models(classifications, classifications_b, gt)
        assert comparison.agreement_rate == pytest.approx(1.0)
        assert comparison.cohens_kappa == pytest.approx(1.0)


class TestOperationalMetrics:
    def test_cached_excluded_from_latency(self) -> None:
        """Cached calls have near-zero latency — exclude from p50/p95."""
        non_cached = [make_clf(i, "m", LabelEnum.bug, latency_ms=150.0) for i in range(10)]
        cached = [make_clf(i + 10, "m", LabelEnum.bug, latency_ms=0.1, from_cache=True) for i in range(10)]
        all_clfs = non_cached + cached

        ops = compute_operational_metrics(all_clfs, 10.0, 10, n_correct=10)
        # p50 should be ~150ms (from non-cached only), not ~75ms (average of both)
        assert ops.p50_latency_ms > 100.0
        assert ops.cache_hit_rate == pytest.approx(0.5)

    def test_error_rate(self) -> None:
        success = [make_clf(i, "m", LabelEnum.bug) for i in range(8)]
        errors = [make_clf(i + 8, "m", None) for i in range(2)]
        all_clfs = success + errors

        ops = compute_operational_metrics(all_clfs, 5.0, 10, n_correct=8)
        assert ops.error_rate == pytest.approx(0.2)
