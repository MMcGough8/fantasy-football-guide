import pytest
import requests

import sleeper_league
from sleeper_league import SleeperError, get_user, league_config

ME = "460869683914993664"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


def test_league_config_normalizes_real_league(league):
    cfg = league_config(league, ME)
    assert cfg["name"] == "Don Rugh FFL"
    assert cfg["league_id"] == "1379964730076037120"
    assert cfg["num_teams"] == 12
    assert cfg["starters"] == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}
    assert cfg["bench"] == 5
    assert cfg["scoring_settings"]["rec"] == 0.4
    assert cfg["scoring_settings"]["pass_td"] == 6.0
    assert cfg["draft_id"] == "1379964731309162496"
    assert cfg["is_dynasty"] is False
    assert cfg["user_id"] == ME


def test_get_user_raises_on_unknown_username(monkeypatch):
    monkeypatch.setattr(sleeper_league.requests, "get", lambda *a, **k: FakeResponse(None))
    with pytest.raises(SleeperError, match="not found"):
        get_user("nobody_here_xyz")


def test_get_user_wraps_network_errors(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(sleeper_league.requests, "get", boom)
    with pytest.raises(SleeperError, match="Couldn't reach Sleeper"):
        get_user("magoo82")


def test_non_json_body_is_a_sleeper_error(monkeypatch):
    class HtmlResponse(FakeResponse):
        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(sleeper_league.requests, "get", lambda *a, **k: HtmlResponse({}))
    with pytest.raises(SleeperError, match="unexpected"):
        get_user("magoo82")


def test_get_user_wraps_http_errors(monkeypatch):
    monkeypatch.setattr(sleeper_league.requests, "get", lambda *a, **k: FakeResponse({}, 500))
    with pytest.raises(SleeperError):
        get_user("magoo82")
