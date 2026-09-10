import math

import pytest

import calibration
from calibration import (
    Calibration, correlation, count_pmf, error_sd, normal_ppf, outcome_cdf, shrink_points, swap_confidence, td_probability,
)
from conftest import load_fixture


@pytest.fixture
def cal():
    return Calibration(load_fixture("calibration_sample.json"))


def test_shrink_points_and_error_sd_use_the_position_fit(cal):
    assert shrink_points(cal, "QB", 20.0) == pytest.approx(-1.7 + 0.99 * 20.0)
    assert shrink_points(cal, "RB", 12.0) == pytest.approx(12.0)  # identity when the position has no fit
    assert error_sd(cal, "WR", 15.0) == pytest.approx(3.57 + 0.299 * 15.0)
    assert error_sd(cal, "DEF", 8.0) == pytest.approx(cal.data["points"]["default"]["c0"] + cal.data["points"]["default"]["c1"] * 8.0)


def test_swap_confidence_is_a_normal_probability_of_the_gap():
    assert swap_confidence(0.0, 6.0, 6.0) == pytest.approx(0.5)
    assert swap_confidence(2.5, 7.0, 7.0) == pytest.approx(0.60, abs=0.01)  # the audit's 60% at a 2.5-point gap
    assert swap_confidence(-2.5, 7.0, 7.0) == pytest.approx(0.40, abs=0.01)
    assert 0.995 < swap_confidence(40.0, 5.0, 5.0) <= 1.0


def test_outcome_cdf_interpolates_the_band_table_and_is_flat_beyond_its_ends(cal):
    # WR rec_yd, middle band (projection 45-70): the fixture table has median 1.0 at p=0.5
    assert outcome_cdf(cal, "WR", "rec_yd", 55.0, 1.0) == pytest.approx(0.5)
    assert outcome_cdf(cal, "WR", "rec_yd", 55.0, 0.0) == pytest.approx(0.05)  # zero games are 5% of the band
    assert outcome_cdf(cal, "WR", "rec_yd", 55.0, 0.75) == pytest.approx(0.30, abs=0.02)
    assert outcome_cdf(cal, "WR", "rec_yd", 55.0, 9.0) == 1.0  # beyond the top of the table
    assert outcome_cdf(cal, "WR", "rec_yd", 55.0, -1.0) == 0.0
    # a projection below the stat's floor has no table
    assert outcome_cdf(cal, "WR", "rec_yd", 10.0, 1.0) is None
    assert cal.k50("WR", "rec_yd", 55.0) == pytest.approx(0.88)


def test_outcome_cdf_picks_the_band_by_projection(cal):
    low = outcome_cdf(cal, "WR", "rec_yd", 30.0, 1.5)   # low band is wider: more mass above 1.5x
    high = outcome_cdf(cal, "WR", "rec_yd", 90.0, 1.5)
    assert low < high


def test_count_pmf_is_poisson_when_dispersion_is_near_one_and_sums_to_one(cal):
    pmf = count_pmf(cal, "WR", "rec", 4.0)
    assert sum(pmf.values()) == pytest.approx(1.0, abs=1e-6)
    assert pmf[4] == pytest.approx(math.exp(-4.0) * 4.0 ** 4 / 24, abs=1e-6)
    over_dispersed = count_pmf(cal, "RB", "rec", 3.0)  # dispersion 1.6: heavier tail than Poisson
    assert over_dispersed[7] > math.exp(-3.0) * 3.0 ** 7 / 5040
    assert sum(over_dispersed.values()) == pytest.approx(1.0, abs=1e-6)


def test_td_probability_is_poisson_with_the_fitted_factor(cal):
    assert td_probability(cal, 0.5) == pytest.approx(1 - math.exp(-cal.data["touchdowns"]["k"] * 0.5))
    assert td_probability(cal, 0.0) == 0.0


def test_normal_ppf_round_trips_with_the_normal_cdf():
    for p in (0.01, 0.2, 0.5, 0.8, 0.975):
        z = normal_ppf(p)
        assert calibration.normal_cdf(z) == pytest.approx(p, abs=1e-6)
    assert normal_ppf(0.5) == pytest.approx(0.0, abs=1e-9)


def test_correlation_lookup_carries_sign_and_defaults(cal):
    assert correlation(cal, "player_pass_yds", "player_reception_yds", "same_team_qb_wr1") == pytest.approx(0.48)
    assert correlation(cal, "player_reception_yds", "player_pass_yds", "same_team_qb_wr1") == pytest.approx(0.48)  # symmetric
    assert correlation(cal, "player_pass_yds", "player_rush_yds", "same_team") == pytest.approx(-0.09)
    assert correlation(cal, "player_pass_yds", "player_pass_yds", "opponent") == pytest.approx(0.12)
    assert correlation(cal, "player_receptions", "player_rush_yds", "same_team") == pytest.approx(0.05)  # default same team
    assert correlation(cal, "player_receptions", "player_rush_yds", "cross_game") == 0.0


def test_weather_factor_defaults_to_one(cal):
    assert cal.weather_factor("QB", "wind", 17.0) == pytest.approx(0.80)
    assert cal.weather_factor("RB", "wind", 17.0) == pytest.approx(0.99)
    assert cal.weather_factor("QB", "wind", 2.0) == pytest.approx(1.0)
    assert cal.weather_factor("K", "cold", 20.0) == pytest.approx(0.82)
    assert cal.weather_factor("TE", "fog", 1.0) == 1.0


def test_calibrate_rows_adds_shrunk_points_without_mutating(cal):
    from calibration import calibrate_rows

    rows = [{"position": "QB", "points": 20.0, "adjusted_points": 21.0}, {"position": "RB", "points": 12.0},
            {"position": "K", "points": 0.0, "reason": "bye"}, {"position": "DEF", "points": 2.0}, {"position": "QB", "points": 1.0}]
    out = calibrate_rows(cal, rows)
    assert out[0]["calibrated_points"] == pytest.approx(round(-1.7 + 0.99 * 20.0, 1))
    assert out[0]["calibrated_adjusted_points"] == pytest.approx(round(-1.7 + 0.99 * 21.0, 1))
    assert out[1]["calibrated_points"] == 12.0 and out[1]["calibrated_adjusted_points"] == 12.0  # falls back to points
    assert out[2]["calibrated_points"] == 0.0 and out[3]["calibrated_points"] == 2.0  # a bye stays 0; K and DEF are not recalibrated
    assert out[4]["calibrated_points"] == 0.0  # never below zero
    assert "calibrated_points" not in rows[0] and out[0] is not rows[0]
