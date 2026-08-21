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
