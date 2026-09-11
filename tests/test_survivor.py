import json

import pytest

from survivor import (
    DEFAULT_POOLS, add_pool, default_pools, grade_picks, load_pools, pool_status, record_pick, remove_pick, remove_pool, rename_pool,
    save_pools, used_teams,
)


def _game(week, home, away, scores=None):
    home_score, away_score = scores if scores else (None, None)
    return {"week": week, "home": home, "away": away, "neutral": False, "total": None, "spread_line": None, "roof": None,
            "gameday": "2026-09-13", "gametime": "13:00", "home_score": home_score, "away_score": away_score, "home_moneyline": None, "away_moneyline": None}


def test_pools_round_trip_and_week_keys_come_back_as_ints(tmp_path):
    path = tmp_path / "survivor.json"
    pools = record_pick(default_pools(), DEFAULT_POOLS[0], 1, "KC")
    save_pools(path, "2026", pools)
    assert load_pools(path, "2026") == pools and load_pools(path, "2026")[DEFAULT_POOLS[0]]["picks"] == {1: "KC"}
    assert json.load(open(path))["pools"][DEFAULT_POOLS[0]]["picks"] == {"1": "KC"}
    assert load_pools(path, "2027") == default_pools()  # a new season starts clean
    assert load_pools(tmp_path / "missing.json", "2026") == default_pools()
    path.write_text("{not json")
    assert load_pools(path, "2026") == default_pools()


def test_record_pick_replaces_the_week_and_rejects_a_team_used_elsewhere():
    pools = record_pick(default_pools(), "CBS pool 1", 1, "KC")
    pools = record_pick(pools, "CBS pool 1", 1, "BUF")
    assert pools["CBS pool 1"]["picks"] == {1: "BUF"}
    pools = record_pick(pools, "CBS pool 1", 2, "KC")
    with pytest.raises(ValueError):
        record_pick(pools, "CBS pool 1", 3, "BUF")
    assert record_pick(pools, "CBS pool 2", 3, "BUF")["CBS pool 2"]["picks"] == {3: "BUF"}  # the other pool is free to use it
    assert remove_pick(pools, "CBS pool 1", 2)["CBS pool 1"]["picks"] == {1: "BUF"}
    assert pools["CBS pool 1"]["picks"] == {1: "BUF", 2: "KC"}  # nothing mutated
    assert used_teams({1: "BUF", 2: "KC"}) == frozenset({"BUF", "KC"}) and used_teams({1: "BUF", 2: "KC"}, except_week=2) == frozenset({"BUF"})


def test_add_rename_and_remove_pools_keep_order_and_reject_duplicates():
    pools = add_pool(default_pools(), "Office")
    assert list(pools) == ["CBS pool 1", "CBS pool 2", "Office"]
    pools = rename_pool(record_pick(pools, "Office", 1, "KC"), "Office", "Work")
    assert list(pools) == ["CBS pool 1", "CBS pool 2", "Work"] and pools["Work"]["picks"] == {1: "KC"}
    with pytest.raises(ValueError):
        add_pool(pools, "Work")
    with pytest.raises(ValueError):
        add_pool(pools, "   ")
    with pytest.raises(ValueError):
        rename_pool(pools, "Work", "CBS pool 1")
    assert list(remove_pool(pools, "CBS pool 2")) == ["CBS pool 1", "Work"]


def test_grade_picks_and_pool_status():
    games = [_game(1, "KC", "DEN", (27, 17)), _game(1, "NE", "SEA", (20, 20)), _game(2, "BUF", "NYJ", (10, 24)), _game(2, "PHI", "DAL", (30, 3)),
             _game(3, "KC", "LV")]
    picks = {1: "KC", 2: "BUF", 3: "KC", 4: "PHI"}
    graded = grade_picks(picks, games)
    assert graded[1] == {"team": "KC", "opponent": "DEN", "site": "home", "result": "won", "score": "27-17", "week_over": True}
    assert graded[2]["result"] == "lost" and graded[2]["site"] == "home" and graded[2]["score"] == "10-24"
    assert graded[3]["result"] == "pending" and graded[3]["score"] is None and graded[3]["week_over"] is False
    assert graded[4]["result"] == "no game" and graded[4]["opponent"] is None
    assert pool_status(graded) == {"alive": False, "week": 2, "team": "BUF"}
    assert pool_status({1: graded[1], 3: graded[3]}) == {"alive": True, "week": None, "team": None}
    tie = grade_picks({1: "NE"}, games)[1]
    assert tie["result"] == "lost"  # a straight-up pool: a tie is not a win


@pytest.mark.parametrize("payload", ['{"season": "2026", "pools": {"A": {"picks": {"wk1": "KC"}}}}', '{"season": "2026", "pools": {"A": {"picks": ["KC"]}}}',
                                     '{"season": "2026", "pools": {"A": null}}', '{"season": "2026", "pools": []}', '[]', '{"season": "2026"}'])
def test_a_malformed_file_gives_the_defaults_because_it_loads_in_every_mode(tmp_path, payload):
    path = tmp_path / "survivor.json"
    path.write_text(payload)
    assert load_pools(path, "2026") == default_pools()
