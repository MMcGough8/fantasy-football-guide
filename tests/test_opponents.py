import pytest

from opponents import demand_factor, demand_multipliers, team_counts, upcoming_rosters

STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}


def _draft(teams=4, rounds=14, draft_type="snake"):
    return {
        "type": draft_type,
        "draft_order": {f"u{i}": i for i in range(1, teams + 1)},
        "slot_to_roster_id": {str(i): i for i in range(1, teams + 1)},
        "settings": {"teams": teams, "rounds": rounds},
    }


def _pick(roster_id, pos):
    return {"roster_id": roster_id, "metadata": {"position": pos}}


def test_team_counts_from_picks():
    picks = [_pick(1, "RB"), _pick(1, "RB"), _pick(2, "WR"), _pick(3, "DEF")]
    assert team_counts(picks) == {1: {"RB": 2}, 2: {"WR": 1}, 3: {"DEF": 1}}


def test_upcoming_rosters_follow_the_snake():
    draft = _draft(teams=4)
    # 3 picks made: pick 4 is slot 4, then round 2 snakes back 4,3,2
    assert upcoming_rosters(draft, picks_made=3, n=4) == [4, 4, 3, 2]
    assert upcoming_rosters({**draft, "draft_order": None}, picks_made=3, n=4) == []


def test_demand_factor_reflects_open_slots_and_caps():
    empty = {}
    assert demand_factor(empty, "RB", STARTERS, current_round=1, total_rounds=14) == 1.0
    two_rb = {"RB": 2}
    # starters at RB filled but FLEX open: still some appetite
    assert 0 < demand_factor(two_rb, "RB", STARTERS, 5, 14) < 1.0
    three_rb_two_wr_te = {"RB": 3, "WR": 2, "TE": 1}
    # FLEX filled too: bench-only appetite
    assert demand_factor(three_rb_two_wr_te, "RB", STARTERS, 8, 14) < demand_factor(two_rb, "RB", STARTERS, 8, 14)
    # at the cap: essentially none
    assert demand_factor({"QB": 2}, "QB", STARTERS, 9, 14) < 0.1
    # kickers early: essentially none; in the last rounds with the slot open: full
    assert demand_factor({}, "K", STARTERS, 5, 14) < 0.1
    assert demand_factor({}, "K", STARTERS, 13, 14) == 1.0


def test_multipliers_shift_with_the_needs_of_the_teams_picking_next():
    draft = _draft(teams=4)
    # teams 4 and 3 (the next pickers after 3 picks) are stacked at RB; teams 1 and 2 are empty
    picks = [_pick(4, "RB"), _pick(4, "RB"), _pick(4, "RB"), _pick(3, "RB"), _pick(3, "RB"), _pick(3, "RB")]
    m = demand_multipliers(draft, picks, STARTERS, picks_made=3, gap=3)
    assert m["RB"] < 1.0  # the teams in front of me do not need RBs
    assert m["WR"] >= 1.0  # they do need WRs
    assert 0.3 <= m["RB"] <= 2.5


def test_multipliers_are_neutral_without_a_draft_order():
    assert demand_multipliers({"draft_order": None}, [], STARTERS, picks_made=0, gap=5) == {}
