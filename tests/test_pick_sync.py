from pick_sync import apply_picks, index_board_by_player_id, next_pick_info

ME = "460869683914993664"


def _board(projections):
    board = []
    for pos, rows in projections.items():
        for r in rows:
            p = r["player"]
            board.append(
                {
                    "name": f"{p['first_name']} {p['last_name']}",
                    "team": p["team"],
                    "position": pos,
                    "player_id": r["player_id"],
                    "vor": 10.0,
                }
            )
    return board


def test_apply_picks_splits_taken_and_mine(projections, picks):
    index = index_board_by_player_id(_board(projections))
    result = apply_picks(picks, index, my_user_id=ME, my_roster_id=3)
    taken_names = [p["name"] for p in result.taken]
    assert taken_names == ["Jahmyr Gibbs", "Bijan Robinson", "Josh Allen", "Puka Nacua"]
    assert [p["name"] for p in result.mine] == ["Josh Allen", "Puka Nacua"]


def test_without_roster_id_only_picked_by_counts(projections, picks):
    index = index_board_by_player_id(_board(projections))
    result = apply_picks(picks, index, my_user_id=ME, my_roster_id=None)
    assert [p["name"] for p in result.mine] == ["Josh Allen"]


def test_autopick_with_empty_picked_by_is_matched_via_roster_id(projections, picks):
    index = index_board_by_player_id(_board(projections))
    result = apply_picks(picks, index, my_user_id=ME, my_roster_id=3)
    assert "Puka Nacua" in [p["name"] for p in result.mine]


def test_pick_i_made_for_another_roster_is_not_mine(projections, picks):
    """Commissioner picking on behalf of an absent manager: roster_id wins."""
    index = index_board_by_player_id(_board(projections))
    proxy = [dict(picks[0], picked_by=ME, roster_id=7)]
    result = apply_picks(proxy, index, my_user_id=ME, my_roster_id=3)
    assert result.mine == []
    assert len(result.taken) == 1


def test_next_pick_info_linear_does_not_snake(draft):
    d = {**draft, "type": "linear"}
    d["draft_order"] = {ME: 1, "u2": 2}
    d["settings"] = {**draft["settings"], "teams": 2, "rounds": 4}
    # Pick 3 starts round 2; in a linear draft slot 1 (me) is up again.
    info = next_pick_info(d, picks_made=2, my_user_id=ME)
    assert info["on_clock_slot"] == 1 and info["picks_until_mine"] == 0


def test_next_pick_info_unsupported_for_third_round_reversal_or_auction(draft):
    d = dict(draft)
    d["draft_order"] = {ME: 1, "u2": 2}
    d["settings"] = {**draft["settings"], "teams": 2, "rounds": 4, "reversal_round": 3}
    assert next_pick_info(d, picks_made=2, my_user_id=ME) is None
    assert next_pick_info({**d, "type": "auction"}, picks_made=0, my_user_id=ME) is None


def test_unknown_player_ids_are_reported_not_dropped_silently(projections, picks):
    index = index_board_by_player_id(_board(projections))
    result = apply_picks(picks, index, my_user_id=ME, my_roster_id=3)
    assert len(result.unmatched) == 1
    assert result.unmatched[0]["player_id"] == "999999"


def test_next_pick_info_is_none_until_draft_order_is_set(draft):
    assert draft["draft_order"] is None
    assert next_pick_info(draft, picks_made=0, my_user_id=ME) is None


def test_next_pick_info_snake_math(draft):
    d = dict(draft)
    d["draft_order"] = {ME: 3, "u1": 1, "u2": 2}
    d["settings"] = {**draft["settings"], "teams": 12, "rounds": 14}
    # Pick 1 is on the clock, I'm slot 3 of 12: two picks until mine
    info = next_pick_info(d, picks_made=0, my_user_id=ME)
    assert info == {"my_slot": 3, "on_clock_slot": 1, "on_clock_user_id": "u1", "picks_until_mine": 2, "round": 1}
    # After my pick 3, round 1 has 9 more picks, round 2 snakes back: slot 12..1,
    # so my next turn is round 2 pick 10 (overall 22), 18 picks away from pick 4.
    info = next_pick_info(d, picks_made=3, my_user_id=ME)
    assert info["picks_until_mine"] == 18
    assert info["round"] == 1
    # On the clock myself
    info = next_pick_info(d, picks_made=2, my_user_id=ME)
    assert info["picks_until_mine"] == 0
    assert info["on_clock_user_id"] == ME


def test_next_pick_info_after_final_pick(draft):
    d = dict(draft)
    d["draft_order"] = {ME: 1}
    d["settings"] = {**draft["settings"], "teams": 2, "rounds": 1}
    assert next_pick_info(d, picks_made=2, my_user_id=ME) is None
