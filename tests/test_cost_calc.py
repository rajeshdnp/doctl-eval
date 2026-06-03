"""
Tests for cost calculation logic.

Ensures cost_usd = (prompt_tokens × input_rate + completion_tokens × output_rate) / 1_000_000
is correctly implemented and traceable to token counts.
"""

import pytest


def compute_cost(
    prompt_tokens: int,
    completion_tokens: int,
    input_rate: float,
    output_rate: float,
) -> float:
    """Mirror of the cost formula used in client.py."""
    return (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000


class TestSymmetricPricing:
    """llama3.3-70b-instruct: input == output == $0.65/1M"""

    SLUG = "llama3.3-70b-instruct"
    INPUT = 0.65
    OUTPUT = 0.65

    def test_symmetric_cost(self) -> None:
        cost = compute_cost(500, 50, self.INPUT, self.OUTPUT)
        expected = (500 * 0.65 + 50 * 0.65) / 1_000_000
        assert cost == pytest.approx(expected, rel=1e-9)
        # (500 * 0.65 + 50 * 0.65) / 1_000_000 = 357.5 / 1_000_000 = 0.0003575
        assert cost == pytest.approx(0.0003575, rel=1e-6)

    def test_symmetric_only_prompt_tokens(self) -> None:
        cost = compute_cost(1000, 0, self.INPUT, self.OUTPUT)
        assert cost == pytest.approx(0.65 / 1_000, rel=1e-9)


class TestAsymmetricPricing:
    """anthropic-claude-haiku-4.5: input=$1.00/1M, output=$5.00/1M"""

    SLUG = "anthropic-claude-haiku-4.5"
    INPUT = 1.00
    OUTPUT = 5.00

    def test_asymmetric_cost(self) -> None:
        cost = compute_cost(500, 50, self.INPUT, self.OUTPUT)
        expected = (500 * 1.00 + 50 * 5.00) / 1_000_000
        assert cost == pytest.approx(expected, rel=1e-9)
        assert cost == pytest.approx(0.00075, rel=1e-3)

    def test_output_rate_higher(self) -> None:
        """50 output tokens at $5/1M costs more than 50 input tokens at $1/1M."""
        cost_output_dominated = compute_cost(0, 1000, self.INPUT, self.OUTPUT)
        cost_input_dominated = compute_cost(1000, 0, self.INPUT, self.OUTPUT)
        assert cost_output_dominated > cost_input_dominated


class TestEdgeCases:
    def test_zero_tokens(self) -> None:
        """Zero tokens means zero cost — used for error classification results."""
        cost = compute_cost(0, 0, 1.00, 5.00)
        assert cost == 0.0

    def test_large_volume_cost(self) -> None:
        """Sanity check: 1M prompt + 100K completion tokens at frontier pricing."""
        cost = compute_cost(1_000_000, 100_000, 1.00, 5.00)
        assert cost == pytest.approx(1.50, rel=1e-9)  # $1 input + $0.50 output

    def test_budget_model_very_cheap(self) -> None:
        """openai-gpt-oss-20b is the cheapest — verify it's cheaper than haiku."""
        budget = compute_cost(500, 50, 0.05, 0.45)
        haiku = compute_cost(500, 50, 1.00, 5.00)
        assert budget < haiku
        # Budget cost: (500*0.05 + 50*0.45)/1M = (25+22.5)/1M = 0.0000475
        assert budget == pytest.approx(0.0000475, rel=1e-3)
