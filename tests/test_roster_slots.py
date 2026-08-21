from roster_slots import allocate_slots, starters_from_roster_positions


def _p(pos, vor):
    return {"name": f"{pos}{vor}", "position": pos, "vor": vor}


def test_starters_from_roster_positions_collapses_slots(league):
    starters, bench = starters_from_roster_positions(league["roster_positions"])
    assert starters == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}
    assert bench == 5


def test_starters_ignores_idp_and_unknown_slots():
    starters, bench = starters_from_roster_positions(
        ["QB", "SUPER_FLEX", "REC_FLEX", "DL", "LB", "BN", "IR", "TAXI"]
    )
    assert starters == {"QB": 1, "SUPER_FLEX": 1, "REC_FLEX": 1}
    assert bench == 1


def test_empty_roster_needs_everything():
    starters = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}
    result = allocate_slots([], starters)
    assert result.needs == starters
    assert result.filled == 0
    assert result.total == 9
    assert result.bench == []


def test_third_rb_fills_flex_not_bench():
    starters = {"RB": 2, "WR": 1, "FLEX": 1}
    roster = [_p("RB", 50), _p("RB", 40), _p("RB", 30)]
    result = allocate_slots(roster, starters)
    assert result.needs == {"WR": 1}
    assert result.filled == 3
    assert result.bench == []
    assert result.slots["FLEX"][0]["vor"] == 30


def test_best_players_take_dedicated_slots_and_weakest_eligible_goes_to_flex():
    starters = {"RB": 1, "WR": 1, "FLEX": 1}
    roster = [_p("WR", 80), _p("RB", 20), _p("WR", 60), _p("RB", 10)]
    result = allocate_slots(roster, starters)
    assert result.slots["RB"][0]["vor"] == 20
    assert result.slots["WR"][0]["vor"] == 80
    assert result.slots["FLEX"][0]["vor"] == 60
    assert [p["vor"] for p in result.bench] == [10]


def test_qb_never_fills_a_standard_flex():
    starters = {"QB": 1, "FLEX": 1}
    roster = [_p("QB", 90), _p("QB", 70)]
    result = allocate_slots(roster, starters)
    assert result.needs == {"FLEX": 1}
    assert len(result.bench) == 1


def test_superflex_accepts_qb():
    starters = {"QB": 1, "SUPER_FLEX": 1}
    roster = [_p("QB", 90), _p("QB", 70)]
    result = allocate_slots(roster, starters)
    assert result.needs == {}


def test_most_restrictive_flex_is_filled_first():
    starters = {"QB": 1, "SUPER_FLEX": 1, "FLEX": 1}
    roster = [_p("QB", 100), _p("QB", 40), _p("RB", 60)]
    result = allocate_slots(roster, starters)
    assert result.needs == {}
    assert result.slots["FLEX"][0]["position"] == "RB"
    assert result.slots["SUPER_FLEX"][0]["vor"] == 40


def test_missing_positions_lists_unfilled_slots_once():
    starters = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}
    roster = [_p("RB", 50)]
    result = allocate_slots(roster, starters)
    assert result.missing == ["QB", "RB", "WR", "TE", "FLEX", "K", "DEF"]


def test_allocation_does_not_mutate_inputs():
    starters = {"RB": 1, "FLEX": 1}
    roster = [_p("RB", 50), _p("RB", 40)]
    before = [dict(p) for p in roster]
    allocate_slots(roster, starters)
    assert roster == before
