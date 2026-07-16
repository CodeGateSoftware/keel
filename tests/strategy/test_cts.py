"""Tests for halal_cb.strategy.indicators_cts: the CTS confluence scorer.

Additive scoring over named confluence factors (spec §9) -> a total score that
selects one of the 4 graded entry techniques (spec §9/§17.1).
"""

from __future__ import annotations

from halal_cb.strategy.indicators_cts import (
    DEFAULT_WEIGHTS,
    CTSFactor,
    CTSResult,
    entry_technique,
    score,
)


class TestScoreAdditive:
    def test_three_present_high_value_factors_sum_correctly(self) -> None:
        context = {
            "condition_aligned": True,
            "sr_touches": 3,
            "ema_fan_aligned": True,
        }
        result = score(context)
        expected = (
            DEFAULT_WEIGHTS["condition_aligned"]
            + DEFAULT_WEIGHTS["sr_touches"]
            + DEFAULT_WEIGHTS["ema_fan_aligned"]
        )
        assert isinstance(result, CTSResult)
        assert result.total == expected

    def test_absent_factors_add_zero(self) -> None:
        result = score({})
        assert result.total == 0
        assert all(f.points == 0 for f in result.factors)
        assert all(f.present is False for f in result.factors)

    def test_all_factors_present_sums_full_weight(self) -> None:
        context = {
            "condition_aligned": True,
            "in_pullback": True,
            "sr_touches": 5,
            "round_number_proximity": True,
            "deceleration": True,
            "ema_fan_aligned": True,
            "rsi_extreme": True,
            "rsi_divergence": True,
            "candlestick_pattern": "hammer",
            "fib_confluence": True,
            "seasonality": True,
        }
        result = score(context)
        assert result.total == sum(DEFAULT_WEIGHTS.values())

    def test_result_includes_a_factor_entry_for_every_weighted_factor(self) -> None:
        result = score({})
        names = {f.name for f in result.factors}
        assert names == set(DEFAULT_WEIGHTS.keys())
        assert all(isinstance(f, CTSFactor) for f in result.factors)

    def test_sr_touches_requires_at_least_three(self) -> None:
        below = score({"sr_touches": 2})
        at = score({"sr_touches": 3})
        above = score({"sr_touches": 4})
        assert below.total == 0
        assert at.total == DEFAULT_WEIGHTS["sr_touches"]
        assert above.total == DEFAULT_WEIGHTS["sr_touches"]

    def test_candlestick_pattern_present_only_when_truthy(self) -> None:
        none_pattern = score({"candlestick_pattern": None})
        empty_pattern = score({"candlestick_pattern": ""})
        real_pattern = score({"candlestick_pattern": "tweezer_bottom"})
        assert none_pattern.total == 0
        assert empty_pattern.total == 0
        assert real_pattern.total == DEFAULT_WEIGHTS["candlestick_pattern"]

    def test_factor_detail_is_a_nonempty_string(self) -> None:
        result = score({"condition_aligned": True})
        for factor in result.factors:
            assert isinstance(factor.detail, str)
            assert factor.detail

    def test_seasonality_off_by_default_in_v1(self) -> None:
        assert DEFAULT_WEIGHTS["seasonality"] == 0
        result = score({"seasonality": True})
        assert result.total == 0


class TestScoreWeightsConfigurable:
    def test_custom_weights_override_defaults(self) -> None:
        weights = {"condition_aligned": 10}
        result = score({"condition_aligned": True}, weights=weights)
        assert result.total == 10
        assert len(result.factors) == 1
        assert result.factors[0].name == "condition_aligned"
        assert result.factors[0].points == 10

    def test_custom_weights_absent_factor_still_zero(self) -> None:
        weights = {"rsi_divergence": 6}
        result = score({}, weights=weights)
        assert result.total == 0
        assert result.factors[0].present is False

    def test_unknown_weight_key_scores_absent_by_default(self) -> None:
        weights = {"made_up_factor": 3}
        result = score({"made_up_factor": True}, weights=weights)
        # No presence-check is registered for an unrecognized factor name, so it
        # never contributes points even if the caller stuffs a truthy context value.
        assert result.total == 0


class TestEntryTechnique:
    def test_low_score_is_confirm_3bar(self) -> None:
        assert entry_technique(0) == "confirm_3bar"
        assert entry_technique(4) == "confirm_3bar"

    def test_boundary_at_low_is_signal_candle(self) -> None:
        assert entry_technique(5) == "signal_candle"

    def test_mid_score_is_signal_candle(self) -> None:
        assert entry_technique(7) == "signal_candle"

    def test_boundary_at_high_is_aggressive(self) -> None:
        assert entry_technique(8) == "aggressive"

    def test_high_score_is_aggressive(self) -> None:
        assert entry_technique(20) == "aggressive"

    def test_custom_boundaries(self) -> None:
        assert entry_technique(2, low=3, high=6) == "confirm_3bar"
        assert entry_technique(3, low=3, high=6) == "signal_candle"
        assert entry_technique(6, low=3, high=6) == "aggressive"
