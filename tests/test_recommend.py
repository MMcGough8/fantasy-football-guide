from recommend import recommend_pick, LATE_ROUND_WINDOW

STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}
TOTAL_PICKS = 14


def _p(name, pos, vor):
    return {"name": name, "position": pos, "vor": vor}


def test_need_bonus_lifts_a_needed_position_over_slightly_better_value():
    available = [_p("WR A", "WR", 50), _p("RB A", "RB", 45)]
    needs = {"RB": 1}
    pick, reason = recommend_pick(available, needs, roster_size=3, total_picks=TOTAL_PICKS)
    assert pick["name"] == "RB A"
    assert reason == "fills a need at RB"


def test_flex_need_counts_for_rb_wr_te():
    available = [_p("QB A", "QB", 40), _p("TE A", "TE", 30)]
    needs = {"FLEX": 1}
    pick, reason = recommend_pick(available, needs, roster_size=6, total_picks=TOTAL_PICKS)
    assert pick["name"] == "TE A"


def test_kicker_and_defense_are_ignored_until_the_final_picks():
    # Round 8: skill slots full, only K and DEF open, kicker has the best adjusted value
    available = [_p("K A", "K", 15), _p("DEF A", "DEF", 23), _p("WR depth", "WR", 12)]
    needs = {"K": 1, "DEF": 1}
    pick, reason = recommend_pick(available, needs, roster_size=7, total_picks=TOTAL_PICKS)
    assert pick["name"] == "WR depth"
    assert reason == "best value on the board"


def test_kicker_and_defense_become_urgent_in_the_final_picks():
    available = [_p("WR depth", "WR", 30), _p("DEF A", "DEF", 23), _p("K A", "K", 15)]
    needs = {"K": 1, "DEF": 1}
    roster_size = TOTAL_PICKS - LATE_ROUND_WINDOW  # first pick inside the window
    pick, reason = recommend_pick(available, needs, roster_size=roster_size, total_picks=TOTAL_PICKS)
    assert pick["name"] == "DEF A"
    assert reason == "fills a need at DEF"
    # Next pick: still need K, so K is urgent
    pick, _ = recommend_pick(
        [_p("WR depth", "WR", 30), _p("K A", "K", 15)], {"K": 1},
        roster_size=roster_size + 1, total_picks=TOTAL_PICKS,
    )
    assert pick["name"] == "K A"


def test_filled_kicker_slot_is_not_recommended_again_late():
    available = [_p("WR depth", "WR", 10), _p("K B", "K", 14)]
    pick, _ = recommend_pick(available, needs={}, roster_size=13, total_picks=TOTAL_PICKS)
    assert pick["name"] == "WR depth"


def test_empty_board_returns_none():
    assert recommend_pick([], {"RB": 2}, roster_size=0, total_picks=TOTAL_PICKS) == (None, "")


def test_only_kickers_left_early_still_returns_something():
    available = [_p("K A", "K", 15)]
    pick, _ = recommend_pick(available, {"K": 1}, roster_size=2, total_picks=TOTAL_PICKS)
    assert pick["name"] == "K A"


def test_position_caps_block_a_third_quarterback():
    available = [_p("QB C", "QB", 40), _p("WR D", "WR", -5)]
    counts = {"QB": 2, "RB": 4, "WR": 2, "TE": 1, "K": 0, "DEF": 0}
    pick, _ = recommend_pick(available, needs={}, roster_size=9, total_picks=TOTAL_PICKS, roster_counts=counts)
    assert pick["name"] == "WR D"


def test_position_caps_prefer_wr3_over_backup_te():
    available = [_p("TE B", "TE", 14), _p("WR C", "WR", -12)]
    counts = {"QB": 1, "RB": 3, "WR": 2, "TE": 2}
    pick, _ = recommend_pick(available, needs={}, roster_size=8, total_picks=TOTAL_PICKS, roster_counts=counts)
    assert pick["name"] == "WR C"


def test_caps_fall_back_when_everything_is_capped():
    available = [_p("QB C", "QB", 40)]
    counts = {"QB": 2}
    pick, _ = recommend_pick(available, needs={}, roster_size=9, total_picks=TOTAL_PICKS, roster_counts=counts)
    assert pick["name"] == "QB C"


def test_ir_and_pup_players_are_never_recommended():
    available = [
        dict(_p("WR IR", "WR", 90), injury_status="IR"),
        dict(_p("TE PUP", "TE", 80), injury_status="PUP"),
        dict(_p("RB Sus", "RB", 70), injury_status="Sus"),
        dict(_p("WR Q", "WR", 30), injury_status="Questionable"),
    ]
    pick, _ = recommend_pick(available, {"WR": 2}, roster_size=1, total_picks=TOTAL_PICKS)
    assert pick["name"] == "WR Q"


def test_questionable_is_still_eligible():
    available = [dict(_p("RB Q", "RB", 50), injury_status="Questionable"), _p("RB B", "RB", 40)]
    pick, _ = recommend_pick(available, {"RB": 1}, roster_size=0, total_picks=TOTAL_PICKS)
    assert pick["name"] == "RB Q"


def test_second_qb_waits_until_the_late_rounds():
    available = [_p("QB B", "QB", 12), _p("WR E", "WR", -20)]
    counts = {"QB": 1, "RB": 3, "WR": 2, "TE": 1, "K": 0, "DEF": 0}
    # round 8: the backup QB has more VOR but never starts; depth WR wins
    pick, _ = recommend_pick(available, needs={}, roster_size=7, total_picks=TOTAL_PICKS, roster_counts=counts)
    assert pick["name"] == "WR E"
    # round 11: the backup is allowed
    pick, _ = recommend_pick(available, needs={}, roster_size=10, total_picks=TOTAL_PICKS, roster_counts=counts)
    assert pick["name"] == "QB B"


def test_second_te_waits_too_but_first_te_does_not():
    available = [_p("TE B", "TE", 10), _p("RB F", "RB", -15)]
    pick, _ = recommend_pick(available, needs={}, roster_size=7, total_picks=TOTAL_PICKS, roster_counts={"TE": 1})
    assert pick["name"] == "RB F"
    pick, _ = recommend_pick(available, needs={"TE": 1}, roster_size=7, total_picks=TOTAL_PICKS, roster_counts={"TE": 0})
    assert pick["name"] == "TE B"


def test_all_capped_fallback_still_keeps_kickers_out_early():
    available = [_p("QB C", "QB", 40), _p("K A", "K", 15)]
    pick, _ = recommend_pick(available, needs={"K": 1}, roster_size=5, total_picks=TOTAL_PICKS, roster_counts={"QB": 2})
    assert pick["name"] == "QB C"


def test_rank_candidates_returns_a_scored_shortlist():
    from recommend import rank_candidates

    available = [_p("RB A", "RB", 60), _p("WR A", "WR", 55), _p("TE A", "TE", 30), _p("QB A", "QB", 20)]
    ranked = rank_candidates(available, needs={"WR": 1}, roster_size=2, total_picks=TOTAL_PICKS)
    names = [c["player"]["name"] for c in ranked]
    assert names[:2] == ["WR A", "RB A"]  # WR fills a need: 55 + 30 beats 60
    top = ranked[0]
    assert top["fills_need"] is True
    assert top["adjusted"] == 85
    assert top["mode"] == "value"
    assert all(c["reach"] == 1.0 for c in ranked)


def test_rank_candidates_market_mode_reports_reach_and_mode():
    from recommend import rank_candidates

    available = [dict(_p("Star", "WR", 150), adp=5), dict(_p("Reachable", "WR", 60), adp=40)]
    between = rank_candidates(available, {"WR": 2}, 1, TOTAL_PICKS, picks_made=6, gap=18, reach_gap=10)
    assert between[0]["player"]["name"] == "Reachable" and between[0]["mode"] == "reach"
    assert 0 < between[0]["reach"] <= 1
    on_clock = rank_candidates(available, {"WR": 2}, 1, TOTAL_PICKS, picks_made=6, gap=18, reach_gap=0)
    assert on_clock[0]["player"]["name"] == "Star" and on_clock[0]["mode"] == "now"
    assert on_clock[0]["back"] < 0.1


def test_held_reason_explains_why_a_better_player_is_skipped():
    from recommend import held_reason

    counts = {"QB": 1, "TE": 1, "RB": 2, "WR": 2}
    needs = {"K": 1, "DEF": 1}
    assert "backup QB" in held_reason(_p("QB B", "QB", 32), counts, roster_size=7, total_picks=14, needs=needs)
    assert "backup TE" in held_reason(_p("TE B", "TE", 20), counts, roster_size=7, total_picks=14, needs=needs)
    assert "last 3 picks" in held_reason(_p("DEF A", "DEF", 27), counts, roster_size=7, total_picks=14, needs=needs)
    assert held_reason(_p("WR C", "WR", 10), counts, roster_size=7, total_picks=14, needs=needs) is None
    assert "cap" in held_reason(_p("QB C", "QB", 30), {"QB": 2}, roster_size=12, total_picks=14, needs={})
    assert "IR" in held_reason(dict(_p("RB X", "RB", 50), injury_status="IR"), {}, 0, 14, {"RB": 2})


def test_headline_gate_is_stricter_than_the_candidate_gate():
    from recommend import rank_candidates, HEADLINE_REACH

    assert HEADLINE_REACH > 0.5
    available = [dict(_p("Coin flip", "WR", 90), adp=20), dict(_p("Safe", "WR", 60), adp=60)]
    loose = rank_candidates(available, {"WR": 2}, 1, 14, picks_made=18, gap=18, reach_gap=6, limit=1)
    strict = rank_candidates(available, {"WR": 2}, 1, 14, picks_made=18, gap=18, reach_gap=6, limit=1, min_reach=HEADLINE_REACH)
    assert loose[0]["player"]["name"] == "Coin flip"
    assert strict[0]["player"]["name"] == "Safe"
