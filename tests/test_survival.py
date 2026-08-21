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
    assert "still be there" in reason


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


def test_between_turns_only_players_likely_to_reach_me_are_candidates():
    # 10 picks until my turn: the ADP-5 star is gone; the ADP-40 player reaches me
    available = [_p("Star", "WR", 150, 5), _p("Reachable", "WR", 60, 40)]
    pick, _ = recommend_pick(
        available, needs={"WR": 2}, roster_size=1, total_picks=14,
        picks_made=6, gap=18, reach_gap=10,
    )
    assert pick["name"] == "Reachable"


def test_on_the_clock_everyone_is_a_candidate():
    available = [_p("Star", "WR", 150, 5), _p("Reachable", "WR", 60, 40)]
    pick, _ = recommend_pick(
        available, needs={"WR": 2}, roster_size=1, total_picks=14,
        picks_made=6, gap=18, reach_gap=0,
    )
    assert pick["name"] == "Star"


def test_players_without_adp_do_not_pin_the_expected_best():
    pool = [_p("Known", "RB", 100, 10), _p("Mystery", "RB", 120, None), _p("Depth", "RB", 30, 150)]
    e = expected_best_vor(pool, "RB", picks_made=5, gap=20)
    assert e < 100  # Mystery (no ADP) is ignored, not assumed 100% available


def test_cost_of_waiting_now_column_respects_reach():
    available = [_p("Star", "RB", 150, 5), _p("Reachable", "RB", 60, 40), _p("Depth", "RB", 20, 120)]
    table = cost_of_waiting(available, ["RB"], picks_made=6, gap=18, reach_gap=10)
    assert table["RB"]["now"]["name"] == "Reachable"


def test_survival_spread_grows_with_the_square_root_of_adp():
    # Early picks are tight, late picks loose: the odds of an ADP-10 player surviving
    # 6 picks should be far lower than an ADP-100 player surviving 6 picks.
    early = survival_probability(adp=10, picks_made=9, gap=6)
    late = survival_probability(adp=100, picks_made=99, gap=6)
    assert early < 0.5 < late
    assert late > 0.7


def test_cost_of_waiting_is_zero_when_you_pick_again_immediately():
    available = [_p("RB1", "RB", 45, 30), _p("RB2", "RB", 20, 35)]
    table = cost_of_waiting(available, ["RB"], picks_made=11, gap=0)
    assert table["RB"]["cost"] == 0
    assert table["RB"]["later"] == 45


def test_survival_uses_the_expert_rank_spread_when_present():
    from recommend import survival_for

    contested = {"adp": 30, "fp_rank_std": 12.0}
    settled = {"adp": 30, "fp_rank_std": 2.0}
    plain = {"adp": 30}
    # A contested player has fatter tails: more likely to slip to me than a settled one
    assert survival_for(contested, picks_made=18, gap=12) > survival_for(settled, picks_made=18, gap=12)
    assert 0 < survival_for(plain, picks_made=18, gap=12) < 1


def test_demand_multiplier_scales_the_hazard():
    from recommend import survival_for

    p = {"adp": 30}
    base = survival_for(p, picks_made=18, gap=12)
    hungrier = survival_for(p, picks_made=18, gap=12, demand=lambda gap: {"RB": 2.0})
    full = survival_for(p, picks_made=18, gap=12, demand=lambda gap: {"RB": 0.5})
    # p has no position; multipliers apply per position
    assert hungrier == base == full
    rb = {"adp": 30, "position": "RB"}
    assert survival_for(rb, 18, 12, demand=lambda gap: {"RB": 2.0}) < survival_for(rb, 18, 12)
    assert survival_for(rb, 18, 12, demand=lambda gap: {"RB": 0.5}) > survival_for(rb, 18, 12)


def test_recommendation_reacts_to_opponent_needs():
    available = [_p("RB A", "RB", 60, 20), _p("WR A", "WR", 58, 20)]
    # Neutral market: the RB edges it
    pick, _ = recommend_pick(available, {"RB": 1, "WR": 1}, 2, 14, picks_made=10, gap=12, reach_gap=0)
    assert pick["name"] == "RB A"
    # The teams picking next are RB-full and WR-hungry: the RB will come back, the WR will not
    demand = lambda gap: {"RB": 0.3, "WR": 2.5}
    pick, _ = recommend_pick(available, {"RB": 1, "WR": 1}, 2, 14, picks_made=10, gap=12, reach_gap=0, demand=demand)
    assert pick["name"] == "WR A"
