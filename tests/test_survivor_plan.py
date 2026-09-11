import math

import pytest

from odds import parse_game_lines
import itertools
import random

from survivor_plan import (
    INFEASIBLE_COST, P_FLOOR, TIE_RATE, best_future, game_win_probability, hungarian, live_win_probabilities, moneyline_win_probability,
    priced_weeks, rank_candidates, spread_pair, spread_win_probability, week_probabilities,
)

SD = 12.64


def _game(week, home, away, spread=None, home_ml=None, away_ml=None, scores=None, neutral=False):
    home_score, away_score = scores if scores else (None, None)
    return {"week": week, "home": home, "away": away, "neutral": neutral, "total": 44.5, "spread_line": spread, "roof": "outdoors",
            "gameday": "2026-09-13", "gametime": "13:00", "home_score": home_score, "away_score": away_score,
            "home_moneyline": home_ml, "away_moneyline": away_ml}


def _live_leg(home_name, away_name, home_price, away_price):
    event = {"id": "e1", "commence_time": "2026-09-13T17:00:00Z", "home_team": home_name, "away_team": away_name,
             "bookmakers": [{"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [{"name": home_name, "price": home_price}, {"name": away_name, "price": away_price}]}]}]}
    return [leg for leg in parse_game_lines(event) if leg["market"] == "moneyline"]


def test_spread_win_probability_hand_values_and_the_tie_comes_off_both_sides():
    assert spread_win_probability(3.0, SD) == pytest.approx(0.578, abs=0.001)
    assert spread_win_probability(7.0, SD) == pytest.approx(0.697, abs=0.001)
    assert spread_win_probability(-7.0, SD) == pytest.approx(0.277, abs=0.001)
    assert spread_win_probability(0.0, SD) == pytest.approx(0.484, abs=0.001)  # a pick'em: a tie is a loss for both sides


def test_moneyline_win_probability_devigs():
    assert moneyline_win_probability(-150, 130) == pytest.approx(0.580, abs=0.001)


def test_game_win_probability_prefers_live_then_moneyline_then_spread():
    live = live_win_probabilities(_live_leg("Indianapolis Colts", "Atlanta Falcons", -160, 136))
    assert set(live) == {("IND", "ATL")} and 0.58 < live[("IND", "ATL")] < 0.61
    game = _game(1, "IND", "ATL", spread=3.0, home_ml=-150, away_ml=130)
    assert game_win_probability(game, live, SD) == (pytest.approx(live[("IND", "ATL")]), "live")
    assert game_win_probability(game, {}, SD) == (pytest.approx(0.580, abs=0.001), "moneyline")
    assert game_win_probability(_game(1, "IND", "ATL", spread=3.0), {}, SD) == (pytest.approx(0.596, abs=0.001), "spread")  # 0.578 rescaled to the real tie rate
    assert game_win_probability(_game(1, "IND", "ATL"), {}, SD) == (None, None)
    assert game_win_probability(_game(2, "ATL", "IND", spread=3.0), live, SD)[1] == "spread"  # the live pair is week 1's, home and away swapped
    fresh = {**game, "line_source": "live"}  # a live spread beats the weekly moneyline snapshot
    assert game_win_probability(fresh, {}, SD)[1] == "spread"
    home, away = spread_pair(3.0, SD)
    assert home + away == pytest.approx(1 - TIE_RATE) and home / away == pytest.approx(0.578 / 0.391, abs=0.01)


def test_week_probabilities_lists_both_sides_skips_played_games_and_counts_the_unpriced():
    games = [_game(1, "IND", "ATL", spread=3.0), _game(1, "KC", "DEN", spread=-7.0), _game(1, "NE", "SEA", spread=3.0, scores=(13, 20)), _game(1, "PHI", "DAL"),
             _game(2, "ATL", "IND", spread=1.0)]
    info = week_probabilities(games, 1, SD)
    assert set(info["p"]) == {"IND", "ATL", "KC", "DEN"} and "NE" not in info["p"]  # NE-SEA is final, PHI-DAL unpriced
    assert info["p"]["KC"] == pytest.approx(0.284, abs=0.001) and info["p"]["DEN"] == pytest.approx(0.715, abs=0.001)
    assert info["p"]["IND"] + info["p"]["ATL"] == pytest.approx(1 - TIE_RATE)  # the spread route sits on the moneyline scale
    live = live_win_probabilities(_live_leg("Indianapolis Colts", "Atlanta Falcons", -160, 136))
    two_way = week_probabilities(games, 1, SD, live)["p"]
    assert two_way["IND"] + two_way["ATL"] == pytest.approx(1.0)
    assert info["unpriced"] == 1 and info["games"] == 3
    assert info["opponents"]["ATL"] == {"opponent": "IND", "site": "away"} and info["opponents"]["IND"]["site"] == "home"
    assert info["sources"][("KC", "DEN")] == "spread"


def test_priced_weeks_stops_at_the_first_week_with_too_few_lines():
    games = [_game(1, "IND", "ATL", spread=3.0), _game(2, "ATL", "IND", spread=-1.0), _game(3, "IND", "KC", spread=2.0), _game(3, "ATL", "DEN"), _game(4, "KC", "ATL", spread=1.0)]
    out = priced_weeks(games, 1, SD)
    assert list(out["weeks"]) == [1, 2] and out["dropped"] == 3  # week 3: one of two games priced
    assert priced_weeks(games, 4, SD)["weeks"].keys() == {4}
    mostly = [_game(2, h, a, spread=1.0) for h, a in (("A", "B"), ("C", "D"), ("E", "F"), ("G", "H"))] + [_game(2, "I", "J")]  # 4 of 5 priced
    out = priced_weeks([_game(1, "A", "B", spread=1.0)] + mostly, 1, SD)
    assert list(out["weeks"]) == [1, 2] and out["dropped"] is None and "I" not in out["weeks"][2]["p"]


def test_hungarian_finds_the_exact_assignment_where_greedy_fails():
    # week 2: KC .80, PHI .78, DAL .60; week 3: KC .85, DAL .60, PHI .55. Greedy takes KC then DAL (.48); PHI then KC is .663.
    weeks_p = {2: {"KC": 0.80, "PHI": 0.78, "DAL": 0.60}, 3: {"KC": 0.85, "DAL": 0.60, "PHI": 0.55}}
    plan = best_future(weeks_p, [2, 3], set())
    assert plan["picks"] == {2: "PHI", 3: "KC"} and plan["survival"] == pytest.approx(0.78 * 0.85) and plan["unfilled"] == []
    cost = [[4.0, 1.0, 3.0], [2.0, 0.0, 5.0], [3.0, 2.0, 2.0]]
    assert sum(cost[i][j] for i, j in enumerate(hungarian(cost))) == 5.0
    with pytest.raises(ValueError):
        hungarian([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])  # more rows than columns would never terminate


def test_hungarian_matches_brute_force_on_random_matrices_with_ties_and_padding():
    rng = random.Random(11)
    for _ in range(300):
        n = rng.randint(1, 5)
        m = n + rng.randint(0, 3)
        cost = [[rng.choice([0.0, 0.5, 1.0, 2.5, INFEASIBLE_COST]) for _ in range(m)] for _ in range(n)]
        got = hungarian(cost)
        assert len(set(got)) == n and all(0 <= c < m for c in got)
        best = min(sum(cost[i][p[i]] for i in range(n)) for p in itertools.permutations(range(m), n))
        assert sum(cost[i][got[i]] for i in range(n)) == pytest.approx(best)


def test_best_future_marks_a_week_nobody_can_fill_and_respects_exclusions():
    weeks_p = {2: {"KC": 0.80, "PHI": 0.78}, 3: {"KC": 0.85}}
    plan = best_future(weeks_p, [2, 3], {"KC"})
    assert plan["picks"] == {2: "PHI"} and plan["unfilled"] == [3] and plan["survival"] == pytest.approx(0.78 * P_FLOOR)  # an empty week is never free
    rows = rank_candidates({1: {"p": {"KC": 0.8, "BUF": 0.75}, "opponents": {"KC": {"opponent": "X", "site": "home"}, "BUF": {"opponent": "Y", "site": "home"}}, "sources": {}, "unpriced": 0, "games": 1},
                            2: {"p": {"KC": 0.5}, "opponents": {"KC": {"opponent": "Z", "site": "home"}}, "sources": {}, "unpriced": 0, "games": 1}}, 1, set(), set())
    assert [r["team"] for r in rows] == ["BUF", "KC"] and rows[1]["future_cost"] > 0  # burning the only week-2 team can never look free
    assert best_future(weeks_p, [], set()) == {"picks": {}, "survival": 1.0, "unfilled": []}
    assert INFEASIBLE_COST > 8 * abs(math.log(1e-4))


def _weeks(this, future):
    return {w: {"p": p, "opponents": {t: {"opponent": "OPP", "site": "home"} for t in p}, "sources": {}, "unpriced": 0, "games": len(p) // 2}
            for w, p in {1: this, 2: future}.items()}


def test_recommendation_saves_the_bigger_future_favourite():
    weeks = _weeks({"KC": 0.74, "BUF": 0.72, "NYJ": 0.55}, {"KC": 0.85, "BUF": 0.60, "NYJ": 0.50})
    rows = rank_candidates(weeks, 1, set(), set())
    assert [r["team"] for r in rows] == ["BUF", "NYJ", "KC"]
    buf, nyj, kc = rows
    assert buf["horizon"] == pytest.approx(0.72 * 0.85) and buf["future_cost"] == 0.0 and buf["plan"] == {2: "KC"}
    assert kc["horizon"] == pytest.approx(0.74 * 0.60) and kc["future_cost"] == pytest.approx(0.25) and kc["saved_for"] == 2 and kc["saved_p"] == 0.85
    assert buf["reason"] == "BUF wins 72% of the time vs OPP; the plan keeps KC for week 2 (85%)."
    assert kc["reason"] == "KC wins 85% of the time in week 2; save it (using it now costs 25 points of plan survival)."
    assert nyj["reason"] == "NYJ wins 55% of the time vs OPP and is never the plan's pick later."


def test_rank_candidates_drops_used_and_locked_teams_and_handles_a_horizon_of_one_week():
    weeks = _weeks({"KC": 0.74, "BUF": 0.72, "NYJ": 0.55}, {"KC": 0.85, "BUF": 0.60, "NYJ": 0.50})
    rows = rank_candidates(weeks, 1, {"BUF"}, {"KC"})
    assert [r["team"] for r in rows] == ["NYJ"] and rows[0]["plan"] == {2: "KC"}  # BUF used, KC locked this week only
    only = {1: _weeks({"KC": 0.74, "BUF": 0.72}, {})[1]}
    rows = rank_candidates(only, 1, set(), set())
    assert rows[0]["team"] == "KC" and rows[0]["horizon"] == pytest.approx(0.74)
    assert rows[0]["reason"] == "KC wins 74% of the time vs OPP; no lines posted beyond this week, so nothing is being saved."


def test_rank_candidates_is_deterministic_on_equal_probabilities():
    weeks = _weeks({"KC": 0.7, "BUF": 0.7, "DEN": 0.7}, {"KC": 0.7, "BUF": 0.7, "DEN": 0.7})
    assert [r["team"] for r in rank_candidates(weeks, 1, set(), set())] == ["BUF", "DEN", "KC"]
