import pytest

from bet_builder import best_parlays, best_singles, leg_reason, nearly_safe
from calibration import Calibration
from conftest import load_fixture


def _entry(player, market, side, line, price, p, ev_line, book="draftkings", team="IND", game="e1", position="WR", consensus_line=None, edge=0.0, **extra):
    fair = -110
    return {"player": player, "player_id": player, "market": market, "side": side, "line": line, "price": price, "book": book, "p": p, "p_win": p,
            "ev": ev_line, "ev_line": ev_line, "p_model": p, "p_market": p, "fair": fair, "team": team, "game": game, "event_id": game,
            "position": position, "consensus_line": consensus_line, "edge": edge, "push": 0.0, "home": "IND", "away": "ATL", **extra}


def test_leg_reason_says_where_the_edge_comes_from_in_plain_words():
    room = _entry("Geno Smith", "player_pass_yds", "under", 212.5, -114, 0.55, 0.064, book="fanduel", consensus_line=221.0, edge=8.5)
    assert leg_reason(room) == "FanDuel posts 212.5 against a consensus 221: 8.5 yards of room on the under, which lands 55% of the time. Pays -114, fair -110."
    price = _entry("Bo Nix", "player_pass_tds", "over", 1.5, 130, 0.45, 0.03, consensus_line=1.5)
    assert leg_reason(price) == "DraftKings pays +130 on the over 1.5 where fair is -110; it lands 45% of the time."
    td = _entry("Woody Marks", "player_anytime_td", "yes", None, 240, 0.30, 0.037, book="fanduel")
    assert leg_reason(td) == "FanDuel pays +240; a touchdown lands 30% of the time, fair -110. A long shot, not a bankroll bet."
    spread = _entry("IND", "spread", "home", -2.5, -110, 0.52, 0.0, book="fanduel", position="TEAM", consensus_line=-3.0, edge=0.5)
    assert leg_reason(spread) == "FanDuel gives IND -2.5 against a consensus -3, half a point better; covers 52% of the time. Pays -110, fair -110."
    ml = _entry("IND", "moneyline", "home", None, -150, 0.60, 0.01, book="fanduel", position="TEAM")
    assert leg_reason(ml) == "FanDuel pays -150 on IND to win; the market makes that 60%, fair -110."
    total = _entry("ATL at IND", "total", "under", 48.0, -112, 0.51, 0.0, book="fanduel", position="GAME", team=None, consensus_line=47.5, edge=0.5)
    assert leg_reason(total) == "FanDuel posts 48 against a consensus 47.5, half a point better on the under, which lands 51% of the time. Pays -112, fair -110."


def test_best_singles_are_the_safe_legs_by_line_edge_and_nearly_safe_explains_the_rest():
    good = _entry("A", "player_reception_yds", "over", 50.5, -110, 0.58, 0.05)
    better = _entry("B", "player_receptions", "under", 4.5, -105, 0.57, 0.07, game="e2")
    coin = _entry("C", "player_rush_yds", "over", 60.5, -110, 0.52, 0.03)
    td = _entry("D", "player_anytime_td", "yes", None, 300, 0.30, 0.10)
    assert [e["player"] for e in best_singles([good, coin, td, better])] == ["B", "A"]
    assert best_singles([coin, td]) == []
    steep = _entry("E", "player_pass_yds", "over", 250.5, -250, 0.72, 0.01)
    assert [(e["player"], why) for e, why in nearly_safe([coin, td, steep], limit=3)] == [("C", "probability under 55%")]  # a touchdown or a -250 price never qualifies


def test_best_parlays_pair_safe_legs_and_rank_by_expected_value():
    cal = Calibration(load_fixture("calibration_sample.json"))
    a = _entry("A", "player_reception_yds", "over", 50.5, -110, 0.58, 0.05, game="e1")
    b = _entry("B", "player_receptions", "under", 4.5, -105, 0.57, 0.07, game="e2", team="KC")
    c = _entry("C", "player_rush_yds", "over", 60.5, -110, 0.56, 0.03, game="e3", team="PHI")
    a_again = _entry("A", "player_receptions", "over", 4.5, -110, 0.56, 0.04, game="e1")  # same player as A: never paired with A
    parlays = best_parlays(cal, [a, b, c, a_again], limit=2)
    assert len(parlays) == 2 and all(len(p["legs"]) == 2 for p in parlays)
    assert parlays[0]["ev"] >= parlays[1]["ev"]
    assert all({l["player"] for l in p["legs"]} != {"A"} for p in parlays)
    top = parlays[0]
    assert top["joint"] == pytest.approx(top["legs"][0]["p"] * top["legs"][1]["p"], abs=0.02)  # cross-game legs are independent
    assert top["payout"] == pytest.approx((1 + 100 / 110) * (1 + 100 / 105), abs=0.05) or top["payout"] > 3.0
    assert best_parlays(cal, [a]) == []
