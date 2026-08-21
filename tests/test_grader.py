from grader import grade_draft

STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}


def _p(pos, vor):
    return {"name": f"{pos}{vor}", "position": pos, "vor": vor}


def test_empty_roster_has_no_grade():
    assert grade_draft([], STARTERS) is None


def test_flex_counts_as_filled_by_third_rb():
    roster = [_p("RB", 30), _p("RB", 20), _p("RB", 10)]
    grade = grade_draft(roster, STARTERS)
    assert grade["slots_filled"] == 3
    assert grade["slots_total"] == 9
    assert "FLEX" not in grade["missing"]
    assert grade["missing"] == ["QB", "WR", "TE", "K", "DEF"]


def test_full_high_value_roster_is_an_a():
    roster = [
        _p("QB", 40), _p("RB", 60), _p("RB", 40), _p("WR", 50), _p("WR", 30),
        _p("TE", 25), _p("WR", 20), _p("K", 5), _p("DEF", 5),
    ]
    grade = grade_draft(roster, STARTERS)
    assert grade["letter"] == "A"
    assert grade["missing"] == []


def test_complete_roster_with_no_value_is_a_d_not_an_a():
    roster = [_p(pos, 0) for pos in ("QB", "RB", "RB", "WR", "WR", "TE", "WR", "K", "DEF")]
    grade = grade_draft(roster, STARTERS)
    assert grade["letter"] == "D"
    assert grade["score"] == 60


def test_value_component_discriminates_between_average_and_elite():
    average = [_p(pos, 11) for pos in ("QB", "RB", "RB", "WR", "WR", "TE", "WR", "K", "DEF")]
    elite = [_p(pos, 25) for pos in ("QB", "RB", "RB", "WR", "WR", "TE", "WR", "K", "DEF")]
    assert grade_draft(average, STARTERS)["score"] < grade_draft(elite, STARTERS)["score"]
    assert grade_draft(average, STARTERS)["letter"] == "B"


def test_negative_value_roster_never_scores_below_zero():
    roster = [_p("RB", -150)]
    assert grade_draft(roster, STARTERS)["score"] >= 0


def test_one_great_pick_is_not_an_a_because_roster_is_incomplete():
    grade = grade_draft([_p("RB", 150)], STARTERS)
    assert grade["letter"] in {"C", "D", "F"}
    assert grade["slots_filled"] == 1
