import pytest

from calibration import Calibration
from conftest import load_fixture
from game_lines import (
    GAME_MARKETS, cover_probabilities, favourite_sign, game_consensus, game_rho, game_scores, grade_game_leg, leg_label,
    price_game_leg, price_game_legs, solve_centre,
)
from odds import parse_game_lines
from props import parlay_probability
from props_log import grade_leg


@pytest.fixture
def cal():
    data = load_fixture("calibration_sample.json")
    data["games"] = {"n": 816, "margin_sd": 12.6, "total_sd": 12.9, "favourite_over_rho": 0.04}
    data["correlations"]["pairs"].update({"player_rush_yds|spread|same_team": 0.24, "player_rush_yds|spread|opponent": -0.24,
                                          "player_pass_yds|total|same_game": 0.38, "spread|total|same_game": 0.04, "moneyline|total|same_game": 0.04})
    return Calibration(data)


@pytest.fixture
def event():
    return {
        "id": "e9", "commence_time": "2026-09-13T17:00:00Z", "home_team": "Indianapolis Colts", "away_team": "Atlanta Falcons",
        "bookmakers": [
            {"key": "draftkings", "markets": [
                {"key": "spreads", "outcomes": [{"name": "Indianapolis Colts", "price": -105, "point": -3.0}, {"name": "Atlanta Falcons", "price": -115, "point": 3.0}]},
                {"key": "totals", "outcomes": [{"name": "Over", "price": -110, "point": 47.5}, {"name": "Under", "price": -110, "point": 47.5}]},
                {"key": "h2h", "outcomes": [{"name": "Indianapolis Colts", "price": -160}, {"name": "Atlanta Falcons", "price": 136}]}]},
            {"key": "fanduel", "markets": [
                {"key": "spreads", "outcomes": [{"name": "Indianapolis Colts", "price": -110, "point": -2.5}, {"name": "Atlanta Falcons", "price": -110, "point": 2.5}]},
                {"key": "totals", "outcomes": [{"name": "Over", "price": -108, "point": 48.0}, {"name": "Under", "price": -112, "point": 48.0}]},
                {"key": "h2h", "outcomes": [{"name": "Indianapolis Colts", "price": -150}, {"name": "Atlanta Falcons", "price": 130}]}]},
            {"key": "betmgm", "markets": [
                {"key": "spreads", "outcomes": [{"name": "Indianapolis Colts", "price": -110, "point": -3.0}, {"name": "Atlanta Falcons", "price": -110, "point": 3.0}]},
                {"key": "totals", "outcomes": [{"name": "Over", "price": -110, "point": 47.5}]},  # one side only: dropped
                {"key": "h2h", "outcomes": [{"name": "Indianapolis Colts", "price": -155}, {"name": "Atlanta Falcons", "price": 130}]}]},
        ],
    }


@pytest.fixture
def legs(event):
    return {leg["market"]: leg for leg in parse_game_lines(event)}


def test_parse_game_lines_gives_one_leg_per_market_with_both_sides_per_book(legs):
    assert set(legs) == set(GAME_MARKETS)
    spread = legs["spread"]
    assert spread["home"] == "IND" and spread["away"] == "ATL" and spread["event_id"] == "e9"
    assert spread["books"]["draftkings"] == {"home": (-3.0, -105), "away": (3.0, -115)}
    assert legs["moneyline"]["books"]["fanduel"] == {"home": (None, -150), "away": (None, 130)}
    assert "betmgm" not in legs["total"]["books"] and set(legs["total"]["books"]) == {"draftkings", "fanduel"}
    assert parse_game_lines({"id": "x", "home_team": "Nobody", "away_team": "Atlanta Falcons", "bookmakers": []}) == []


def test_game_consensus_is_the_median_line_and_devigged_probability(legs):
    cons = game_consensus(legs["spread"], "home")
    assert cons["line"] == -3.0 and cons["books"] == 3
    assert 0.49 < cons["p"] < 0.51
    away = game_consensus(legs["moneyline"], "away")
    assert away["line"] is None and 0.40 < away["p"] < 0.44


def test_cover_probabilities_use_a_continuity_corrected_normal():
    win, push, loss = cover_probabilities(0.0, 12.6, 2.5)  # margin must reach 3
    assert win == pytest.approx(1 - 0.5793, abs=0.002) and push == 0.0
    win3, push3, loss3 = cover_probabilities(0.0, 12.6, 3.0)  # a whole number can push
    assert push3 == pytest.approx(0.6096 - 0.5787, abs=0.002)  # Phi(3.5/12.6) - Phi(2.5/12.6)
    assert win3 == pytest.approx(1 - 0.6096, abs=0.002) and win3 + push3 + loss3 == pytest.approx(1.0)
    assert cover_probabilities(0.0, 12.6, 0.0)[0] == pytest.approx(cover_probabilities(0.0, 12.6, 0.0)[2])


def test_solve_centre_inverts_cover_probabilities():
    mu = solve_centre(0.55, 2.5, 12.6)
    assert cover_probabilities(mu, 12.6, 2.5)[0] == pytest.approx(0.55, abs=1e-4)
    assert solve_centre(0.5, 0.0, 12.6) == pytest.approx(0.0, abs=1e-3)


def test_price_game_leg_prices_the_offered_line_off_the_consensus_centre(cal, legs):
    home = price_game_leg(cal, legs["spread"], "home", preferred=("draftkings", "fanduel"))
    assert home["book"] == "fanduel" and home["line"] == -2.5  # the half point beats DK's -105 at -3
    assert home["edge"] == 0.5 and home["consensus_line"] == -3.0
    assert home["p"] > home["p_market"] and home["p_model"] is None and home["ev"] == home["ev_line"]
    assert home["player"] == "IND" and home["team"] == "IND" and home["position"] == "TEAM"
    away = price_game_leg(cal, legs["spread"], "away", preferred=("draftkings", "fanduel"))
    assert away["line"] == 3.0 and away["book"] == "draftkings" and away["team"] == "ATL"  # +3 -115 beats +2.5 -110 on this shape
    total = price_game_leg(cal, legs["total"], "under", preferred=("draftkings", "fanduel"))
    assert total["book"] == "fanduel" and total["line"] == 48.0 and total["consensus_line"] == 47.75 and total["edge"] == 0.25  # two books: the median is their mean
    assert total["push"] > 0 and total["player"] == "ATL at IND" and total["team"] is None
    ml = price_game_leg(cal, legs["moneyline"], "home", preferred=("draftkings", "fanduel"))
    assert ml["book"] == "fanduel" and ml["price"] == -150 and ml["line"] is None and ml["p"] == pytest.approx(ml["p_market"])
    assert price_game_leg(Calibration({}), legs["spread"], "home") is None  # no fitted shape: no off-consensus pricing
    assert price_game_leg(Calibration({}), legs["moneyline"], "home")["p"] == pytest.approx(ml["p"])  # a moneyline needs no shape


def test_price_game_legs_skips_kicked_off_games_and_labels_every_side(cal, event):
    from datetime import datetime, timezone

    legs = parse_game_lines(event)
    priced = price_game_legs(cal, legs, ["draftkings", "fanduel"], datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc))
    assert len(priced) == 6 and {leg_label(e) for e in priced} == {"IND -2.5 spread", "ATL +3 spread", "ATL at IND Over 47.5", "ATL at IND Under 48", "IND moneyline", "ATL moneyline"}
    assert all(e["player_id"] == "game:ATL@IND" and e["game"] == "e9" for e in priced)
    assert price_game_legs(cal, legs, ["draftkings"], datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)) == []


def test_game_rho_reads_team_relations_and_rejects_one_event_twice(cal):
    spread = {"market": "spread", "side": "home", "team": "IND", "player": "IND", "game": "e9", "p": 0.52, "consensus_line": -3.0, "p_market": 0.5}
    dog = {"market": "spread", "side": "away", "team": "ATL", "player": "ATL", "game": "e9", "p": 0.5, "consensus_line": 3.0, "p_market": 0.5}
    ml = {"market": "moneyline", "side": "home", "team": "IND", "player": "IND", "game": "e9", "p": 0.6, "consensus_line": None, "p_market": 0.6}
    over = {"market": "total", "side": "over", "team": None, "player": "ATL at IND", "game": "e9", "p": 0.5}
    rb = {"market": "player_rush_yds", "side": "over", "team": "IND", "player": "J Taylor", "position": "RB", "game": "e9", "p": 0.55}
    qb = {"market": "player_pass_yds", "side": "under", "team": "ATL", "player": "M Penix", "position": "QB", "game": "e9", "p": 0.55}
    assert game_rho(cal, rb, spread) == pytest.approx(0.24) and game_rho(cal, rb, dog) == pytest.approx(-0.24)
    assert game_rho(cal, {**rb, "side": "under"}, spread) == pytest.approx(-0.24)
    assert game_rho(cal, qb, over) == pytest.approx(-0.38) and game_rho(cal, qb, {**over, "side": "under"}) == pytest.approx(0.38)
    assert favourite_sign(spread) == 1 and favourite_sign(dog) == -1 and favourite_sign(ml) == 1
    assert game_rho(cal, spread, over) == pytest.approx(0.04) and game_rho(cal, dog, over) == pytest.approx(-0.04)
    assert game_rho(cal, spread, {**rb, "game": "e2"}) == 0.0
    assert game_rho(cal, {**rb, "team": None}, spread) == 0.0  # a prop without a team is unknown context, not the opponent
    assert game_rho(cal, ml, over) == pytest.approx(0.04) and game_rho(cal, {**ml, "p_market": 0.4}, over) == pytest.approx(-0.04)
    with pytest.raises(ValueError):
        game_rho(cal, spread, dog)  # both sides of one market
    with pytest.raises(ValueError):
        game_rho(cal, dog, ml)  # a spread and a moneyline on one game are one bet
    joint = parlay_probability(cal, [rb, spread])  # the props parlay delegates to the game rules
    assert joint["rho"] == pytest.approx(0.24) and joint["correlated"] > joint["independent"]
    with pytest.raises(ValueError):
        parlay_probability(cal, [spread, ml])  # same team twice


def test_grade_game_leg_settles_on_the_final_score():
    scores = {"home_score": 24.0, "away_score": 20.0, "gp": 1}
    assert grade_game_leg({"market": "spread", "side": "home", "line": -3.0}, scores) == "win"
    assert grade_game_leg({"market": "spread", "side": "home", "line": -4.0}, scores) == "push"
    assert grade_game_leg({"market": "spread", "side": "away", "line": 3.0}, scores) == "loss"
    assert grade_game_leg({"market": "moneyline", "side": "away", "line": None}, scores) == "loss"
    assert grade_game_leg({"market": "moneyline", "side": "home", "line": None}, {**scores, "away_score": 24.0}) == "push"
    assert grade_game_leg({"market": "total", "side": "over", "line": 43.5}, scores) == "win"
    assert grade_game_leg({"market": "total", "side": "under", "line": 44.0}, scores) == "push"
    assert grade_game_leg({"market": "total", "side": "under", "line": 44.5}, scores) == "win"
    assert grade_game_leg({"market": "total", "side": "over", "line": 44.5}, None) == "void"
    assert grade_leg({"market": "spread", "side": "home", "line": -3.0, "player_id": "game:ATL@IND"}, scores) == "win"  # the props log delegates


def test_game_scores_keys_played_games_the_way_legs_are_identified():
    games = [{"week": 1, "home": "IND", "away": "ATL", "home_score": 24.0, "away_score": 20.0},
             {"week": 1, "home": "KC", "away": "DEN", "home_score": None, "away_score": None},
             {"week": 2, "home": "ATL", "away": "IND", "home_score": 10.0, "away_score": 3.0}]
    assert game_scores(games, 1) == {"game:ATL@IND": {"home_score": 24.0, "away_score": 20.0, "gp": 1}}
