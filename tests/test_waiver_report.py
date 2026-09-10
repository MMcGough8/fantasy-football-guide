from datetime import datetime

from matchups import ET
from waiver_report import format_league_report


def _row(pid, pos, points, name=None, team="X"):
    return {"player_id": pid, "name": name or f"{pos}{pid}", "position": pos, "team": team, "points": points,
            "injury_status": None, "reason": None, "matchup": None}


def test_format_league_report_lists_every_section():
    pittman = dict(_row("1", "WR", 11.6, "Michael Pittman", "IND"), matchup={"gameday": "2026-09-13", "gametime": "13:00"})
    swap = {"slot": "WR", "in": pittman, "out": _row("2", "WR", 10.0, "Courtland Sutton", "DEN"), "out_reason": None,
            "delta": 1.6, "coin_flip": False}
    targets = {
        "season": [{"row": _row("3", "WR", 9.8, "Jauan Jennings", "SF"), "week_gain": 2.1, "season_gain": 38.0, "adds": 12340}],
        "week": [{"row": _row("LAC", "DEF", 8.4, "Los Angeles Chargers", "LAC"), "week_gain": 2.2, "season_gain": 0.0, "adds": 0}],
        "depth": [{"row": _row("6", "RB", 4.0, "Deep Back", "GB"), "week_gain": 0.0, "season_gain": 0.0, "adds": 900,
                   "season_points": 88.0, "over": _row("4", "TE", 3.0, "Brenton Strange", "JAX")}],
    }
    drops = [{"row": _row("4", "TE", 3.0, "Brenton Strange", "JAX"), "season_points": 153.0},
             {"row": _row("5", "RB", 0.0, "Nobody", "FA"), "season_points": None}]
    text = format_league_report(
        "Deuces", "magoo82", 1, {"type": "FAAB", "budget": 100, "clear_days": 2}, 87, [swap], targets, drops, "points",
        ["Week 1 projections: Sleeper + ESPN"], now=datetime(2026, 9, 9, 22, 0, tzinfo=ET),
    )
    assert text.splitlines()[0] == "== Deuces (magoo82) · week 1 =="
    assert "Waivers: FAAB · $87 of 100 left · claims clear after 2 day(s)" in text
    assert "WR: start Michael Pittman (11.6) for Courtland Sutton (10.0), +1.6, by Sun 1:00 pm ET" in text
    assert "Add for the season:" in text
    assert "WR Jauan Jennings SF 9.8 · +2.1 this week · +38 season · 12,340 adds/24h" in text
    assert "Streamers for week 1:" in text and "DEF Los Angeles Chargers LAC 8.4 · +2.2 this week" in text
    assert "Depth upgrades:" in text and "RB Deep Back GB · season 88 over Brenton Strange · 900 adds/24h" in text
    assert "Drop candidates:" in text and "TE Brenton Strange JAX · season 153" in text
    assert "RB Nobody FA · no season projection" in text
    assert "Notes: Week 1 projections: Sleeper + ESPN" in text


def test_format_league_report_says_when_there_is_nothing_to_do():
    text = format_league_report("Girls", "amcgough13", 2, {"type": "rolling", "budget": None, "clear_days": None},
                                None, [], {"season": [], "week": []}, [], "points", [])
    assert "Waivers: rolling" in text and "$" not in text
    assert "Lineup: already optimal" in text
    assert "Add for the season: nobody on the wire improves this lineup" in text
    assert "Streamers for week 2: none worth a claim" in text
    assert "Depth upgrades: nobody beats your worst bench player over the season" in text
    assert "Drop candidates: everyone starts somewhere" in text
    assert "Notes:" not in text


def test_format_swap_marks_out_starters_coin_flips_and_empty_slots():
    from waiver_report import format_swap

    now = datetime(2026, 9, 13, 12, 30, tzinfo=ET)
    into_empty = {"slot": "K", "in": dict(_row("K1", "K", 8.0, "Some Kicker", "CIN"), matchup={"gameday": "2026-09-13", "gametime": "13:00"}),
                  "out": None, "out_reason": None, "delta": 8.0, "coin_flip": False}
    assert format_swap(into_empty, "points", now) == "K: start Some Kicker (8.0) into an empty slot, +8.0, kicks off in 30 min (Sun 1:00 pm ET)"
    for_out = {"slot": "RB", "in": _row("1", "RB", 9.0, "Healthy Back"), "out": _row("2", "RB", 14.0, "Hurt Back"),
               "out_reason": "Out", "delta": 9.0, "coin_flip": False}
    assert format_swap(for_out, "points", now) == "RB: start Healthy Back (9.0) for Hurt Back (14.0, Out), +9.0"
    flip = {"slot": "WR", "in": _row("1", "WR", 9.0, "A"), "out": _row("2", "WR", 8.5, "B"), "out_reason": None, "delta": 0.5, "coin_flip": True}
    assert format_swap(flip, "points", now) == "WR: start A (9.0) for B (8.5), +0.5 (coin flip)"
