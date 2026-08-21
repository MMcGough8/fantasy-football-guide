import pytest

from scoring import (
    PRESET_FALLBACK_POSITIONS,
    estimate_threshold_bonuses,
    score_stats,
    unprojected_bonus_keys,
)


def _stats(projections, pos, last_name):
    return next(r["stats"] for r in projections[pos] if r["player"]["last_name"] == last_name)


def test_qb_uses_six_point_passing_tds(league, projections):
    stats = _stats(projections, "QB", "Allen")
    pts = score_stats(stats, league["scoring_settings"], "QB")
    # 3650 pass yds / 30 + 27 pass TD * 6 - 10 INT + 535 rush yds * 0.1 + 11 rush TD * 6
    # + 2pt conversions - 3 fumbles lost * 2
    base = 3650 / 30 + 27 * 6 - 10 + 535 * 0.1 + 11 * 6 + 1 * 2 + 1 * 2 - 3 * 2
    bonuses = sum(estimate_threshold_bonuses(stats, league["scoring_settings"]).values())
    assert pts == pytest.approx(base + bonuses, abs=0.05)
    assert pts > stats["pts_std"]


def test_rb_uses_four_tenths_ppr(league, projections):
    stats = _stats(projections, "RB", "Gibbs")
    without_bonuses = {k: v for k, v in stats.items() if k != "gp"}
    pts = score_stats(without_bonuses, league["scoring_settings"], "RB")
    assert stats["pts_std"] < pts < stats["pts_half_ppr"]


def test_kicker_and_defense_fall_back_to_sleeper_preset(league, projections):
    assert PRESET_FALLBACK_POSITIONS == {"K", "DEF"}
    k_stats = _stats(projections, "K", "Aubrey")
    d_stats = _stats(projections, "DEF", "Rams")
    assert score_stats(k_stats, league["scoring_settings"], "K") == k_stats["pts_std"]
    assert score_stats(d_stats, league["scoring_settings"], "DEF") == d_stats["pts_std"]


def test_ignores_stat_keys_the_league_does_not_score(league):
    stats = {"cmp_pct": 66.0, "pass_yd": 300}
    assert score_stats(stats, league["scoring_settings"], "QB") == pytest.approx(10.0)


def test_threshold_bonuses_are_estimated_from_season_totals(league):
    # 1700 rush yds over 17 games averages 100/game: roughly half the games
    # clear the 100-yard bonus, a few clear 200.
    stats = {"rush_yd": 1700, "gp": 17}
    bonus = estimate_threshold_bonuses(stats, league["scoring_settings"])
    assert 10 < bonus["bonus_rush_yd_100"] < 20
    assert 0 < bonus["bonus_rush_yd_200"] < 4
    assert score_stats(stats, league["scoring_settings"], "RB") > 170


def test_threshold_bonus_is_monotonic_in_volume(league):
    low = estimate_threshold_bonuses({"rec_yd": 600, "gp": 17}, league["scoring_settings"])
    high = estimate_threshold_bonuses({"rec_yd": 1400, "gp": 17}, league["scoring_settings"])
    assert high["bonus_rec_yd_100"] > low["bonus_rec_yd_100"]


def test_threshold_bonus_zero_without_games_or_volume(league):
    assert estimate_threshold_bonuses({"rush_yd": 0, "gp": 17}, league["scoring_settings"]) == {}
    assert estimate_threshold_bonuses({"rush_yd": 900}, league["scoring_settings"]) == {}


def test_unprojected_bonus_keys_are_reported(league):
    assert "pass_td_40p" in unprojected_bonus_keys(league["scoring_settings"])
    assert "bonus_rush_yd_100" not in unprojected_bonus_keys(league["scoring_settings"])


def test_no_scoring_settings_returns_none():
    assert score_stats({"pass_yd": 300, "pts_std": 10}, None, "QB") is None
