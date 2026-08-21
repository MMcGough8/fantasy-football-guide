import pytest
import requests

import espn_projections
from espn_projections import EspnError, fetch_all, parse_players
from espn_ranks import match_key


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
def espn_sample():
    from conftest import load_fixture

    return load_fixture("espn_sample.json")


def _fake_api(espn_sample):
    slot_to_pos = {v: k for k, v in espn_projections.SLOT_IDS.items()}

    def fake_get(url, headers=None, params=None, timeout=None):
        import json

        flt = json.loads(headers["X-Fantasy-Filter"])
        slot = flt["players"]["filterSlotIds"]["value"][0]
        fixture_pos = {"DEF": "DST"}.get(slot_to_pos[slot], slot_to_pos[slot])
        return FakeResponse(espn_sample[fixture_pos])

    return fake_get


def test_parse_players_maps_stat_ids_to_sleeper_keys(espn_sample):
    rows = parse_players(espn_sample["RB"]["players"], "RB", "2026")
    gibbs = rows[match_key("Jahmyr Gibbs", "RB")]
    assert gibbs["stats"]["rush_yd"] == pytest.approx(1372.6, abs=0.1)
    assert gibbs["stats"]["rush_td"] == pytest.approx(14.45, abs=0.05)
    assert gibbs["stats"]["rec"] == pytest.approx(67.8, abs=0.1)
    assert gibbs["stats"]["rec_yd"] == pytest.approx(545.9, abs=0.1)
    assert gibbs["stats"]["fum_lost"] == pytest.approx(1.3, abs=0.1)
    assert gibbs["stats"]["gp"] == 17
    assert gibbs["stats"]["pts_ppr"] == pytest.approx(364.9, abs=0.1)
    assert gibbs["espn_rank"] == 1
    assert gibbs["adp"] == pytest.approx(1.5)


def test_parse_players_qb_passing_stats(espn_sample):
    allen = parse_players(espn_sample["QB"]["players"], "QB", "2026")[match_key("Josh Allen", "QB")]
    assert allen["stats"]["pass_yd"] == pytest.approx(3946.4, abs=0.1)
    assert allen["stats"]["pass_td"] == pytest.approx(26.3, abs=0.1)
    assert allen["stats"]["pass_int"] == pytest.approx(11.6, abs=0.1)
    assert allen["stats"]["rush_yd"] == pytest.approx(579.9, abs=0.1)


def test_kicker_and_defense_use_applied_total_as_preset(espn_sample):
    aubrey = parse_players(espn_sample["K"]["players"], "K", "2026")[match_key("Brandon Aubrey", "K")]
    assert aubrey["stats"]["pts_std"] == pytest.approx(171.7, abs=0.1)
    texans = parse_players(espn_sample["DST"]["players"], "DEF", "2026")[match_key("Houston Texans", "DEF")]
    assert texans["stats"]["pts_std"] == pytest.approx(131.1, abs=0.1)


def test_fetch_all_returns_projections_and_live_ranks(monkeypatch, espn_sample):
    monkeypatch.setattr(espn_projections.requests, "get", _fake_api(espn_sample))
    result = fetch_all("2026")
    assert set(result["projections"]) == {"QB", "RB", "WR", "TE", "K", "DEF"}
    assert result["ranks"][match_key("Jahmyr Gibbs", "RB")] == 1
    assert result["ranks"][match_key("Puka Nacua", "WR")] == 4


def test_network_errors_are_wrapped(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(espn_projections.requests, "get", boom)
    with pytest.raises(EspnError, match="ESPN"):
        fetch_all("2026")


def test_match_key_defenses_by_nickname():
    assert match_key("Texans D/ST", "DEF") == match_key("Houston Texans", "DEF")
    assert match_key("Commanders D/ST", "DEF") == match_key("Washington Commanders", "DEF")
    assert match_key("Amon-Ra St. Brown", "WR") == match_key("Amon-Ra St Brown", "WR")
    assert match_key("Marvin Harrison Jr.", "WR") == match_key("Marvin Harrison", "WR")


def test_previous_season_block_is_skipped(espn_sample):
    gibbs_blocks = espn_sample["RB"]["players"][0]["player"]["stats"]
    assert [b["seasonId"] for b in gibbs_blocks] == [2025, 2026], "fixture must carry both blocks"
    rows = parse_players(espn_sample["RB"]["players"], "RB", "2026")
    assert rows[match_key("Jahmyr Gibbs", "RB")]["stats"]["pts_ppr"] == pytest.approx(364.9, abs=0.1)
    assert parse_players(espn_sample["RB"]["players"], "RB", "2027") == {}


def test_fetch_all_keeps_positions_that_answered(monkeypatch, espn_sample):
    real = _fake_api(espn_sample)

    def partial(url, headers=None, params=None, timeout=None):
        import json

        slot = json.loads(headers["X-Fantasy-Filter"])["players"]["filterSlotIds"]["value"][0]
        if slot == espn_projections.SLOT_IDS["TE"]:
            raise requests.ConnectionError("down")
        return real(url, headers=headers, params=params, timeout=timeout)

    monkeypatch.setattr(espn_projections.requests, "get", partial)
    result = fetch_all("2026")
    assert "TE" not in result["projections"]
    assert result["missing"] == ["TE"]


def test_berry_ranks_match_defenses_by_nickname(monkeypatch):
    import espn_ranks

    monkeypatch.setattr(espn_ranks, "_berry_cache", None)
    monkeypatch.setattr(espn_ranks, "_espn_cache", None)
    monkeypatch.setattr(
        espn_ranks, "_load",
        lambda name: [{"name": "Texans D/ST", "position": "DST", "berry_rank": 150, "espn_rank": 140}],
    )
    board = [{"name": "Houston Texans", "position": "DEF", "vor": 10}]
    espn_ranks.attach_ranks(board)
    assert board[0]["berry_rank"] == 150
    assert board[0]["espn_rank"] == 140
    monkeypatch.setattr(espn_ranks, "_berry_cache", None)
    monkeypatch.setattr(espn_ranks, "_espn_cache", None)
