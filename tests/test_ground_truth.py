"""Tests for ground truth label mapping logic."""

import pytest

from src.ground_truth.builder import map_label
from src.models import LabelEnum


def test_bug_label() -> None:
    label, conf = map_label(["bug"])
    assert label == LabelEnum.bug
    assert conf == 1.0


def test_suggestion_maps_to_enhancement() -> None:
    label, conf = map_label(["suggestion"])
    assert label == LabelEnum.enhancement
    assert conf == 1.0


def test_enhancement_maps_to_enhancement() -> None:
    label, conf = map_label(["enhancement"])
    assert label == LabelEnum.enhancement
    assert conf == 1.0


def test_question_label() -> None:
    label, conf = map_label(["question"])
    assert label == LabelEnum.question
    assert conf == 1.0


def test_documentation_label() -> None:
    label, conf = map_label(["documentation"])
    assert label == LabelEnum.documentation
    assert conf == 1.0


def test_docs_alias() -> None:
    label, conf = map_label(["docs"])
    assert label == LabelEnum.documentation
    assert conf == 1.0


def test_security_label() -> None:
    label, conf = map_label(["security"])
    assert label == LabelEnum.security
    assert conf == 1.0


def test_security_in_composite() -> None:
    """Any label containing 'security' maps to security."""
    label, conf = map_label(["security-vulnerability"])
    assert label == LabelEnum.security


def test_empty_labels_excluded() -> None:
    label, conf = map_label([])
    assert label is None
    assert conf == 0.0


def test_meta_only_excluded() -> None:
    """Pure meta/routing labels with no content signal are excluded."""
    label, conf = map_label(["do-api"])
    assert label is None
    assert conf == 0.0


def test_api_parity_excluded() -> None:
    label, conf = map_label(["api-parity"])
    assert label is None
    assert conf == 0.0


def test_conflicting_labels() -> None:
    """Bug + suggestion are conflicting — ambiguous, exclude."""
    label, conf = map_label(["bug", "suggestion"])
    assert label is None
    assert conf == 0.0


def test_conflicting_bug_enhancement() -> None:
    label, conf = map_label(["bug", "enhancement"])
    assert label is None
    assert conf == 0.0


def test_app_platform_bug() -> None:
    """Bug qualified by meta label — retain bug at reduced confidence."""
    label, conf = map_label(["app-platform", "bug"])
    assert label == LabelEnum.bug
    assert conf == pytest.approx(0.7)


def test_kubernetes_bug() -> None:
    label, conf = map_label(["kubernetes", "bug"])
    assert label == LabelEnum.bug
    assert conf == pytest.approx(0.7)


def test_process_labels_excluded() -> None:
    """Process labels like good-first-issue have no classification signal."""
    label, conf = map_label(["good-first-issue"])
    assert label is None
    assert conf == 0.0


def test_hacktoberfest_excluded() -> None:
    label, conf = map_label(["hacktoberfest"])
    assert label is None
    assert conf == 0.0
