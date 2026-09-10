import json

import pytest

import projection_log
from projection_log import (
    accuracy_report, append_records, fetch_actual_points, is_logged, log_actuals, log_projections,
    pair_records, projection_records, read_records,
)

LEAGUE = "1400991268770734080"


def _pool():
    return {
        "4866": {"name": "Saquon Barkley", "position": "RB", "team": "PHI", "opponent": "GB", "points": 19.2,
                 "points_by_source": {"sleeper": 19.2, "fp": 18.0, "espn": 20.0}, "adjusted_points": 20.1, "fp_week_rank": 3,
                 "matchup": {"implied": 25.0, "dvp_factor": 1.1, "site": "away"}},
        "HOU": {"name": "Houston Texans", "position": "DEF", "team": "HOU", "opponent": "IND", "points": 7.5,
                "points_by_source": {"sleeper": 7.5}, "adjusted_points": 7.5, "fp_week_rank": None, "matchup": None},
    }


def test_records_round_trip_and_bad_lines_are_skipped(tmp_path):
    path = tmp_path / "log.jsonl"
    assert read_records(path) == []
    append_records(path, [{"kind": "actual", "player_id": "1", "actual": 3.0}])
    with open(path, "a") as f:
        f.write("not json\n")
    append_records(path, [{"kind": "actual", "player_id": "2", "actual": 4.0}])
    assert [r["player_id"] for r in read_records(path)] == ["1", "2"]


def test_projection_records_capture_sources_blend_adjustment_and_matchup():
    recs = projection_records("2026", 3, LEAGUE, "HALF", _pool(), now="2026-09-16T10:00:00")
    barkley = next(r for r in recs if r["player_id"] == "4866")
    assert barkley["kind"] == "projection" and barkley["blend"] == 19.2 and barkley["adjusted"] == 20.1
    assert barkley["points_by_source"]["fp"] == 18.0 and barkley["implied"] == 25.0 and barkley["site"] == "away"
    assert barkley["logged_at"] == "2026-09-16T10:00:00" and barkley["scoring_code"] == "HALF"
    texans = next(r for r in recs if r["player_id"] == "HOU")
    assert texans["implied"] is None and texans["dvp_factor"] is None


def test_log_projections_writes_once_per_league_week(tmp_path):
    path = tmp_path / "log.jsonl"
    assert log_projections(path, "2026", 3, LEAGUE, "HALF", _pool()) == 2
    assert log_projections(path, "2026", 3, LEAGUE, "HALF", _pool()) == 0
    assert log_projections(path, "2026", 4, LEAGUE, "HALF", _pool()) == 2
    records = read_records(path)
    assert is_logged(records, "projection", "2026", 3, LEAGUE) and not is_logged(records, "actual", "2026", 3, LEAGUE)
    marker = next(r for r in records if r["kind"] == "logged")
    assert marker["what"] == "projection" and marker["count"] == 2 and "coefficients" in marker


def test_log_actuals_writes_once(tmp_path):
    path = tmp_path / "log.jsonl"
    assert log_actuals(path, "2026", 3, LEAGUE, {"4866": 24.0, "HOU": 9.0}) == 2
    assert log_actuals(path, "2026", 3, LEAGUE, {"4866": 24.0}) == 0
    assert is_logged(read_records(path), "actual", "2026", 3, LEAGUE)


def test_fetch_actual_points_scores_sleeper_stats_with_league_rules(monkeypatch, league):
    from conftest import load_fixture

    sample = load_fixture("sleeper_actuals_sample.json")
    seen = []

    class R:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    def fake_get(url, params=None, timeout=None):
        seen.append(url)
        return R(sample[params["position[]"]])

    monkeypatch.setattr(projection_log.requests, "get", fake_get)
    actual = fetch_actual_points("2026", 3, league["scoring_settings"])
    assert all(u.endswith("/stats/nfl/2026/3") for u in seen) and len(seen) == 6
    # 100 rush yd (10) + rush TD (6) + 5 rec at 0.4 (2) + 40 rec yd (4) plus the league's 100-yard bonus estimate
    assert 22.0 <= actual["4866"] <= 26.0
    assert "7564" not in actual  # did not play (gp 0): an injury, not a projection miss
    assert actual["1234K"] == 7.0 and actual["HOU"] == 9.0  # K/DEF preset totals


def test_fetch_actual_points_refuses_a_week_with_no_stats(monkeypatch, league):
    from projection_log import ActualsError

    class Empty:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    monkeypatch.setattr(projection_log.requests, "get", lambda *a, **k: Empty())
    with pytest.raises(ActualsError, match="no stats"):
        fetch_actual_points("2026", 9, league["scoring_settings"])


def test_log_actuals_can_be_forced_to_re_record(tmp_path):
    path = tmp_path / "log.jsonl"
    assert log_actuals(path, "2026", 3, LEAGUE, {"4866": 24.0}) == 1
    assert log_actuals(path, "2026", 3, LEAGUE, {"4866": 25.0}) == 0
    assert log_actuals(path, "2026", 3, LEAGUE, {"4866": 25.0}, force=True) == 1
    pairs = pair_records(read_records(path) + [{"kind": "projection", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "4866", "blend": 20.0}])
    assert pairs[0][1] == 25.0  # the latest actual wins


def test_pair_records_collapses_duplicate_projections_from_a_partial_write():
    proj = {"kind": "projection", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "1", "blend": 10.0, "position": "RB", "points_by_source": {}}
    recs = [proj, dict(proj, blend=11.0), {"kind": "actual", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "1", "actual": 12.0}]
    pairs = pair_records(recs)
    assert len(pairs) == 1 and pairs[0][0]["blend"] == 11.0


def test_accuracy_report_measures_each_source_and_counts_unmatched():
    recs = [
        {"kind": "projection", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "1", "position": "RB",
         "points_by_source": {"sleeper": 9.0, "fp": 11.0, "espn": 10.0}, "blend": 10.0, "adjusted": 10.5},
        {"kind": "projection", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "2", "position": "WR",
         "points_by_source": {"sleeper": 22.0}, "blend": 20.0, "adjusted": 19.0},
        {"kind": "projection", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "3", "position": "WR",
         "points_by_source": {"sleeper": 3.0}, "blend": 3.0, "adjusted": 3.0},
        {"kind": "projection", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "4", "position": "TE",
         "points_by_source": {"sleeper": 8.0}, "blend": 8.0, "adjusted": 8.0},
        {"kind": "actual", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "1", "actual": 12.0},
        {"kind": "actual", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "2", "actual": 15.0},
        {"kind": "actual", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "3", "actual": 1.0},
        {"kind": "logged", "what": "projection", "season": "2026", "week": 3, "league_id": LEAGUE, "count": 4},
        {"kind": "logged", "what": "actual", "season": "2026", "week": 3, "league_id": LEAGUE, "count": 3},
    ]
    assert len(pair_records(recs)) == 3
    report = accuracy_report(recs, min_points=5.0)
    blend = report["overall"]["blend"]
    assert blend["n"] == 2 and blend["mae"] == pytest.approx(3.5) and blend["bias"] == pytest.approx(1.5)
    assert report["overall"]["fp"]["n"] == 1 and report["overall"]["fp"]["mae"] == pytest.approx(1.0)
    assert report["overall"]["adjusted"]["mae"] == pytest.approx(2.75)
    assert report["by_position"]["WR"]["blend"]["mae"] == pytest.approx(5.0)
    assert report["unmatched"] == 1 and report["weights"] is None


def test_unmatched_only_counts_weeks_whose_actuals_were_recorded():
    recs = [
        {"kind": "projection", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "1", "blend": 10.0, "position": "RB", "points_by_source": {}},
        {"kind": "projection", "season": "2026", "week": 4, "league_id": LEAGUE, "player_id": "1", "blend": 10.0, "position": "RB", "points_by_source": {}},
        {"kind": "logged", "what": "actual", "season": "2026", "week": 3, "league_id": LEAGUE, "count": 0},
    ]
    assert accuracy_report(recs)["unmatched"] == 1  # week 4 has no actuals yet, so it is not a miss


def test_feed_weights_use_only_player_weeks_every_feed_covered():
    from projection_log import MIN_FEED_SAMPLE

    recs = []
    for i in range(MIN_FEED_SAMPLE):
        recs.append({"kind": "projection", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": str(i), "position": "RB", "blend": 10.0, "adjusted": 10.0,
                     "points_by_source": {"sleeper": 12.0, "fp": 11.0, "espn": 10.0}})
        recs.append({"kind": "actual", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": str(i), "actual": 10.0})
    # an extra sleeper-only row must not enter the weights sample
    recs.append({"kind": "projection", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "x", "position": "RB", "blend": 10.0, "adjusted": 10.0, "points_by_source": {"sleeper": 40.0}})
    recs.append({"kind": "actual", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "x", "actual": 10.0})
    weights = accuracy_report(recs)["weights"]
    assert weights["espn"] > weights["fp"] > weights["sleeper"] and abs(sum(weights.values()) - 1) < 0.01


def test_decision_curve_counts_correct_calls_by_projection_gap():
    from projection_log import decision_curve

    recs = [
        {"kind": "projection", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "1", "position": "WR", "blend": 20.0, "points_by_source": {}},
        {"kind": "projection", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "2", "position": "WR", "blend": 15.0, "points_by_source": {}},
        {"kind": "projection", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "3", "position": "WR", "blend": 8.0, "points_by_source": {}},
        {"kind": "projection", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "4", "position": "RB", "blend": 9.5, "points_by_source": {}},
        {"kind": "actual", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "1", "actual": 18.0},
        {"kind": "actual", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "2", "actual": 21.0},
        {"kind": "actual", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "3", "actual": 9.0},
        {"kind": "actual", "season": "2026", "week": 3, "league_id": LEAGUE, "player_id": "4", "actual": 1.0},
    ]
    curve = {tuple(b["gap"]): b for b in decision_curve(recs)}
    assert curve[(5, 8)] == {"gap": [5, 8], "observed": 0.5, "n": 2}  # 20 vs 15 was wrong, 15 vs 8 was right
    assert curve[(8, 99)]["observed"] == 1.0 and curve[(8, 99)]["n"] == 1  # 20 vs 8
    assert curve[(1, 2)]["n"] == 0 and curve[(1, 2)]["observed"] is None  # the RB pairs with nobody
