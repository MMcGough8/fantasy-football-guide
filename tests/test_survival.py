import pytest

from recommend import (
    cost_of_waiting,
    expected_best_vor,
    likely_gone,
    recommend_pick,
    survival_probability,
)


def _p(name, pos, vor, adp):
    return {"name": name, "position": pos, "vor": vor, "adp": adp}


def test_survival_is_one_when_no_picks_happen():
    assert survival_probability(adp=10, picks_made=5, gap=0) == 1.0


def test_survival_falls_with_a_longer_gap_and_bounded():
    short = survival_probability(adp=30, picks_made=18, gap=5)
    long = survival_probability(adp=30, picks_made=18, gap=20)
    assert 0.0 <= long < short <= 1.0


def test_survival_is_higher_for_later_adp():
    early = survival_probability(adp=20, picks_made=18, gap=10)
    late = survival_probability(adp=60, picks_made=18, gap=10)
    assert late > early


def test_survival_without_adp_is_certain():
    assert survival_probability(adp=None, picks_made=18, gap=10) == 1.0


def test_expected_best_vor_weights_by_survival():
    pool = [_p("A", "RB", 100, 10), _p("B", "RB", 50, 80), _p("C", "RB", 20, 150)]
    # Far from the board (pick 5, 20 picks away) A is almost surely gone; B likely stays
    e = expected_best_vor(pool, "RB", picks_made=5, gap=20)
    assert 45 < e < 70
    # No gap: the best player is still there
    assert expected_best_vor(pool, "RB", picks_made=5, gap=0) == pytest.approx(100)


def test_opportunity_cost_prefers_the_player_who_will_not_be_there():
    # Henry has slightly more VOR but an ADP that says he often comes back; CMC is gone
    available = [_p("Henry", "RB", 127.7, 17.4), _p("CMC", "RB", 126.3, 5.9), _p("WR X", "WR", 60, 40)]
    pick, reason = recommend_pick(
        available, needs={"RB": 2}, roster_size=0, total_picks=14, picks_made=5, gap=13
    )
    assert pick["name"] == "CMC"
    assert "to be there" in reason


def test_recommendation_without_pick_info_is_pure_vor():
    available = [_p("Henry", "RB", 127.7, 17.4), _p("CMC", "RB", 126.3, 5.9)]
    pick, _ = recommend_pick(available, needs={"RB": 2}, roster_size=0, total_picks=14)
    assert pick["name"] == "Henry"


def test_likely_gone_lists_players_unlikely_to_survive():
    available = [_p("A", "RB", 100, 6), _p("B", "WR", 90, 8), _p("C", "TE", 20, 200)]
    gone = likely_gone(available, picks_made=5, gap=13)
    assert [p["name"] for p in gone] == ["A", "B"]


def test_cost_of_waiting_per_position():
    available = [
        _p("QB1", "QB", 40, 20), _p("QB2", "QB", -4, 90),
        _p("RB1", "RB", 45, 30), _p("RB2", "RB", 40, 35), _p("RB3", "RB", 38, 38),
    ]
    table = cost_of_waiting(available, ["QB", "RB"], picks_made=28, gap=12)
    qb, rb = table["QB"], table["RB"]
    assert qb["now"]["name"] == "QB1" and qb["cost"] > rb["cost"]
    assert rb["now"]["name"] == "RB1"
    assert rb["cost"] >= 0
