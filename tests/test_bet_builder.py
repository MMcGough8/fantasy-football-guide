import pytest

from bet_builder import LIKELY, best_parlays, best_singles, leg_reason, likeliest_parlays, likely_winners
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


def test_best_singles_are_the_safe_legs_by_line_edge_using_the_no_push_chance():
    good = _entry("A", "player_reception_yds", "over", 50.5, -110, 0.58, 0.05)
    better = _entry("B", "player_receptions", "under", 4.5, -105, 0.57, 0.07, game="e2")
    coin = _entry("C", "player_rush_yds", "over", 60.5, -110, 0.52, 0.03)
    td = _entry("D", "player_anytime_td", "yes", None, 300, 0.30, 0.10)
    pushy = {**_entry("E", "player_receptions", "over", 4.0, -110, 0.47, 0.04, game="e3"), "p_win": 0.57, "push": 0.17}  # the page shows 57%
    assert [e["player"] for e in best_singles([good, coin, td, better, pushy])] == ["B", "A", "E"]
    assert best_singles([coin, td]) == []


def test_likely_winners_are_the_likeliest_legs_at_a_fair_price_never_long_shots():
    fav = _entry("PIT", "moneyline", "home", None, -230, 0.67, -0.038, position="TEAM", team="PIT", game="g1")
    dog = _entry("CLE", "moneyline", "away", None, 360, 0.23, 0.047, position="TEAM", team="CLE", game="g2")  # an edge, but a long shot
    steep = _entry("LAC", "moneyline", "home", None, -490, 0.80, -0.037, position="TEAM", team="LAC", game="g3")  # shorter than the price floor
    spread = _entry("ATL", "spread", "away", 5.5, -115, 0.53, -0.006, position="TEAM", team="ATL", game="g4")  # a coin flip
    prop = _entry("A", "player_reception_yds", "over", 50.5, -110, 0.61, -0.02, game="g5")
    td = _entry("D", "player_anytime_td", "yes", None, -150, 0.65, 0.01, game="g6")
    picked = likely_winners([dog, prop, fav, steep, spread, td])
    assert [e["player"] for e in picked] == ["PIT", "A"]
    assert likely_winners([fav], exclude=[fav]) == [] and LIKELY["min_probability"] == 0.60


def test_likeliest_parlays_take_one_leg_per_game_and_show_the_honest_edge():
    cal = Calibration(load_fixture("calibration_sample.json"))
    a = _entry("PIT", "moneyline", "home", None, -230, 0.67, -0.038, position="TEAM", team="PIT", game="g1")
    b = _entry("LAR", "moneyline", "home", None, -205, 0.64, -0.041, position="TEAM", team="LAR", game="g2")
    c = _entry("BAL", "moneyline", "home", None, -175, 0.61, -0.035, position="TEAM", team="BAL", game="g3")
    same = _entry("SF", "spread", "away", 3.5, -105, 0.50, -0.032, position="TEAM", team="SF", game="g2")  # LAR's game: never with b
    parlays = likeliest_parlays(cal, [a, b, c, same])
    twos = [p for p in parlays if len(p["legs"]) == 2]
    threes = [p for p in parlays if len(p["legs"]) == 3]
    assert [l["player"] for l in twos[0]["legs"]] == ["PIT", "LAR"] and twos[0]["joint"] == pytest.approx(0.67 * 0.64, abs=1e-3)
    assert twos[0]["payout"] == pytest.approx((1 + 100 / 230) * (1 + 100 / 205), abs=0.01) and twos[0]["ev"] < 0  # fairly priced legs: the books' cut compounds
    assert [l["player"] for l in threes[0]["legs"]] == ["PIT", "LAR", "BAL"] and threes[0]["joint"] == pytest.approx(0.67 * 0.64 * 0.61, abs=1e-3)
    assert all(len({l["game"] for l in p["legs"]}) == len(p["legs"]) for p in parlays)
    assert likeliest_parlays(cal, [a]) == []


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
    assert {l["player"] for l in top["legs"]} == {"A", "B"}
    assert top["joint"] == pytest.approx(0.58 * 0.57, abs=0.02)  # cross-game legs are independent
    assert top["payout"] == pytest.approx((1 + 100 / 110) * (1 + 100 / 105), abs=0.01)
    assert best_parlays(cal, [a]) == []
