import pytest

from manual_draft import build_draft, draft_info, pick_positions
from draft_state import load_state, save_state


def test_build_draft_is_sleeper_shaped_snake():
    d = build_draft(teams=10, rounds=15, slots={"Marc": 4, "Amy": 9})
    assert d["type"] == "snake"
    assert d["draft_order"] == {"Marc": 4, "Amy": 9}
    assert d["slot_to_roster_id"]["10"] == 10
    assert d["settings"] == {"teams": 10, "rounds": 15}


def test_build_draft_rejects_bad_slots():
    with pytest.raises(ValueError):
        build_draft(10, 15, {"Marc": 11})
    with pytest.raises(ValueError):
        build_draft(1, 15, {"Marc": 1})


def test_draft_info_counts_picks_and_gives_each_team_its_own_clock():
    d = build_draft(teams=10, rounds=15, slots={"Marc": 4, "Amy": 9})
    log = [{"key": f"p{i}", "team": None} for i in range(3)]
    marc = draft_info(d, log, "Marc", lambda key: "RB")
    amy = draft_info(d, log, "Amy", lambda key: "RB")
    assert marc["status"] == "drafting" and marc["picks"] == 3 and marc["rounds"] == 15
    assert marc["next"]["picks_until_mine"] == 0  # pick 4 is on the clock
    assert amy["next"]["picks_until_mine"] == 5  # picks 4..8 before slot 9
    assert marc["next"]["on_clock_user_id"] == "Marc"
    assert marc["draft_meta"] is d and marc["manual"] is True


def test_draft_info_before_any_pick_is_pre_draft_with_an_order():
    d = build_draft(teams=10, rounds=15, slots={"Marc": 4})
    info = draft_info(d, [], "Marc", lambda key: None)
    assert info["status"] == "pre_draft" and info["has_order"] and info["next"]["picks_until_mine"] == 3


def test_pick_positions_follow_the_snake():
    log = [{"key": f"p{i}", "team": None} for i in range(12)]
    pos = pick_positions(log, teams=10, position_of=lambda key: "WR" if key == "p10" else "RB")
    assert [e["roster_id"] for e in pos[:10]] == list(range(1, 11))
    assert pos[10]["roster_id"] == 10 and pos[10]["metadata"]["position"] == "WR"  # round 2 reverses
    assert pos[11]["roster_id"] == 9


def test_state_round_trips_rosters_and_pick_log(tmp_path):
    path = tmp_path / "state.json"
    log = [{"key": "a|X|RB", "team": "Marc"}, {"key": "b|Y|WR", "team": None}]
    save_state(path, "manual:1", drafted={"a|X|RB", "b|Y|WR"}, mine=["a|X|RB"],
               rosters={"Marc": ["a|X|RB"], "Amy": []}, pick_log=log)
    loaded = load_state(path, "manual:1")
    assert loaded["drafted"] == {"a|X|RB", "b|Y|WR"} and loaded["mine"] == ["a|X|RB"]
    assert loaded["rosters"] == {"Marc": ["a|X|RB"], "Amy": []} and loaded["pick_log"] == log


def test_state_without_the_new_fields_still_loads(tmp_path):
    path = tmp_path / "state.json"
    save_state(path, "d1", drafted={"a|X|RB"}, mine=["a|X|RB"])
    loaded = load_state(path, "d1")
    assert loaded["rosters"] == {} and loaded["pick_log"] == []


def test_state_with_only_a_pick_log_is_kept(tmp_path):
    path = tmp_path / "state.json"
    save_state(path, "d1", drafted=set(), mine=[], pick_log=[{"key": "a|X|RB", "team": None}])
    assert path.exists()


def test_draft_info_reports_complete_when_the_owner_ends_the_draft():
    from manual_draft import build_draft, draft_info

    draft = build_draft(4, 2, {"Marc": 1, "Wife": 3})
    log = [{"key": "a|A|RB", "team": "Marc"}, {"key": "b|B|WR", "team": None}]
    ended = draft_info(draft, log, "Marc", lambda k: "RB", complete=True)
    assert ended["status"] == "complete" and ended["picks"] == 2 and ended["manual"] is True
    assert draft_info(draft, log, "Marc", lambda k: "RB")["status"] == "drafting"
