from draft_state import load_state, save_state


def test_round_trip(tmp_path):
    path = tmp_path / "state.json"
    save_state(path, "draft-1", drafted={"a|X|RB", "b|Y|WR"}, mine=["b|Y|WR"])
    state = load_state(path, "draft-1")
    assert state == {"drafted": {"a|X|RB", "b|Y|WR"}, "mine": ["b|Y|WR"]}


def test_state_for_another_draft_is_ignored(tmp_path):
    path = tmp_path / "state.json"
    save_state(path, "draft-1", drafted={"a|X|RB"}, mine=[])
    assert load_state(path, "draft-2") is None


def test_missing_or_corrupt_file_is_none(tmp_path):
    path = tmp_path / "state.json"
    assert load_state(path, "draft-1") is None
    path.write_text("{not json")
    assert load_state(path, "draft-1") is None


def test_empty_state_removes_the_file(tmp_path):
    path = tmp_path / "state.json"
    save_state(path, "draft-1", drafted={"a|X|RB"}, mine=[])
    save_state(path, "draft-1", drafted=set(), mine=[])
    assert not path.exists()
