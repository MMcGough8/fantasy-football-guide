from conftest import load_fixture
from waivers import (
    drop_candidates, faab_left, free_agents, lineup_gain, parse_trending, rostered_ids, waiver_settings, waiver_targets,
)


def _row(pid, pos, points, status=None, team="X"):
    return {"player_id": pid, "name": f"{pos}{pid}", "position": pos, "team": team, "points": points,
            "injury_status": status, "reason": None}


def test_rostered_ids_include_reserve_and_taxi_and_tolerate_none():
    rosters = [{"players": ["1", "2"], "reserve": ["3"], "taxi": None}, {"players": None, "reserve": None, "taxi": ["4"]}]
    assert rostered_ids(rosters) == {"1", "2", "3", "4"}
    assert {"4866", "HOU", "7564"} <= rostered_ids(load_fixture("rosters_sample.json"))


def test_free_agents_are_pool_rows_nobody_rosters():
    pool = {"1": _row("1", "RB", 10), "2": _row("2", "WR", 9), "HOU": _row("HOU", "DEF", 7)}
    free = free_agents(pool, [{"players": ["1"], "reserve": None, "taxi": None}])
    assert [r["player_id"] for r in free] == ["2", "HOU"]


def test_lineup_gain_is_the_improvement_to_the_best_lineup():
    mine = [_row("1", "WR", 12), _row("2", "WR", 6), _row("3", "RB", 10)]
    starters = {"WR": 2, "RB": 1}
    assert lineup_gain(_row("9", "WR", 9), mine, starters) == 3.0  # replaces the 6-point WR2
    assert lineup_gain(_row("8", "WR", 5), mine, starters) == 0.0
    assert lineup_gain(_row("7", "K", 8), mine, starters) == 0.0  # no K slot
    assert lineup_gain(_row("6", "RB", 11, status="Out"), mine, starters) == 0.0  # cannot play this week
    assert lineup_gain(_row("6", "RB", 11, status="Out"), mine, starters, respect_injury=False) == 1.0
    mine_adjusted = [dict(r, adjusted_points=r["points"]) for r in mine]
    candidate = dict(_row("5", "RB", 20), adjusted_points=9.0)
    assert lineup_gain(candidate, mine_adjusted, starters) == 10.0
    assert lineup_gain(candidate, mine_adjusted, starters, key="adjusted_points") == 0.0  # ranked by the page's key


def test_waiver_targets_split_season_adds_from_streamers_and_keep_k_def_weekly():
    week_mine = [_row("1", "WR", 12), _row("2", "WR", 6), _row("3", "RB", 10), _row("K1", "K", 7)]
    season_mine = [_row("1", "WR", 200), _row("2", "WR", 120), _row("3", "RB", 180), _row("K1", "K", 130)]
    starters = {"WR": 2, "RB": 1, "K": 1}
    free = [_row("9", "WR", 9), _row("8", "WR", 11), _row("K2", "K", 9), _row("7", "RB", 4)]
    season_index = {"9": _row("9", "WR", 150), "8": _row("8", "WR", 100), "K2": _row("K2", "K", 140), "7": _row("7", "RB", 90)}
    targets = waiver_targets(free, week_mine, season_mine, season_index, starters, "points", {"9": 500}, limit=8)
    season = [(t["row"]["player_id"], t["season_gain"], t["week_gain"], t["adds"]) for t in targets["season"]]
    assert season == [("9", 30.0, 3.0, 500)]  # WR9 beats WR2 over the season; WR8 does not
    assert targets["season"][0]["displaces"]["player_id"] == "2"  # the WR2 he pushes out of this week's lineup
    week = [(t["row"]["player_id"], t["week_gain"]) for t in targets["week"]]
    assert week == [("8", 5.0), ("K2", 2.0)]  # WR8 is a streamer; a K is only ever a weekly play
    assert targets["season"][0]["row"]["points"] == 9  # entries carry the weekly row


def test_displaces_covers_a_flex_cascade_and_an_empty_slot():
    week_mine = [_row("1", "RB", 10), _row("2", "WR", 12), _row("3", "WR", 5)]
    targets = waiver_targets([_row("9", "RB", 9)], week_mine, [], {}, {"RB": 1, "WR": 1, "FLEX": 1}, "points", {})
    assert targets["week"][0]["displaces"]["player_id"] == "3"  # the RB takes the FLEX and pushes the weak WR out
    empty = waiver_targets([_row("8", "TE", 6)], week_mine, [], {}, {"RB": 1, "WR": 1, "TE": 1}, "points", {})
    assert empty["week"][0]["displaces"] is None  # an empty TE slot displaces nobody


def test_waiver_targets_respect_the_limit_and_missing_season_rows():
    week_mine = [_row("1", "WR", 12), _row("2", "WR", 6)]
    season_mine = [_row("1", "WR", 200), _row("2", "WR", 120)]
    free = [_row("9", "WR", 9), _row("8", "WR", 11)]
    targets = waiver_targets(free, week_mine, season_mine, {}, {"WR": 2}, "points", {}, limit=1)
    assert targets["season"] == []  # nobody has a season row
    assert [t["row"]["player_id"] for t in targets["week"]] == ["8"]  # top 1 by week gain
    assert targets["week"][0]["season_gain"] == 0.0 and targets["week"][0]["adds"] == 0


def test_drop_candidates_are_outside_both_lineups_lowest_season_value_first():
    week_mine = [_row("1", "WR", 12), _row("2", "WR", 6), _row("3", "WR", 8), _row("4", "WR", 2),
                 _row("5", "WR", 14, status="IR"), _row("6", "WR", 1)]
    season_mine = [_row("1", "WR", 200), _row("2", "WR", 120), _row("3", "WR", 90), _row("4", "WR", 60),
                   _row("5", "WR", 210, status="IR")]
    drops = drop_candidates(week_mine, season_mine, {"WR": 2}, limit=3)
    # weekly lineup: 1 and 3 (5 is on IR); season lineup ignoring injury: 5 and 1; 5 is never a drop
    assert [(d["row"]["player_id"], d["season_points"]) for d in drops] == [("4", 60), ("2", 120), ("6", None)]
    assert len(drop_candidates(week_mine, season_mine, {"WR": 2}, limit=1)) == 1


def test_waiver_settings_and_faab(league):
    assert waiver_settings(league) == {"type": "FAAB", "budget": 100, "clear_days": 1}
    assert waiver_settings({"settings": {"waiver_type": 0}}) == {"type": "rolling", "budget": None, "clear_days": None}
    assert waiver_settings({"settings": {"waiver_type": 1}})["type"] == "reverse standings"
    assert waiver_settings({})["type"] == "unknown"
    assert faab_left({"settings": {"waiver_budget_used": 37}}, 100) == 63
    assert faab_left({"settings": None}, 100) == 100 and faab_left({}, None) is None


def test_parse_trending_maps_ids_to_counts():
    assert parse_trending([{"player_id": "9482", "count": 12}, {"player_id": 1, "count": 3}]) == {"9482": 12, "1": 3}
    assert parse_trending(None) == {}


def test_waiver_targets_respect_kickoff_locks_on_both_sides():
    maye = dict(_row("1", "QB", 18), locked=True)
    week_mine = [maye, _row("2", "RB", 10)]
    current = {"QB": [maye], "RB": [week_mine[1]]}
    starters = {"QB": 1, "RB": 1}
    murray = dict(_row("9", "QB", 20), locked=False)
    played = dict(_row("8", "RB", 30), locked=True)  # his game already kicked off
    targets = waiver_targets([murray, played], week_mine, [], {}, starters, "points", {}, current=current)
    assert targets["week"] == []  # Maye is frozen in the QB slot; the played RB cannot start this week
    free_qb = waiver_targets([murray], [dict(maye, locked=False), week_mine[1]], [], {}, starters, "points", {},
                             current={"QB": [dict(maye, locked=False)], "RB": [week_mine[1]]})
    assert [t["row"]["player_id"] for t in free_qb["week"]] == ["9"]


def test_waiver_targets_list_depth_upgrades_over_the_worst_bench_player():
    week_mine = [_row("1", "WR", 12), _row("2", "WR", 6), _row("3", "WR", 3)]
    season_mine = [_row("1", "WR", 200), _row("2", "WR", 120), _row("3", "WR", 60)]
    free = [_row("9", "WR", 5), _row("8", "WR", 4), _row("K2", "K", 9), _row("7", "WR", 2)]
    season_index = {"9": _row("9", "WR", 90), "8": _row("8", "WR", 61), "K2": _row("K2", "K", 140), "7": _row("7", "WR", 50)}
    targets = waiver_targets(free, week_mine, season_mine, season_index, {"WR": 2}, "points", {}, limit=8)
    assert targets["season"] == [] and targets["week"] == []
    depth = [(t["row"]["player_id"], t["season_points"], t["over"]["player_id"]) for t in targets["depth"]]
    assert depth == [("9", 90, "3"), ("8", 61, "3")]  # better than my worst droppable bench player; K never
    assert waiver_targets(free, week_mine, [], {}, {"WR": 2}, "points", {})["depth"] == []  # no season rows, no bench comparison


def test_season_and_depth_lists_exclude_free_agents_on_ir_or_pup_but_not_a_one_week_injury():
    week_mine = [_row("1", "WR", 12), _row("2", "WR", 6), _row("3", "WR", 3)]
    season_mine = [_row("1", "WR", 200), _row("2", "WR", 120), _row("3", "WR", 60)]
    free = [_row("9", "WR", 0, status="IR"), _row("8", "WR", 0, status="PUP"), _row("7", "WR", 0, status="Out"), _row("6", "WR", 2)]
    season_index = {"9": _row("9", "WR", 300, status="IR"), "8": _row("8", "WR", 250, status="PUP"),
                    "7": _row("7", "WR", 150, status="Out"), "6": _row("6", "WR", 70)}
    targets = waiver_targets(free, week_mine, season_mine, season_index, {"WR": 2}, "points", {})
    assert [t["row"]["player_id"] for t in targets["season"]] == ["7"]  # a one-week injury is still a season add
    assert [t["row"]["player_id"] for t in targets["depth"]] == ["6"]  # the IR and PUP stashes never appear


def test_drop_candidates_skip_pup_and_other_long_term_stashes():
    week_mine = [_row("1", "WR", 12), _row("2", "WR", 6), _row("3", "WR", 0, status="PUP"), _row("4", "WR", 1)]
    season_mine = [_row("1", "WR", 200), _row("2", "WR", 120), _row("3", "WR", 180, status="PUP"), _row("4", "WR", 30)]
    assert [d["row"]["player_id"] for d in drop_candidates(week_mine, season_mine, {"WR": 1})] == ["4", "2"]


def test_targets_flag_a_candidate_without_a_season_projection():
    targets = waiver_targets([_row("9", "WR", 9)], [_row("1", "WR", 12), _row("2", "WR", 6)], [], {}, {"WR": 2}, "points", {})
    assert targets["week"][0]["has_season"] is False
    assert parse_trending([{"player_id": "1"}]) == {"1": 0}


def test_depth_upgrades_and_drops_compare_value_over_replacement_not_raw_points():
    def season(pid, pos, points, vor):
        return dict(_row(pid, pos, points), vor=vor)

    week_mine = [_row("1", "WR", 12), _row("2", "WR", 6), _row("3", "TE", 3), _row("4", "RB", 2)]
    season_mine = [season("1", "WR", 200, 80), season("2", "WR", 120, 30), season("3", "TE", 60, 5), season("4", "RB", 90, 1)]
    free = [_row("9", "QB", 15), _row("8", "WR", 4)]
    season_index = {"9": season("9", "QB", 300, -20), "8": season("8", "WR", 80, 8)}  # a QB3's raw points mean nothing in a 1-QB league
    targets = waiver_targets(free, week_mine, season_mine, season_index, {"WR": 2}, "points", {})
    assert [(t["row"]["player_id"], t["season_value"]) for t in targets["depth"]] == [("8", 8)]
    assert targets["depth"][0]["over"]["player_id"] == "4" and targets["depth"][0]["season_points"] == 80
    drops = drop_candidates(week_mine, season_mine, {"WR": 2})
    assert [(d["row"]["player_id"], d["season_value"]) for d in drops] == [("4", 1), ("3", 5)]  # lowest VOR first, not lowest points
