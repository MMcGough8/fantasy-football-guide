from lineup import COIN_FLIP_POINTS, current_lineup, lineup_diff, lineup_points, optimal_lineup, startable


def _row(pid, pos, points, status=None, reason=None):
    return {"player_id": pid, "name": f"{pos}{pid}", "position": pos, "points": points, "injury_status": status, "reason": reason}


def test_current_lineup_follows_the_league_slot_order_and_keeps_empty_slots():
    rows = {"1": _row("1", "RB", 10), "2": _row("2", "WR", 8)}
    lineup = current_lineup(["1", "0", "2"], ["RB", "RB", "WR", "BN", "IR"], rows)
    assert list(lineup) == ["RB", "WR"]
    assert lineup["RB"] == [rows["1"], None] and lineup["WR"] == [rows["2"]]


def test_current_lineup_drops_idp_slots_but_keeps_flexes():
    rows = {"1": _row("1", "QB", 20), "2": _row("2", "LB", 9), "3": _row("3", "RB", 12)}
    lineup = current_lineup(["1", "2", "3"], ["QB", "LB", "SUPER_FLEX", "BN"], rows)
    assert list(lineup) == ["QB", "SUPER_FLEX"] and lineup["SUPER_FLEX"] == [rows["3"]]


def test_startable_excludes_out_doubtful_ir_and_reasons_but_keeps_questionable():
    rows = [_row("1", "RB", 10, "Out"), _row("2", "RB", 9, "Doubtful"), _row("3", "RB", 8, "IR"),
            _row("4", "RB", 7, "Questionable"), _row("5", "RB", 6, None, "bye")]
    assert [r["player_id"] for r in startable(rows)] == ["4"]


def test_optimal_lineup_uses_weekly_points_and_a_superflex_can_start_a_second_qb():
    rows = [_row("1", "QB", 22), _row("2", "QB", 19), _row("3", "RB", 14), _row("4", "WR", 11), _row("5", "RB", 9, "Out")]
    slots = optimal_lineup(rows, {"QB": 1, "RB": 1, "SUPER_FLEX": 1})
    assert [r["player_id"] for r in slots["QB"]] == ["1"]
    assert [r["player_id"] for r in slots["RB"]] == ["3"]
    assert [r["player_id"] for r in slots["SUPER_FLEX"]] == ["2"]
    assert lineup_points(slots) == 55


def test_lineup_diff_pairs_same_position_first_and_flags_coin_flips():
    bench_rb = _row("9", "RB", 15.0)
    cur = {"RB": [_row("1", "RB", 14.0), _row("2", "RB", 10.0)], "WR": [_row("3", "WR", 12.0)]}
    opt = {"RB": [bench_rb, _row("1", "RB", 14.0)], "WR": [_row("3", "WR", 12.0)]}
    swaps = lineup_diff(cur, opt)
    assert len(swaps) == 1
    swap = swaps[0]
    assert swap["in"]["player_id"] == "9" and swap["out"]["player_id"] == "2" and swap["slot"] == "RB"
    assert swap["delta"] == 5.0 and swap["coin_flip"] is False


def test_lineup_diff_is_empty_when_already_optimal_and_handles_empty_slots():
    row = _row("1", "RB", 14.0)
    assert lineup_diff({"RB": [row]}, {"RB": [row]}) == []
    swaps = lineup_diff({"RB": [row, None]}, {"RB": [row, _row("2", "RB", 1.0)]})
    assert swaps[0]["out"] is None and swaps[0]["delta"] == 1.0 and swaps[0]["coin_flip"] is True
    assert 0 < COIN_FLIP_POINTS < 3


def test_an_out_starter_counts_for_nothing_so_the_swap_gain_is_positive():
    from lineup import expected_points

    out_starter = _row("1", "RB", 14.0, "Out")  # FP/ESPN still publish a number for him
    healthy = _row("2", "RB", 11.0)
    cur = {"RB": [out_starter]}
    opt = optimal_lineup([out_starter, healthy], {"RB": 1})
    swap = lineup_diff(cur, opt)[0]
    assert swap["in"]["player_id"] == "2" and swap["out"]["player_id"] == "1"
    assert swap["delta"] == 11.0 and swap["out_reason"] == "Out"
    assert expected_points(cur) == 0 and lineup_points(cur) == 14.0


def test_optimal_lineup_pads_slots_it_cannot_fill():
    slots = optimal_lineup([_row("1", "RB", 10)], {"RB": 2, "WR": 1})
    assert slots["RB"] == [slots["RB"][0], None] and slots["WR"] == [None]
    assert lineup_points(slots) == 10


def test_lineup_diff_only_pairs_players_who_could_share_the_slot():
    cur = {"QB": [_row("1", "QB", 20.0)], "FLEX": [_row("2", "RB", 5.0)]}
    opt = {"QB": [_row("1", "QB", 20.0)], "FLEX": [_row("3", "WR", 9.0)]}
    assert lineup_diff(cur, opt)[0]["out"]["player_id"] == "2"  # a WR into FLEX can replace the RB there
    cur = {"QB": [_row("1", "QB", 20.0)], "WR": [None]}
    opt = {"QB": [_row("4", "QB", 22.0)], "WR": [_row("3", "WR", 9.0)]}
    swaps = {s["in"]["player_id"]: s for s in lineup_diff(cur, opt)}
    assert swaps["4"]["out"]["player_id"] == "1" and swaps["3"]["out"] is None


def test_current_lineup_ignores_extra_starter_ids():
    rows = {"1": _row("1", "RB", 10)}
    assert current_lineup(["1", "0", "0"], ["RB", "BN"], rows) == {"RB": [rows["1"]]}


def test_unplayable_starters_lists_current_starters_who_cannot_play():
    from lineup import unplayable_starters

    cur = {"RB": [_row("1", "RB", 14.0, "Out"), _row("2", "RB", 10.0)], "WR": [_row("3", "WR", 9.0, None, "bye"), None]}
    assert [(r["player_id"], reason) for r, reason in unplayable_starters(cur)] == [("1", "Out"), ("3", "bye")]


def test_lineup_functions_can_rank_by_another_key():
    a = dict(_row("1", "RB", 10.0), adjusted_points=8.0)
    b = dict(_row("2", "RB", 9.0), adjusted_points=11.0)
    slots = optimal_lineup([a, b], {"RB": 1}, key="adjusted_points")
    assert slots["RB"][0]["player_id"] == "2"
    assert lineup_points(slots, key="adjusted_points") == 11.0 and lineup_points(slots) == 9.0
    swaps = lineup_diff({"RB": [a]}, slots, key="adjusted_points")
    assert swaps[0]["delta"] == 3.0
    from lineup import expected_points

    assert expected_points(slots, key="adjusted_points") == 11.0


# ---- kickoff locks ----
from datetime import datetime

from lineup import expected_points, locked_starters, mark_locked, reachable_lineup, swap_deadline
from matchups import ET


def _lrow(pid, pos, points, locked=False, status=None):
    return {**_row(pid, pos, points, status), "locked": locked}


def test_mark_locked_stamps_new_dicts_by_team():
    rows = [dict(_row("1", "RB", 10), team="NE"), dict(_row("2", "RB", 9), team="KC"), dict(_row("3", "K", 7), team=None)]
    marked = mark_locked(rows, frozenset({"NE"}))
    assert [r["locked"] for r in marked] == [True, False, False]
    assert "locked" not in rows[0] and marked[0] is not rows[0]


def test_reachable_lineup_keeps_a_locked_starter_even_when_the_bench_is_better():
    starter, bench = _lrow("1", "RB", 8, locked=True), _lrow("2", "RB", 15)
    current = {"RB": [starter]}
    reach = reachable_lineup([starter, bench], current, {"RB": 1})
    assert reach == {"RB": [starter]} and lineup_diff(current, reach) == []


def test_reachable_lineup_never_starts_a_locked_bench_player():
    starter, bench, other = _lrow("1", "RB", 8), _lrow("2", "RB", 15, locked=True), _lrow("3", "RB", 9)
    reach = reachable_lineup([starter, bench, other], {"RB": [starter]}, {"RB": 1})
    assert reach["RB"][0]["player_id"] == "3"


def test_reachable_lineup_keeps_a_locked_flex_in_the_flex_while_the_rb_slot_is_refilled():
    flex_rb, rb, bench_rb = _lrow("1", "RB", 12, locked=True), _lrow("2", "RB", 7), _lrow("3", "RB", 10)
    current = {"RB": [rb], "FLEX": [flex_rb]}
    reach = reachable_lineup([flex_rb, rb, bench_rb], current, {"RB": 1, "FLEX": 1})
    assert reach["FLEX"] == [flex_rb] and reach["RB"][0]["player_id"] == "3"
    assert list(reach) == ["RB", "FLEX"]


def test_reachable_lineup_freezes_a_locked_out_starter_who_counts_for_nothing():
    out, bench = _lrow("1", "RB", 14, locked=True, status="Out"), _lrow("2", "RB", 11)
    current = {"RB": [out]}
    reach = reachable_lineup([out, bench], current, {"RB": 1})
    assert reach == {"RB": [out]} and expected_points(reach) == 0 and lineup_diff(current, reach) == []


def test_fully_locked_lineup_yields_no_swaps_and_lists_its_locked_starters():
    a, b, c = _lrow("1", "QB", 20, locked=True), _lrow("2", "RB", 10, locked=True), _lrow("3", "RB", 30)
    current = {"QB": [a], "RB": [b]}
    reach = reachable_lineup([a, b, c], current, {"QB": 1, "RB": 1})
    assert reach == current and lineup_diff(current, reach) == []
    assert locked_starters(current) == [a, b] and locked_starters({"QB": [None], "RB": [c]}) == []


def test_reachable_lineup_pads_open_slots_nobody_can_fill():
    starter = _lrow("1", "RB", 8, locked=True)
    assert reachable_lineup([starter], {"RB": [starter, None]}, {"RB": 2}) == {"RB": [starter, None]}


def test_reachable_lineup_prefers_current_starters_on_ties_so_nothing_churns():
    starter, bench = _lrow("1", "WR", 12.0, status="Questionable"), _lrow("2", "RB", 12.0)
    current = {"FLEX": [starter]}
    reach = reachable_lineup([bench, starter], current, {"FLEX": 1})
    assert reach == current and lineup_diff(current, reach) == []
    better = _lrow("3", "RB", 12.1)
    assert reachable_lineup([starter, better], current, {"FLEX": 1})["FLEX"] == [better]


def test_reachable_lineup_keeps_a_locked_qb_in_super_flex_and_refills_qb():
    qb1, qb2, qb3 = _lrow("1", "QB", 22), _lrow("2", "QB", 18, locked=True), _lrow("3", "QB", 25)
    current = {"QB": [qb1], "SUPER_FLEX": [qb2]}
    reach = reachable_lineup([qb1, qb2, qb3], current, {"QB": 1, "SUPER_FLEX": 1})
    assert reach["SUPER_FLEX"] == [qb2] and reach["QB"] == [qb3]


def test_reachable_lineup_fills_a_slot_missing_from_current():
    rb, wr = _lrow("1", "RB", 10, locked=True), _lrow("2", "WR", 9)
    reach = reachable_lineup([rb, wr], {"RB": [rb]}, {"RB": 1, "WR": 1})
    assert reach == {"RB": [rb], "WR": [wr]}


def test_deadline_status_rounds_down_and_names_the_kickoff():
    from lineup import deadline_status

    kickoff = datetime(2026, 9, 13, 13, 0, tzinfo=ET)
    assert deadline_status(None, kickoff) is None
    assert deadline_status(kickoff, datetime(2026, 9, 13, 13, 0, tzinfo=ET)) == ("past", "kicked off")
    assert deadline_status(kickoff, datetime(2026, 9, 13, 10, 24, tzinfo=ET)) == ("soon", "kicks off in 2h (Sun 1:00 pm ET)")
    assert deadline_status(kickoff, datetime(2026, 9, 13, 12, 15, tzinfo=ET)) == ("soon", "kicks off in 45 min (Sun 1:00 pm ET)")
    assert deadline_status(kickoff, datetime(2026, 9, 10, 12, 0, tzinfo=ET)) == ("later", "by Sun 1:00 pm ET")


def test_missed_players_separates_locked_from_blocked():
    from lineup import missed_players

    maye, mahomes, stevenson, judkins = _lrow("1", "QB", 18, locked=True), _lrow("2", "QB", 19), _lrow("3", "RB", 14, locked=True), _lrow("4", "RB", 12)
    optimal = {"QB": [mahomes], "FLEX": [stevenson]}
    reachable = {"QB": [maye], "FLEX": [judkins]}
    missed = missed_players(optimal, reachable)
    assert [r["player_id"] for r in missed["locked"]] == ["3"] and [r["player_id"] for r in missed["blocked"]] == ["2"]
    assert missed_players(reachable, reachable) == {"locked": [], "blocked": []}


def test_swap_deadline_is_the_earlier_kickoff_of_the_two_players():
    early, late = {"gameday": "2026-09-13", "gametime": "13:00"}, {"gameday": "2026-09-13", "gametime": "16:25"}
    swap = {"in": dict(_row("1", "RB", 10), matchup=late), "out": dict(_row("2", "RB", 8), matchup=early)}
    assert swap_deadline(swap) == datetime(2026, 9, 13, 13, 0, tzinfo=ET)
    assert swap_deadline({"in": dict(_row("1", "RB", 10), matchup=late), "out": None}) == datetime(2026, 9, 13, 16, 25, tzinfo=ET)
    assert swap_deadline({"in": dict(_row("1", "RB", 10), matchup=None), "out": None}) is None
