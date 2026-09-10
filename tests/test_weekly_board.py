import pytest
import requests

import draft_board
from espn_ranks import match_key
from weekly_board import attach_weekly_ranks, build_weekly_pool, roster_rows


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture
def weekly():
    from conftest import load_fixture

    return load_fixture("weekly_projections_sample.json")


def _fake_sleeper(weekly, seen):
    def fake_get(url, params=None, timeout=None):
        seen.append(url)
        return FakeResponse(weekly[params["position[]"]])

    return fake_get


def test_build_weekly_pool_keys_by_player_id_and_rescores_with_league_rules(monkeypatch, weekly, league):
    seen = []
    monkeypatch.setattr(draft_board.requests, "get", _fake_sleeper(weekly, seen))
    pool = build_weekly_pool(3, league["scoring_settings"])
    assert all(u.endswith("/2026/3") for u in seen)
    barkley = pool["4866"]
    assert barkley["name"] == "Saquon Barkley" and barkley["opponent"] == "GB" and barkley["position"] == "RB"
    # 0.4 PPR league: 85.5*0.1 + 0.6*6 + 3.1*0.4 + 24*0.1 + 0.1*6 - 0.05*2 (+ small threshold bonus)
    assert 15.5 < barkley["points"] < 18.5
    assert pool["HOU"]["position"] == "DEF" and pool["HOU"]["points"] == 7.5  # preset total for DEF
    assert pool["4034"]["injury_status"] == "Questionable"


def test_build_weekly_pool_blends_extra_feeds_by_match_key(monkeypatch, weekly, league):
    monkeypatch.setattr(draft_board.requests, "get", _fake_sleeper(weekly, []))
    fp = {"RB": {match_key("Saquon Barkley", "RB"): {"rush_yd": 105.5, "rush_td": 0.6, "rec": 3.1, "rec_yd": 24.0, "rec_td": 0.1}}}
    pool = build_weekly_pool(3, league["scoring_settings"], extra_projections={"fp": fp})
    assert pool["4866"]["sources"] == 2 and set(pool["4866"]["points_by_source"]) == {"sleeper", "fp"}


def test_attach_weekly_ranks_returns_a_new_pool_with_fp_fields():
    pool = {"4866": {"name": "Saquon Barkley", "position": "RB", "points": 19.2}}
    ranks = {match_key("Saquon Barkley", "RB"): {"rank": 3, "pos_rank": "RB2", "std": 1.5, "grade": "A", "opponent": "at GB"}}
    ranked = attach_weekly_ranks(pool, ranks)
    assert ranked["4866"]["fp_week_rank"] == 3 and ranked["4866"]["fp_week_pos_rank"] == "RB2"
    assert ranked["4866"]["fp_week_rank_std"] == 1.5 and ranked["4866"]["fp_grade"] == "A" and ranked["4866"]["fp_opponent"] == "at GB"
    assert "fp_week_rank" not in pool["4866"]
    assert attach_weekly_ranks(pool, {})["4866"]["fp_week_rank"] is None


def test_roster_rows_keep_order_and_explain_missing_players():
    pool = {"4866": {"name": "Saquon Barkley", "position": "RB", "team": "PHI", "player_id": "4866", "points": 19.2}}
    season = {"1234": {"name": "Bye Guy", "position": "WR", "team": "SEA", "player_id": "1234", "bye": 3}}
    rows = roster_rows(["4866", "1234", "9999"], pool, season, byes={"SEA": 3}, week=3)
    assert [r["player_id"] for r in rows] == ["4866", "1234", "9999"]
    assert rows[0]["reason"] is None and rows[0]["points"] == 19.2
    assert rows[1]["name"] == "Bye Guy" and rows[1]["points"] == 0 and rows[1]["reason"] == "bye"
    assert rows[2]["name"] == "Unknown 9999" and rows[2]["points"] == 0 and rows[2]["reason"] == "no projection"


def test_roster_rows_drop_duplicate_ids():
    pool = {"4866": {"name": "Saquon Barkley", "position": "RB", "team": "PHI", "player_id": "4866", "points": 19.2}}
    rows = roster_rows(["4866", "4866"], pool, {}, {}, 3)
    assert [r["player_id"] for r in rows] == ["4866"]


def test_roster_rows_fallback_carries_the_matchup_fields():
    rows = roster_rows(["9999"], {}, {}, {}, 3)
    assert rows[0]["adjusted_points"] == 0.0 and rows[0]["matchup"] is None


def test_assemble_pool_is_the_rank_and_matchup_chain_over_the_weekly_pool(monkeypatch):
    import weekly_board
    from matchups import attach_matchup

    pool = {"1": {"player_id": "1", "name": "Some Back", "position": "RB", "team": "KC", "points": 10.0}}
    seen = {}

    def fake_build(week, scoring, extra):
        seen.update(week=week, scoring=scoring, extra=extra)
        return pool

    monkeypatch.setattr(weekly_board, "build_weekly_pool", fake_build)
    context = {"KC": {"opponent": "DEN", "site": "home", "implied": 27.0, "total": 48.0, "spread": -6.0, "roof": None,
                      "gameday": None, "gametime": None}}
    assembled = weekly_board.assemble_pool(1, {"rec": 1}, {"fp": {}}, None, context, {})
    assert seen == {"week": 1, "scoring": {"rec": 1}, "extra": {"fp": {}}}
    assert assembled == attach_matchup(attach_weekly_ranks(pool, None), context, {})
    assert assembled["1"]["matchup"]["opponent"] == "DEN" and "adjusted_points" in assembled["1"]
