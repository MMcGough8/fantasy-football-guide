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
