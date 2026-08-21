import pytest
import requests

import fantasypros
from espn_ranks import match_key
from fantasypros import FantasyProsError, fetch_all, fetch_projections, to_sleeper_stats


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
def fp_sample():
    from conftest import load_fixture

    return load_fixture("fantasypros_sample.json")


def _fake_api(fp_sample):
    def fake_get(url, headers=None, params=None, timeout=None):
        assert headers["x-api-key"] == "test-key"
        assert params["week"] == 0
        return FakeResponse(fp_sample[params["position"]])

    return fake_get


def test_to_sleeper_stats_maps_offense_keys():
    stats = to_sleeper_stats(
        {"pass_yds": 3816.47, "pass_tds": 27.42, "pass_ints": 11.19, "rush_yds": 585.97,
         "rec_rec": 71.27, "rec_yds": 580.97, "fumbles": 1.13, "points_ppr": 372.92,
         "rush_yds_100": 0, "ret_tds": 0}
    )
    assert stats == {
        "pass_yd": 3816.47, "pass_td": 27.42, "pass_int": 11.19, "rush_yd": 585.97,
        "rec": 71.27, "rec_yd": 580.97, "fum_lost": 1.13, "pts_ppr": 372.92,
    }


def test_fetch_projections_keys_by_normalized_name(monkeypatch, fp_sample):
    monkeypatch.setattr(fantasypros.requests, "get", _fake_api(fp_sample))
    rbs = fetch_projections("RB", "2026", "test-key")
    assert match_key("Jahmyr Gibbs", "RB") in rbs
    assert rbs[match_key("Jahmyr Gibbs", "RB")]["rush_yd"] == pytest.approx(1381.53)


def test_fetch_all_maps_def_to_dst_and_back(monkeypatch, fp_sample):
    monkeypatch.setattr(fantasypros.requests, "get", _fake_api(fp_sample))
    everything = fetch_all("2026", "test-key")["projections"]
    assert set(everything) == {"QB", "RB", "WR", "TE", "K", "DEF"}
    assert match_key("Houston Texans", "DEF") in everything["DEF"]


def test_missing_key_is_an_error():
    with pytest.raises(FantasyProsError, match="FANTASYPROS_API_KEY"):
        fetch_projections("RB", "2026", "")


def test_http_errors_are_wrapped(monkeypatch):
    monkeypatch.setattr(fantasypros.time, "sleep", lambda s: None)
    monkeypatch.setattr(fantasypros.requests, "get", lambda *a, **k: FakeResponse({}, 403))
    with pytest.raises(FantasyProsError, match="FantasyPros"):
        fetch_projections("RB", "2026", "bad-key")


def test_network_errors_are_wrapped(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(fantasypros.time, "sleep", lambda s: None)
    monkeypatch.setattr(fantasypros.requests, "get", boom)
    with pytest.raises(FantasyProsError):
        fetch_projections("RB", "2026", "test-key")


def test_retries_on_429_then_succeeds(monkeypatch, fp_sample):
    calls = []

    def flaky(url, headers=None, params=None, timeout=None):
        calls.append(params["position"])
        if len(calls) < 3:
            return FakeResponse({}, 429)
        return FakeResponse(fp_sample[params["position"]])

    monkeypatch.setattr(fantasypros.requests, "get", flaky)
    monkeypatch.setattr(fantasypros.time, "sleep", lambda s: None)
    rbs = fetch_projections("RB", "2026", "test-key")
    assert len(calls) == 3
    assert match_key("Jahmyr Gibbs", "RB") in rbs


def test_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr(fantasypros.requests, "get", lambda *a, **k: FakeResponse({}, 503))
    monkeypatch.setattr(fantasypros.time, "sleep", lambda s: None)
    with pytest.raises(FantasyProsError):
        fetch_projections("RB", "2026", "test-key")


def test_fetch_all_keeps_positions_that_answered(monkeypatch, fp_sample):
    def partial(url, headers=None, params=None, timeout=None):
        if params["position"] == "WR":
            raise requests.ConnectionError("down")
        return FakeResponse(fp_sample[params["position"]])

    monkeypatch.setattr(fantasypros.requests, "get", partial)
    monkeypatch.setattr(fantasypros.time, "sleep", lambda s: None)
    result = fetch_all("2026", "test-key")
    assert set(result["projections"]) == {"QB", "RB", "TE", "K", "DEF"}
    assert result["missing"] == ["WR"]


def test_fetch_all_raises_when_nothing_answered(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(fantasypros.requests, "get", boom)
    monkeypatch.setattr(fantasypros.time, "sleep", lambda s: None)
    with pytest.raises(FantasyProsError):
        fetch_all("2026", "test-key")
