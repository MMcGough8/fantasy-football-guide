import pytest
import requests

import draft_board
from draft_board import fetch_position, replacement_ranks
from espn_ranks import _normalize, match_key


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_projections(projections):
    def fake_get(url, params=None, timeout=None):
        pos = params["position[]"]
        rows = [
            {"player_id": r["player_id"], "player": r["player"], "stats": r["stats"]}
            for r in projections[pos]
        ]
        return FakeResponse(rows)

    return fake_get


def test_replacement_ranks_with_flex_matches_legacy_heuristic_for_12_teams():
    starters = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}
    assert replacement_ranks(12, starters) == {
        "QB": 12, "RB": 30, "WR": 30, "TE": 12, "K": 12, "DEF": 12,
    }


def test_replacement_ranks_without_starters_uses_legacy_heuristic():
    assert replacement_ranks(10, None)["RB"] == 26
    assert replacement_ranks(10, None)["QB"] == 10


def test_replacement_ranks_superflex_adds_qb_demand():
    starters = {"QB": 1, "SUPER_FLEX": 1, "RB": 2, "WR": 2}
    ranks = replacement_ranks(12, starters)
    assert ranks["QB"] > 12


def test_replacement_ranks_never_below_one():
    assert replacement_ranks(12, {"RB": 1})["TE"] >= 1


def test_fetch_position_preset_scoring(monkeypatch, projections):
    monkeypatch.setattr(draft_board.requests, "get", _fake_projections(projections))
    players = fetch_position("RB", "pts_ppr")
    assert players[0]["name"] == "Jahmyr Gibbs"
    assert players[0]["points"] == projections["RB"][0]["stats"]["pts_ppr"]


def test_fetch_position_league_scoring_rescored_offense(monkeypatch, projections, league):
    monkeypatch.setattr(draft_board.requests, "get", _fake_projections(projections))
    players = fetch_position("QB", "pts_ppr", scoring_settings=league["scoring_settings"])
    allen = next(p for p in players if p["name"] == "Josh Allen")
    assert allen["points"] > projections["QB"][0]["stats"]["pts_std"]


def test_fetch_position_league_scoring_keeps_preset_for_kickers(monkeypatch, projections, league):
    monkeypatch.setattr(draft_board.requests, "get", _fake_projections(projections))
    players = fetch_position("K", "pts_ppr", scoring_settings=league["scoring_settings"])
    aubrey = next(p for p in players if p["name"] == "Brandon Aubrey")
    assert aubrey["points"] == projections["K"][0]["stats"]["pts_std"]


def test_fetch_position_sorted_best_first(monkeypatch, projections):
    monkeypatch.setattr(draft_board.requests, "get", _fake_projections(projections))
    pts = [p["points"] for p in fetch_position("WR", "pts_std")]
    assert pts == sorted(pts, reverse=True)


def test_blend_stats_two_sources_averages_and_keeps_singletons():
    from draft_board import blend_stats

    blended = blend_stats([{"rush_yd": 1000, "gp": 17, "adp_ppr": 5}, {"rush_yd": 1200, "rec": 40}])
    assert blended == {"rush_yd": 1100, "gp": 17, "adp_ppr": 5, "rec": 40}


def test_blend_stats_three_sources_takes_the_median():
    from draft_board import blend_stats

    blended = blend_stats([{"rush_yd": 1000}, {"rush_yd": 1600}, {"rush_yd": 1100}])
    assert blended["rush_yd"] == 1100  # the 1600 outlier is ignored


def test_fetch_position_blends_extra_sources(monkeypatch, projections, league):
    monkeypatch.setattr(draft_board.requests, "get", _fake_projections(projections))
    key = match_key("Jahmyr Gibbs", "RB")
    sleeper_only = fetch_position("RB", "pts_ppr", scoring_settings=league["scoring_settings"])
    gibbs_sleeper = next(p for p in sleeper_only if p["name"] == "Jahmyr Gibbs")

    base = {k: v for k, v in projections["RB"][0]["stats"].items() if k != "gp" and not k.startswith("adp")}
    low = {**base, "rush_yd": 100}
    high = {**base, "rush_yd": 99999}
    blended = fetch_position(
        "RB", "pts_ppr", scoring_settings=league["scoring_settings"],
        extra_sources={"fp": {key: high}, "espn": {key: low}},
    )
    gibbs = next(p for p in blended if p["name"] == "Jahmyr Gibbs")
    # median of (sleeper, very high, very low) on every stat is sleeper's own line
    assert gibbs["points"] == pytest.approx(gibbs_sleeper["points"], abs=0.2)
    assert gibbs["sources"] == 3
    assert set(gibbs["points_by_source"]) == {"sleeper", "fp", "espn"}
    assert gibbs["points_by_source"]["fp"] > gibbs["points_by_source"]["espn"]
    bijan = next(p for p in blended if p["name"] == "Bijan Robinson")
    assert bijan["sources"] == 1 and set(bijan["points_by_source"]) == {"sleeper"}


def test_fetch_position_blends_preset_points_when_no_league(monkeypatch, projections):
    monkeypatch.setattr(draft_board.requests, "get", _fake_projections(projections))
    key = match_key("Jahmyr Gibbs", "RB")
    players = fetch_position("RB", "pts_ppr", extra_sources={"fp": {key: {"pts_ppr": 100.0}}})
    gibbs = next(p for p in players if p["name"] == "Jahmyr Gibbs")
    assert gibbs["points"] == pytest.approx((projections["RB"][0]["stats"]["pts_ppr"] + 100.0) / 2)


def test_other_source_borrows_games_played_so_bonus_estimates_match(monkeypatch, projections, league):
    monkeypatch.setattr(draft_board.requests, "get", _fake_projections(projections))
    gibbs_stats = projections["RB"][0]["stats"]
    same_line = {k: v for k, v in gibbs_stats.items() if k != "gp" and not k.startswith("adp")}
    players = fetch_position(
        "RB", "pts_ppr", scoring_settings=league["scoring_settings"],
        extra_sources={"fp": {match_key("Jahmyr Gibbs", "RB"): same_line}},
    )
    gibbs = next(p for p in players if p["name"] == "Jahmyr Gibbs")
    assert gibbs["points_by_source"]["fp"] == pytest.approx(gibbs["points_by_source"]["sleeper"], abs=0.1)
    assert gibbs["points"] == pytest.approx(gibbs["points_by_source"]["sleeper"], abs=0.1)


def test_adp_key_follows_reception_scoring():
    from draft_board import adp_key_for

    assert adp_key_for("pts_ppr", None) == "adp_ppr"
    assert adp_key_for("pts_std", None) == "adp_std"
    assert adp_key_for("pts_ppr", {"rec": 0.4}) == "adp_half_ppr"
    assert adp_key_for("pts_ppr", {"rec": 1.0}) == "adp_ppr"
    assert adp_key_for("pts_ppr", {"rec": 0.0}) == "adp_std"


def test_zero_point_sleeper_line_is_not_counted_as_a_source(monkeypatch, projections, league):
    """Sleeper ships only gp/adp for IR players; that must not masquerade as a projection."""
    ir_only = dict(projections)
    ir_only["RB"] = [dict(projections["RB"][0], stats={"gp": 18.0, "adp_half_ppr": 90.0})]
    monkeypatch.setattr(draft_board.requests, "get", _fake_projections(ir_only))
    key = match_key("Jahmyr Gibbs", "RB")
    espn_line = {"rush_yd": 1200, "rush_td": 10, "rec": 40, "rec_yd": 300, "rec_td": 2, "fum_lost": 1}
    players = fetch_position(
        "RB", "pts_ppr", scoring_settings=league["scoring_settings"],
        extra_sources={"espn": {key: espn_line}},
    )
    gibbs = next(p for p in players if p["name"] == "Jahmyr Gibbs")
    assert gibbs["sources"] == 1
    assert set(gibbs["points_by_source"]) == {"espn"}
    # and with no other source at all, the player drops off the board
    assert fetch_position("RB", "pts_ppr", scoring_settings=league["scoring_settings"]) == [
        p for p in fetch_position("RB", "pts_ppr", scoring_settings=league["scoring_settings"]) if p["name"] != "Jahmyr Gibbs"
    ]
