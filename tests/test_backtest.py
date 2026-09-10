import math
import random

import pytest

from backtest import (
    build_calibration, decision_curve, fit_correlations, fit_counts, fit_games, fit_points, fit_ratio_tables, fit_td_factor,
    fit_team_game_correlations,
    fit_weather, quantile_table,
)
from calibration import Calibration, correlation, outcome_cdf, shrink_points

STATS = ("pass_yd", "pass_td", "rush_yd", "rec", "rec_yd", "rec_td", "rush_td")


def _row(pos, proj, act, ppr_proj, ppr_actual, season="2025", week=1, pid=None, team="A", opp="B", wind=None, temp=None,
         roof="outdoors", site="home"):
    return {
        "season": season, "week": week, "pid": pid or f"{pos}{ppr_proj}", "name": pid or pos, "pos": pos, "team": team, "opp": opp,
        "feeds": 3, "proj": {s: proj.get(s, 0.0) for s in STATS}, "ppr_proj": ppr_proj, "ppr_actual": ppr_actual,
        "act": {s: act.get(s, 0.0) for s in STATS}, "implied": 22.0, "site": site, "roof": roof, "wind": wind, "temp": temp,
        "game": tuple(sorted([team, opp])),
    }


def test_quantile_table_normalises_the_median_to_one():
    ratios = [0.0, 0.5, 0.8, 1.0, 1.2, 1.5, 3.0]
    table = quantile_table(ratios, k50=1.0)
    assert table["p"][0] == 0.0 and table["p"][-1] == 1.0 and len(table["p"]) == 21
    assert table["x"][10] == pytest.approx(1.0) and table["x"][0] == 0.0 and table["x"][-1] == 3.0
    halved = quantile_table(ratios, k50=0.5)
    assert halved["x"][10] == pytest.approx(2.0)  # ratios are divided by the median ratio


def test_fit_ratio_tables_bands_by_projection_and_respects_the_floor():
    rng = random.Random(1)
    rows = []
    for i in range(300):
        proj = 30 + i * 0.3  # 30 .. 120 yards
        rows.append(_row("WR", {"rec_yd": proj}, {"rec_yd": proj * 0.85 * rng.uniform(0.2, 1.8)}, 10, 10, pid=str(i)))
    rows.append(_row("WR", {"rec_yd": 10.0}, {"rec_yd": 200.0}, 3, 3, pid="tiny"))  # below the floor, ignored
    tables = fit_ratio_tables(rows, {"rec_yd": 25.0})
    wr = tables["WR"]["rec_yd"]
    assert wr["floor"] == 25.0 and len(wr["bands"]) == 3
    assert wr["bands"][0]["lo"] == pytest.approx(25.0) and wr["bands"][-1]["hi"] > 119  # first band opens at the floor
    assert sum(b["n"] for b in wr["bands"]) == 300
    for band in wr["bands"]:
        assert 0.7 < band["k50"] < 1.0 and band["x"][10] == pytest.approx(1.0)
    assert "QB" not in tables


def test_fit_points_recovers_a_linear_relationship_and_the_spread():
    rng = random.Random(2)
    rows = [_row("QB", {}, {}, p, 2.0 + 0.9 * p + rng.gauss(0, 4 + 0.1 * p), pid=str(i)) for i, p in enumerate(rng.uniform(8, 30) for _ in range(2000))]
    fit = fit_points(rows)["QB"]
    assert fit["a"] == pytest.approx(2.0, abs=0.6) and fit["b"] == pytest.approx(0.9, abs=0.03)
    assert fit["c0"] == pytest.approx(4.0, abs=1.0) and fit["c1"] == pytest.approx(0.1, abs=0.05)
    assert fit["n"] == 2000


def test_fit_counts_measures_dispersion_and_mean_ratio():
    rng = random.Random(3)

    def poisson(lam):
        total, k, p = math.exp(-lam), 0, rng.random()
        while p > total:
            k += 1
            total += math.exp(-lam) * lam ** k / math.factorial(k)
        return k

    rows = [_row("WR", {"rec": lam}, {"rec": poisson(lam)}, 10, 10, pid=str(i)) for i, lam in enumerate(rng.uniform(3, 8) for _ in range(3000))]
    fit = fit_counts(rows, {"rec": 2.5})["WR"]["rec"]
    assert fit["mean_ratio"] == pytest.approx(1.0, abs=0.05) and fit["dispersion"] == pytest.approx(1.0, abs=0.15)
    assert fit["floor"] == 2.5 and fit["n"] == 3000


def test_fit_td_factor_is_one_for_poisson_touchdowns():
    rng = random.Random(4)
    rows = []
    for i in range(4000):
        lam = rng.uniform(0.1, 0.9)
        scored = 1.0 if rng.random() < 1 - math.exp(-lam) else 0.0
        rows.append(_row("RB", {"rush_td": lam}, {"rush_td": scored}, 10, 10, pid=str(i)))
    fit = fit_td_factor(rows)
    assert fit["k"] == pytest.approx(1.0, abs=0.08) and fit["n"] == 4000


def test_fit_correlations_reads_the_qb_receiver_stack_with_sign():
    rows = []
    rng = random.Random(5)
    for g in range(200):
        e = rng.gauss(0, 40)
        home, away = f"H{g}", f"A{g}"
        rows += [
            _row("QB", {"pass_yd": 250}, {"pass_yd": 250 + e}, 18, 18, week=g, pid=f"qb{g}", team=home, opp=away),
            _row("WR", {"rec_yd": 80}, {"rec_yd": 80 + e / 2}, 12, 12, week=g, pid=f"wr{g}", team=home, opp=away),
            _row("RB", {"rush_yd": 70}, {"rush_yd": 70 - e / 3}, 12, 12, week=g, pid=f"rb{g}", team=home, opp=away),
            _row("QB", {"pass_yd": 240}, {"pass_yd": 240 + rng.gauss(0, 40)}, 17, 17, week=g, pid=f"qb2{g}", team=away, opp=home),
        ]
    pairs = fit_correlations(rows)["pairs"]
    assert pairs["player_pass_yds|player_reception_yds|same_team_qb_wr1"] == pytest.approx(1.0, abs=0.01)
    assert pairs["player_pass_yds|player_rush_yds|same_team"] == pytest.approx(-1.0, abs=0.01)
    assert abs(pairs["player_pass_yds|player_pass_yds|opponent"]) < 0.2


def test_fit_weather_normalises_to_calm_and_needs_a_sample():
    rows = []
    for i in range(60):
        rows.append(_row("QB", {}, {}, 20, 20.0, wind=2.0, temp=60.0, pid=f"c{i}"))
        rows.append(_row("QB", {}, {}, 20, 16.0, wind=20.0, temp=60.0, pid=f"w{i}"))
        rows.append(_row("QB", {}, {}, 20, 19.0, wind=2.0, temp=60.0, roof="dome", pid=f"d{i}"))  # indoors, ignored
    weather = fit_weather(rows, min_n=30)
    assert weather["wind"]["QB"][0] == 1.0 and weather["wind"]["QB"][-1] == pytest.approx(0.8)
    assert weather["wind"]["QB"][1] == 1.0  # a band with no sample stays neutral
    assert "RB" not in weather["wind"]


def test_decision_curve_counts_correct_calls_by_gap():
    rows = [_row("WR", {}, {}, p, p * 1.0, pid=str(p)) for p in (5, 7, 10, 14, 20)]  # higher projection always scores more
    curve = decision_curve(rows)
    assert all(b["observed"] == 1.0 for b in curve if b["n"])
    assert sum(b["n"] for b in curve) == 10


def test_build_calibration_produces_a_file_the_calibration_module_reads():
    rng = random.Random(6)
    rows = []
    for i in range(400):
        proj = rng.uniform(30, 100)
        rows.append(_row("WR", {"rec_yd": proj, "rec": 5.0, "rec_td": 0.4}, {"rec_yd": proj * 0.9 * rng.uniform(0.3, 1.7), "rec": 5, "rec_td": 0.0},
                         proj / 8, proj / 8 * rng.uniform(0.5, 1.5), pid=str(i), wind=3.0, temp=65.0))
        rows.append(_row("QB", {"pass_yd": 250, "pass_td": 1.6}, {"pass_yd": 240, "pass_td": 2}, 20, 18 + rng.gauss(0, 5), pid=f"q{i}", wind=3.0, temp=65.0))
    data = build_calibration(rows, ["2025"], today="2026-09-10")
    for key in ("points", "ratio_tables", "counts", "touchdowns", "correlations", "weather", "decision_curve", "fitted_on", "seasons"):
        assert key in data
    cal = Calibration(data)
    assert shrink_points(cal, "QB", 20.0) == pytest.approx(data["points"]["QB"]["a"] + data["points"]["QB"]["b"] * 20.0)
    assert 0.0 < outcome_cdf(cal, "WR", "rec_yd", 60.0, 1.0) < 1.0
    assert correlation(cal, "player_pass_yds", "player_reception_yds", "cross_game") == 0.0


def _game(week, home, away, spread_line, total_line, home_score, away_score):
    return {"week": week, "home": home, "away": away, "spread_line": spread_line, "total": total_line,
            "home_score": home_score, "away_score": away_score}


def test_fit_games_measures_the_margin_and_total_spread_around_the_market():
    rng = random.Random(3)
    games = []
    for w in range(400):
        spread, total = rng.choice([-7.0, -3.0, 0.0, 3.0, 6.5]), 45.0
        margin = round(spread + rng.gauss(0, 13))
        points = round(total + rng.gauss(0, 13))
        home = (points + margin) / 2
        games.append(_game(w, f"H{w}", f"A{w}", spread, total, home, points - home))
    games.append(_game(999, "X", "Y", 3.0, 44.0, None, None))  # unplayed: ignored
    fit = fit_games(games)
    assert fit["n"] == 400
    assert fit["margin_sd"] == pytest.approx(13.0, abs=1.5) and fit["total_sd"] == pytest.approx(13.0, abs=1.5)
    assert abs(fit["margin_bias"]) < 2.0 and abs(fit["total_bias"]) < 2.0
    assert abs(fit["favourite_over_rho"]) < 0.15
    assert fit_games([])["n"] == 0 and fit_games([])["margin_sd"] is None


def test_fit_team_game_correlations_reads_the_sign_from_the_teams_own_margin():
    rng = random.Random(4)
    rows = []
    for g in range(300):
        margin, total = rng.gauss(0, 13), rng.gauss(0, 13)
        # the QB throws more when the game goes over; the RB runs more when his team leads
        rows.append({**_row("QB", {"pass_yd": 250}, {"pass_yd": 250 + 2 * total + rng.gauss(0, 5)}, 18, 18, week=g, pid=f"q{g}", team="H", opp="A"),
                     "margin_resid": margin, "total_resid": total})
        rows.append({**_row("RB", {"rush_yd": 70}, {"rush_yd": 70 + 2 * margin + rng.gauss(0, 5)}, 12, 12, week=g, pid=f"r{g}", team="H", opp="A"),
                     "margin_resid": margin, "total_resid": total})
        rows.append({**_row("WR", {"rec_yd": 10}, {"rec_yd": 200}, 2, 2, week=g, pid=f"w{g}", team="H", opp="A"),
                     "margin_resid": margin, "total_resid": total})  # under the floor: ignored
        rows.append({**_row("TE", {"rec_yd": 60}, {"rec_yd": 60}, 8, 8, week=g, pid=f"t{g}", team="H", opp="A"),
                     "margin_resid": None, "total_resid": None})  # no score: ignored
    pairs = fit_team_game_correlations(rows)
    assert pairs["player_pass_yds|total|same_game"] > 0.9
    assert pairs["player_rush_yds|spread|same_team"] > 0.9
    assert pairs["player_rush_yds|moneyline|same_team"] == pairs["player_rush_yds|spread|same_team"]
    assert pairs["player_rush_yds|spread|opponent"] == -pairs["player_rush_yds|spread|same_team"]
    assert "player_reception_yds|total|same_game" not in pairs


def test_build_calibration_carries_the_game_fit_into_the_correlation_table():
    rows = [{**_row("QB", {"pass_yd": 250}, {"pass_yd": 260}, 18, 18, pid=str(i)), "margin_resid": 1.0 * i, "total_resid": -1.0 * i} for i in range(60)]
    games = [_game(w, f"H{w}", f"A{w}", -3.0, 45.0, 24 + (w % 5), 20) for w in range(60)]
    data = build_calibration(rows, ["2025"], today="2026-09-10", games=games)
    assert data["games"]["n"] == 60 and data["games"]["margin_sd"] > 0
    cal = Calibration(data)
    assert cal.games()["total_sd"] == data["games"]["total_sd"]
    assert correlation(cal, "spread", "total", "same_game") == data["games"]["favourite_over_rho"]
    assert correlation(cal, "moneyline", "total", "same_game") == data["games"]["favourite_over_rho"]
    assert Calibration({}).games() == {}


def test_fit_games_reads_the_favourite_over_sign_and_a_flat_series_is_zero_not_nan():
    games = []
    for w in range(80):
        # the favourite covers by k and the game goes over by k: perfectly correlated, whichever side is favoured
        k, spread = (w % 7) - 3, 3.0 if w % 2 else -3.0
        home_margin = spread + (k if spread > 0 else -k)
        games.append(_game(w, f"H{w}", f"A{w}", spread, 44.0, (44 + k + home_margin) / 2, (44 + k - home_margin) / 2))
    assert fit_games(games)["favourite_over_rho"] == pytest.approx(1.0)
    flat = [_game(w, f"H{w}", f"A{w}", -3.0, 44.0, 24.0, 21.0) for w in range(80)]
    assert fit_games(flat)["favourite_over_rho"] == 0.0
