import pytest
import requests

import matchups
from dvp import DVP_MAX, DVP_MIN, PRIOR_GAMES, allowed_per_game, blend_seasons, dvp_entry, factors, fetch_player_weeks, parse_player_weeks
from matchups import MatchupError


def _rows():
    import csv
    import io
    from conftest import load_text

    return list(csv.DictReader(io.StringIO(load_text("nflverse_player_week_sample.csv"))))


def test_parse_player_weeks_keeps_regular_season_rows_with_points_and_maps_codes():
    weeks = parse_player_weeks(_rows())
    assert len(weeks) == 10  # blank points and the POST row are dropped
    assert {w["opponent"] for w in weeks} == {"GB", "CHI", "LAR"}
    assert all(set(w) == {"week", "position", "opponent", "points"} for w in weeks)


def test_allowed_per_game_sums_a_game_then_averages_games():
    allowed = allowed_per_game(parse_player_weeks(_rows()))
    assert allowed["GB"]["RB"] == {"allowed": 15.0, "games": 2}  # (10+8) and 12
    assert allowed["CHI"]["RB"] == {"allowed": 30.0, "games": 1}
    assert allowed["GB"]["WR"]["allowed"] == 15.0 and "K" not in allowed["GB"]


def test_factors_rank_the_stingiest_defense_first():
    f = factors(allowed_per_game(parse_player_weeks(_rows())))
    assert f["GB"]["RB"]["rank"] == 1 and f["CHI"]["RB"]["rank"] == 2
    assert f["GB"]["RB"]["factor"] == pytest.approx(15 / 22.5) and f["CHI"]["RB"]["factor"] == pytest.approx(30 / 22.5)
    assert f["GB"]["RB"]["games"] == 2


def test_blend_seasons_weights_the_current_season_by_games_played_and_clamps():
    prior = {"GB": {"RB": {"factor": 0.9, "rank": 1, "allowed": 12.0, "games": 17}}}
    current = {"GB": {"RB": {"factor": 1.3, "rank": 32, "allowed": 25.0, "games": PRIOR_GAMES}}}
    blended = blend_seasons(prior, current)
    assert blended["GB"]["RB"]["factor"] == pytest.approx(1.1)  # w = 4/(4+4)
    assert blended["GB"]["RB"]["rank"] == 1  # ranks are recomputed on the blended factor
    assert blend_seasons(prior, {})["GB"]["RB"]["factor"] == 0.9
    hot = {"GB": {"RB": {"factor": 2.0, "rank": 32, "allowed": 40.0, "games": 100}}}
    assert blend_seasons(prior, hot)["GB"]["RB"]["factor"] == DVP_MAX
    cold = {"GB": {"RB": {"factor": 0.1, "rank": 1, "allowed": 2.0, "games": 100}}}
    assert blend_seasons(prior, cold)["GB"]["RB"]["factor"] == DVP_MIN


def test_dvp_entry_returns_none_for_unknown_teams_or_positions():
    d = {"GB": {"RB": {"factor": 0.9}}}
    assert dvp_entry(d, "GB", "RB")["factor"] == 0.9
    assert dvp_entry(d, "GB", "K") is None and dvp_entry(d, "FA", "RB") is None


def test_fetch_player_weeks_reports_an_unpublished_season(monkeypatch):
    class Gone:
        status_code = 404
        text = ""

        def raise_for_status(self):
            raise requests.HTTPError("404")

    monkeypatch.setattr(matchups.requests, "get", lambda *a, **k: Gone())
    with pytest.raises(MatchupError, match="2026"):
        fetch_player_weeks("2026")


def test_blend_seasons_ranks_on_the_blended_factor():
    prior = {"GB": {"RB": {"factor": 0.9, "rank": 1, "allowed": 12.0, "games": 17}}, "CHI": {"RB": {"factor": 1.1, "rank": 2, "allowed": 15.0, "games": 17}}}
    current = {"GB": {"RB": {"factor": 1.2, "rank": 2, "allowed": 25.0, "games": 1}}, "CHI": {"RB": {"factor": 0.8, "rank": 1, "allowed": 8.0, "games": 1}}}
    blended = blend_seasons(prior, current)
    assert blended["GB"]["RB"]["factor"] < blended["CHI"]["RB"]["factor"]  # one game barely moves the prior
    assert blended["GB"]["RB"]["rank"] == 1 and blended["CHI"]["RB"]["rank"] == 2
