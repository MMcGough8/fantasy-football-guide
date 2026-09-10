import pytest
import requests

import odds
from conftest import load_fixture
from odds import TEAM_NAMES, OddsError, fetch_odds, merge_lines, parse_events


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


def test_team_names_cover_all_32_sleeper_codes():
    assert len(TEAM_NAMES) == 32 and len(set(TEAM_NAMES.values())) == 32
    assert TEAM_NAMES["Los Angeles Rams"] == "LAR" and TEAM_NAMES["Washington Commanders"] == "WAS"
    assert TEAM_NAMES["San Francisco 49ers"] == "SF" and TEAM_NAMES["Jacksonville Jaguars"] == "JAX"


def test_parse_events_takes_the_bookmaker_median_in_nflverse_sign_convention():
    parsed = parse_events(load_fixture("odds_sample.json"))
    rams = parsed["lines"][("LAR", "SF")]
    assert rams["spread_line"] == 3.25 and rams["total"] == 48.75  # Rams favoured by 3.25 at home
    assert rams["updated"] == "2026-09-10T12:05:00Z"  # newest market update across books


def test_parse_events_skips_a_bookmaker_missing_a_market_and_reports_unknown_teams():
    parsed = parse_events(load_fixture("odds_sample.json"))
    kc = parsed["lines"][("KC", "DEN")]
    assert kc["spread_line"] == 7.25 and kc["total"] == 45.5
    assert parsed["missing"] == ["London Monarchs"]
    assert set(parsed["lines"]) == {("LAR", "SF"), ("KC", "DEN")}  # the bookmaker-less game is dropped


def test_merge_lines_replaces_only_matching_games_of_the_week_and_returns_new_dicts():
    games = [
        {"week": 1, "home": "LAR", "away": "SF", "total": 48.5, "spread_line": 3.5},
        {"week": 1, "home": "CIN", "away": "TB", "total": None, "spread_line": None},
        {"week": 2, "home": "LAR", "away": "SF", "total": 40.0, "spread_line": 1.0},
    ]
    lines = {("LAR", "SF"): {"total": 48.75, "spread_line": 3.25, "updated": "x"}}
    merged = merge_lines(games, lines, 1)
    assert merged[0]["total"] == 48.75 and merged[0]["spread_line"] == 3.25 and merged[0]["line_source"] == "live"
    assert merged[1]["line_source"] == "nflverse" and merged[1]["total"] is None
    assert merged[2]["total"] == 40.0 and merged[2]["line_source"] == "nflverse"
    assert games[0]["total"] == 48.5 and "line_source" not in games[0] and merged[0] is not games[0]


def test_fetch_odds_returns_events_and_the_remaining_credits(monkeypatch):
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen.update(url=url, params=params)
        return FakeResponse(load_fixture("odds_sample.json"), headers={"x-requests-remaining": "412", "x-requests-used": "88"})

    monkeypatch.setattr(odds.requests, "get", fake_get)
    result = fetch_odds("secret-key")
    assert len(result["events"]) == 4 and result["remaining"] == 412 and result["used"] == 88
    assert seen["params"]["markets"] == "spreads,totals" and seen["params"]["regions"] == "us"
    assert seen["params"]["apiKey"] == "secret-key" and "secret-key" not in seen["url"]


def test_fetch_odds_reports_the_api_error_code_and_never_leaks_the_key(monkeypatch):
    monkeypatch.setattr(odds.requests, "get", lambda *a, **k: FakeResponse({"message": "API key is not valid", "error_code": "INVALID_KEY"}, status=401))
    with pytest.raises(OddsError, match="INVALID_KEY"):
        fetch_odds("secret-key")
    monkeypatch.setattr(odds.requests, "get", lambda *a, **k: FakeResponse({"error_code": "EXCEEDED_FREQ_LIMIT"}, status=429))
    with pytest.raises(OddsError, match="EXCEEDED_FREQ_LIMIT"):
        fetch_odds("secret-key")
    monkeypatch.setattr(odds.requests, "get", lambda *a, **k: FakeResponse("not json", status=500))
    with pytest.raises(OddsError, match="500"):
        fetch_odds("secret-key")

    def boom(*a, **k):
        raise requests.ConnectionError("Max retries exceeded with url: /v4/odds?apiKey=secret-key")

    monkeypatch.setattr(odds.requests, "get", boom)
    with pytest.raises(OddsError) as failure:
        fetch_odds("secret-key")
    assert "ConnectionError" in str(failure.value) and "secret-key" not in str(failure.value)
