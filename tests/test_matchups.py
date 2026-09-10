import pytest
import requests

import matchups
from matchups import (
    A_IMPLIED, B_DVP, CAP, DEFAULT_IMPLIED, MatchupError, attach_matchup, fetch_csv, fetch_schedule,
    league_avg_implied, matchup_factor, parse_schedule, team_context, to_sleeper_team,
)


class FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


@pytest.fixture
def schedule_rows():
    from conftest import load_text

    return parse_schedule(fetch_rows(load_text("nflverse_schedule_sample.csv")), "2026")


def fetch_rows(text):
    import csv
    import io

    return list(csv.DictReader(io.StringIO(text)))


def test_to_sleeper_team_maps_the_rams_and_leaves_the_rest():
    assert to_sleeper_team("LA") == "LAR" and to_sleeper_team("JAX") == "JAX"


def test_parse_schedule_keeps_the_regular_season_of_the_year_and_maps_codes(schedule_rows):
    assert [g["week"] for g in schedule_rows] == [1, 1, 1, 1, 2]
    neutral = next(g for g in schedule_rows if g["away"] == "SF")
    assert neutral["home"] == "LAR" and neutral["neutral"] is True and neutral["roof"] == "dome"
    no_line = next(g for g in schedule_rows if g["away"] == "TB")
    assert no_line["total"] is None and no_line["spread_line"] is None
    assert next(g for g in schedule_rows if g["away"] == "SEA")["roof"] is None


def test_team_context_gives_each_team_its_implied_total_and_site(schedule_rows):
    ctx = team_context(schedule_rows, 1)
    assert ctx["SEA"]["opponent"] == "NE" and ctx["SEA"]["site"] == "home" and ctx["SEA"]["implied"] == 23.75
    assert ctx["NE"]["site"] == "away" and ctx["NE"]["implied"] == 20.75 and ctx["NE"]["total"] == 44.5
    assert ctx["SEA"]["spread"] == -3 and ctx["NE"]["spread"] == 3  # negative = favored
    assert ctx["CAR"]["implied"] == 21.75 and ctx["CHI"]["implied"] == 24.75
    assert ctx["SF"]["site"] == "neutral" and ctx["LAR"]["site"] == "neutral" and ctx["LAR"]["opponent"] == "SF"
    assert ctx["TB"]["implied"] is None and ctx["TB"]["opponent"] == "CIN"
    assert "KC" not in ctx  # plays in week 2, not week 1
    assert ctx["SEA"]["gameday"] == "2026-09-09" and ctx["SEA"]["roof"] == "outdoors"


def test_league_avg_implied_falls_back_when_no_lines_are_posted(schedule_rows):
    assert league_avg_implied(team_context(schedule_rows, 1)) == pytest.approx(23.25, abs=0.01)
    assert league_avg_implied({"TB": {"implied": None}}) == DEFAULT_IMPLIED


def test_matchup_factor_combines_implied_dvp_and_site_with_small_coefficients():
    ctx = {"implied": 25.0, "site": "home"}
    factor, parts = matchup_factor("RB", ctx, {"factor": 1.2}, 22.5)
    assert parts["implied"] == pytest.approx(1 + A_IMPLIED * (25 / 22.5 - 1))
    assert parts["dvp"] == pytest.approx(1 + B_DVP * 0.2) and parts["site"] == 1.01
    assert factor == pytest.approx(parts["implied"] * parts["dvp"] * parts["site"])


def test_matchup_factor_ignores_dvp_for_kickers_and_flips_for_defenses():
    ctx = {"implied": 27.0, "site": "away", "opponent_implied": 27.0}
    k_factor, k_parts = matchup_factor("K", ctx, {"factor": 1.2}, 22.5)
    assert k_parts["dvp"] == 1.0 and k_factor == pytest.approx(k_parts["implied"] * 0.99)
    d_factor, d_parts = matchup_factor("DEF", {"implied": 18.0, "site": "home", "opponent_implied": 27.0}, None, 22.5)
    assert d_parts["implied"] == pytest.approx(1 - A_IMPLIED * (27 / 22.5 - 1)) and d_factor < 1


def test_matchup_factor_is_capped_and_neutral_when_context_is_missing():
    factor, parts = matchup_factor("WR", {"implied": 40.0, "site": "home"}, {"factor": 1.2}, 22.5)
    assert parts["implied"] == pytest.approx(1.10) and factor == pytest.approx(1 + CAP)
    assert matchup_factor("WR", {"implied": None, "site": "neutral"}, None, 22.5)[0] == 1.0


def test_attach_matchup_returns_a_new_pool_with_adjusted_points():
    pool = {"1": {"name": "A", "position": "RB", "team": "SEA", "points": 10.0},
            "2": {"name": "B", "position": "WR", "team": "FA", "points": 5.0}}
    ctx = {"SEA": {"opponent": "NE", "site": "home", "implied": 25.0, "total": 44.5, "spread": -3, "roof": "outdoors", "gameday": "2026-09-09", "gametime": "20:15"}}
    dvp = {"NE": {"RB": {"factor": 1.2, "rank": 30, "allowed": 25.0, "games": 17}}}
    out = attach_matchup(pool, ctx, dvp)
    assert "adjusted_points" not in pool["1"]
    assert out["1"]["adjusted_points"] == pytest.approx(10.0 * matchup_factor("RB", ctx["SEA"], dvp["NE"]["RB"], 25.0)[0], abs=0.05)
    assert out["1"]["matchup"]["dvp_rank"] == 30 and out["1"]["matchup"]["opponent"] == "NE"
    assert out["2"]["matchup"] is None and out["2"]["adjusted_points"] == 5.0


def test_fetch_csv_wraps_network_and_http_errors(monkeypatch):
    monkeypatch.setattr(matchups.requests, "get", lambda *a, **k: FakeResponse("", 404))
    with pytest.raises(MatchupError):
        fetch_csv("https://example.test/x.csv")

    def boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(matchups.requests, "get", boom)
    with pytest.raises(MatchupError):
        fetch_schedule("2026")


# ---- kickoff locks ----
from datetime import datetime, timedelta

from matchups import ET, kickoff_time, locked_teams, next_kickoffs


def test_kickoff_time_reads_eastern_and_returns_none_when_blank(schedule_rows):
    ctx = team_context(schedule_rows, 1)
    ne = kickoff_time(ctx["NE"])
    assert ne == datetime(2026, 9, 9, 20, 15, tzinfo=ET)
    assert kickoff_time({**ctx["NE"], "factor": 1.0}) == ne  # a row's matchup dict carries the same fields
    assert kickoff_time({"gameday": None, "gametime": None}) is None
    assert kickoff_time({"gameday": "2026-09-13", "gametime": ""}) is None
    assert kickoff_time(None) is None


def test_locked_teams_treats_kickoff_as_locked_and_later_games_as_free(schedule_rows):
    ctx = team_context(schedule_rows, 1)
    at_kickoff = datetime(2026, 9, 9, 20, 15, tzinfo=ET)
    assert locked_teams(ctx, at_kickoff) == frozenset({"NE", "SEA"})
    assert locked_teams(ctx, at_kickoff - timedelta(minutes=1)) == frozenset()
    friday = datetime(2026, 9, 11, 12, 0, tzinfo=ET)
    assert locked_teams(ctx, friday) == frozenset({"NE", "SEA", "SF", "LAR"})
    assert locked_teams({}, friday) == frozenset()


def test_next_kickoffs_dedupes_games_including_neutral_sites_and_sorts(schedule_rows):
    ctx = team_context(schedule_rows, 1)
    upcoming = next_kickoffs(ctx, datetime(2026, 9, 9, 21, 0, tzinfo=ET))
    assert [sorted(teams) for _, teams in upcoming] == [["LAR", "SF"], ["CAR", "CHI"], ["CIN", "TB"]]
    assert upcoming[0][0] == datetime(2026, 9, 10, 20, 15, tzinfo=ET)
    assert next_kickoffs(ctx, datetime(2026, 9, 14, 0, 0, tzinfo=ET)) == []


def test_fmt_kickoff_and_game_label(schedule_rows):
    from matchups import fmt_kickoff, game_label

    ctx = team_context(schedule_rows, 1)
    assert fmt_kickoff(datetime(2026, 9, 10, 20, 35, tzinfo=ET)) == "Thu 8:35 pm ET"
    assert fmt_kickoff(datetime(2026, 9, 13, 13, 0, tzinfo=ET)) == "Sun 1:00 pm ET"
    assert game_label(ctx, frozenset({"NE", "SEA"})) == "NE at SEA"
    assert game_label(ctx, frozenset({"SF", "LAR"})) == "LAR vs SF"  # neutral site
    assert game_label({}, frozenset({"B", "A"})) == "A vs B"
