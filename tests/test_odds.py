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


# ---- player props ----
from odds import PROP_MARKETS, fetch_event_props, fetch_events, fetch_props_for_events, parse_props, select_week_events


def test_select_week_events_keeps_only_this_weeks_games_and_caps_the_count():
    events = load_fixture("odds_events_sample.json")
    games = [{"week": 1, "home": "IND", "away": "ATL"}, {"week": 1, "home": "KC", "away": "DEN"}, {"week": 2, "home": "KC", "away": "SEA"}]
    picked = select_week_events(events, games, 1)
    assert [e["id"] for e in picked] == ["evt1", "evt2"]  # evt9 is next week, evtx has an unmapped team
    assert select_week_events(events, games, 3) == []


def test_parse_props_pairs_over_and_under_per_book_and_keeps_yes_only_markets():
    legs = {(l["player"], l["market"]): l for l in parse_props(load_fixture("odds_event_props_sample.json"))}
    pittman = legs[("Michael Pittman", "player_reception_yds")]
    assert pittman["home"] == "IND" and pittman["away"] == "ATL" and pittman["event_id"] == "evt1"
    assert pittman["books"]["draftkings"] == {"over": (52.5, -115), "under": (52.5, -105)}
    assert pittman["books"]["fanduel"]["over"] == (54.5, -114) and set(pittman["books"]) == {"draftkings", "fanduel", "betmgm"}
    td = legs[("Michael Pittman", "player_anytime_td")]
    assert td["books"]["draftkings"] == {"yes": (None, 165)} and td["books"]["fanduel"] == {"yes": (None, 150)}
    nobody = legs[("Nobody Known", "player_reception_yds")]
    assert nobody["books"]["betmgm"] == {"over": (30.5, -110)}  # a one-sided book is kept, the other side is simply absent
    assert len(legs) == 7  # Pittman x3, London x2, Jones, Nobody Known


def test_fetch_events_and_event_props_pass_the_key_in_params_and_read_credits(monkeypatch):
    seen = []

    def fake_get(url, params=None, timeout=None):
        seen.append((url, params))
        payload = load_fixture("odds_events_sample.json") if url.endswith("/events") else load_fixture("odds_event_props_sample.json")
        return FakeResponse(payload, headers={"x-requests-remaining": "300", "x-requests-used": "200", "x-requests-last": "6"})

    monkeypatch.setattr(odds.requests, "get", fake_get)
    events = fetch_events("secret-key")
    assert len(events["events"]) == 4 and events["remaining"] == 300 and seen[0][1] == {"apiKey": "secret-key"}
    props = fetch_event_props("secret-key", "evt1")
    assert props["event"]["id"] == "evt1" and props["remaining"] == 300 and props["cost"] == 6
    assert seen[1][0].endswith("/events/evt1/odds") and seen[1][1]["markets"] == ",".join(PROP_MARKETS)
    assert seen[1][1]["apiKey"] == "secret-key" and "secret-key" not in seen[1][0]


def test_fetch_props_for_events_stops_at_the_first_refusal_and_keeps_what_it_got(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if len(calls) == 2:
            return FakeResponse({"error_code": "OUT_OF_USAGE_CREDITS"}, status=401)
        return FakeResponse(load_fixture("odds_event_props_sample.json"), headers={"x-requests-remaining": "10"})

    monkeypatch.setattr(odds.requests, "get", fake_get)
    result = fetch_props_for_events("secret-key", ["evt1", "evt2", "evt3"], pause=0)
    assert [e["id"] for e in result["events"]] == ["evt1"] and "OUT_OF_USAGE_CREDITS" in result["error"]
    assert len(calls) == 2 and result["remaining"] == 10  # evt3 was never requested
