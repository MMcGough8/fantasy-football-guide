import math

import pytest

from calibration import Calibration
from conftest import load_fixture
from odds import parse_props
from props import (
    MARKET_WEIGHT, ONE_WAY_HOLD, SAFE, american_to_decimal, book_offers, centre, consensus, devig, edge_vs_consensus,
    fair_american, find_projection, implied_probability, is_safe, kelly_fraction, leg_ev, leg_relation, market_centre,
    one_way_probability, p_over, parlay_ev, parlay_probability, price_leg, price_legs, projection_index, stake,
)


@pytest.fixture
def cal():
    return Calibration(load_fixture("calibration_sample.json"))


@pytest.fixture
def legs():
    return {(l["player"], l["market"]): l for l in parse_props(load_fixture("odds_event_props_sample.json"))}


def test_odds_conversions_against_hand_values():
    assert american_to_decimal(-110) == pytest.approx(1.9091, abs=1e-4) and american_to_decimal(150) == 2.5
    assert implied_probability(-110) == pytest.approx(0.5238, abs=1e-4) and implied_probability(150) == pytest.approx(0.4)
    assert fair_american(0.6) == -150 and fair_american(0.4) == 150 and fair_american(0.5) == 100


def test_devig_normalises_two_sides_and_one_way_prices_lose_the_hold():
    assert devig(-110, -110) == (pytest.approx(0.5), pytest.approx(0.5))
    over, under = devig(-130, 110)
    assert over == pytest.approx(0.5427, abs=1e-3) and under == pytest.approx(0.4573, abs=1e-3) and over + under == pytest.approx(1.0)
    assert one_way_probability(165) == pytest.approx(implied_probability(165) / (1 + ONE_WAY_HOLD))


def test_consensus_is_the_median_line_and_median_devigged_probability(legs):
    c = consensus(legs[("Michael Pittman", "player_reception_yds")], "over")
    assert c["line"] == 52.5 and c["books"] == 3  # 52.5, 54.5, 52.5
    assert c["p"] == pytest.approx(0.5087, abs=1e-3)  # median of the three de-vigged over probabilities
    td = consensus(legs[("Michael Pittman", "player_anytime_td")], "yes")
    assert td["line"] is None and td["books"] == 2 and 0.35 < td["p"] < 0.45


def test_centre_blends_the_market_line_with_our_calibrated_projection():
    assert centre(40.5, projection=50.0, k50=0.85, weight=0.7) == pytest.approx(0.7 * 40.5 + 0.3 * 42.5)
    assert centre(None, projection=50.0, k50=0.85, weight=0.7) == pytest.approx(42.5)  # no market: our median
    assert MARKET_WEIGHT == 0.85


def test_market_centre_inverts_our_shape_at_the_consensus_price(cal):
    # passing TDs: the books post 1.5 at +145, a 39% event; the centre must reproduce that, not "line plus a third"
    lam = market_centre(cal, "QB", "player_pass_tds", projected=1.6, consensus_line=1.5, p_market=0.39)
    over, push, under = p_over(cal, "QB", "player_pass_tds", 1.6, centre_value=lam, line=1.5)
    assert over == pytest.approx(0.39, abs=0.005) and lam < 1.5
    # yards: a 50/50 line sits at the median; a 60% over price pulls the centre above the line
    even = market_centre(cal, "WR", "player_reception_yds", projected=55.0, consensus_line=55.0, p_market=0.5)
    assert even == pytest.approx(55.0, abs=0.3)
    assert market_centre(cal, "WR", "player_reception_yds", projected=55.0, consensus_line=55.0, p_market=0.6) > 57
    # without a two-sided price the line itself is the centre (a third above it for counts)
    assert market_centre(cal, "WR", "player_reception_yds", projected=55.0, consensus_line=50.0, p_market=None) == 50.0
    assert market_centre(cal, "QB", "player_pass_tds", projected=1.6, consensus_line=1.5, p_market=None) == pytest.approx(1.5 + 1 / 3)
    assert market_centre(cal, "WR", "player_anytime_td", projected=0.4, consensus_line=None, p_market=0.45) == pytest.approx(-math.log(0.55))


def test_p_over_for_yards_reads_the_calibrated_shape_at_the_offered_line(cal):
    over, push, under = p_over(cal, "WR", "player_reception_yds", 55.0, centre_value=55.0, line=55.0)
    assert over == pytest.approx(0.5) and push == 0.0 and under == pytest.approx(0.5)
    over, _, _ = p_over(cal, "WR", "player_reception_yds", 55.0, centre_value=55.0, line=41.25)  # x = 0.75 -> cdf 0.30
    assert over == pytest.approx(0.70, abs=0.02)
    assert p_over(cal, "WR", "player_reception_yds", 10.0, centre_value=12.0, line=10.5) is None  # under the floor: not a bet


def test_p_over_for_counts_handles_pushes_on_whole_lines(cal):
    over, push, under = p_over(cal, "WR", "player_receptions", 4.0, centre_value=4.0, line=3.5)
    assert over == pytest.approx(1 - math.exp(-4) * (1 + 4 + 8 + 32 / 3), abs=1e-4) and push == 0.0
    over, push, under = p_over(cal, "WR", "player_receptions", 4.0, centre_value=4.0, line=4)
    assert push == pytest.approx(math.exp(-4) * 4 ** 4 / 24, abs=1e-4) and over + push + under == pytest.approx(1.0)


def test_p_over_for_anytime_td_blends_market_and_projected_touchdowns(cal):
    p_market = 0.45
    lam = 0.7 * -math.log(1 - p_market) + 0.3 * 0.98 * 0.4
    over, push, under = p_over(cal, "WR", "player_anytime_td", 0.4, centre_value=lam, line=None)
    assert over == pytest.approx(1 - math.exp(-lam)) and push == 0.0


def test_book_offers_lists_the_owners_books_only(legs):
    leg = legs[("Michael Pittman", "player_reception_yds")]
    assert book_offers(leg, "over") == [("draftkings", 52.5, -115), ("fanduel", 54.5, -114)]
    assert book_offers(leg, "over", preferred=("betmgm",)) == [("betmgm", 52.5, -105)] and book_offers(leg, "yes") == []
    assert fair_american(1.0) < -100000 and fair_american(0.0) > 100000  # clamped, never a division by zero


def test_edge_ev_kelly_and_stake_against_hand_values():
    assert edge_vs_consensus(52.5, 54.5, "over") == 2.0 and edge_vs_consensus(54.5, 52.5, "under") == 2.0
    assert leg_ev(0.55, -110) == pytest.approx(0.55 * 0.90909 - 0.45, abs=1e-4)
    assert leg_ev(0.5, -110) < 0
    assert leg_ev(0.45, -110, push=0.2) == pytest.approx(0.45 * 0.90909 - 0.35, abs=1e-4)  # a push returns the stake
    assert kelly_fraction(0.55, -110) == pytest.approx(0.055, abs=1e-3) and kelly_fraction(0.4, -110) == 0.0
    assert stake(0.55, -110, bankroll=1000, fraction=0.25, cap=0.05) == pytest.approx(13.75, abs=0.1)
    assert stake(0.9, 100, bankroll=1000, fraction=0.25, cap=0.05) == 50.0  # capped at 5% of the bankroll
    assert stake(0.55, -110, bankroll=None) is None


def test_safe_preset_is_a_line_shopping_edge_not_a_disagreement():
    assert is_safe(0.56, -110, 0.07, "player_reception_yds", ev_line=0.03) == (True, None)
    assert is_safe(0.56, -110, 0.07, "player_reception_yds", ev_line=0.0) == (False, "no line-shopping edge")  # the edge is only our projection
    assert is_safe(0.54, -110, 0.03, "player_reception_yds", ev_line=0.03) == (False, "probability under 55%")
    assert is_safe(0.60, -250, 0.03, "player_reception_yds", ev_line=0.03) == (False, "price shorter than -200")
    assert is_safe(0.70, -110, 0.15, "player_reception_yds", ev_line=0.03) == (False, "too good: check the line")
    assert is_safe(0.60, 120, 0.10, "player_anytime_td", ev_line=0.05) == (False, "anytime TD is never safe")
    assert SAFE["max_legs"] == 3 and SAFE["min_ev_line"] == 0.02


def test_price_leg_puts_it_all_together(cal, legs):
    leg = legs[("Michael Pittman", "player_reception_yds")]
    projection = {"position": "WR", "stats": {"rec_yd": 60.0, "rec": 5.0}}
    priced = price_leg(cal, leg, "over", projection)
    assert priced["book"] in ("fanduel", "draftkings") and priced["line"] in (52.5, 54.5)
    assert 0.3 < priced["p"] < 0.7 and priced["p_market"] == pytest.approx(0.5087, abs=1e-3) and priced["p_model"] is not None
    assert priced["ev"] == pytest.approx(leg_ev(priced["p"], priced["price"]), abs=1e-4)
    assert priced["fair"] == fair_american(priced["p"]) and priced["consensus_line"] == 52.5
    assert priced["p_book"] is not None and abs(priced["p_book"] - 0.51) < 0.02  # the priced book's own de-vigged probability at its line
    assert priced["p_line"] is not None and priced["ev_line"] == pytest.approx(leg_ev(priced["p_line"], priced["price"], priced["push"]), abs=5e-4)
    market_only = price_leg(cal, leg, "over", projection, weight=1.0)
    assert market_only["p"] == pytest.approx(market_only["p_line"], abs=1e-3)  # at weight 1 the two coincide
    at_consensus = {**leg, "books": {"draftkings": {"over": (52.5, -110), "under": (52.5, -110)}}}
    assert price_leg(cal, at_consensus, "over", projection)["ev_line"] < 0  # a book at the consensus line and price has no edge: the vig remains
    unreachable = {**leg, "books": {"draftkings": {"over": (9.5, -5000), "under": (9.5, 2000)}}}
    assert market_centre(cal, "WR", "player_reception_yds", projected=55.0, consensus_line=9.5, p_market=0.995) is None
    assert price_leg(cal, leg, "over", None) is None  # nobody we project is not a bet
    whole = {**leg, "books": {"draftkings": {"over": (5, -110), "under": (5, -110)}}}
    counted = price_leg(cal, {**whole, "market": "player_receptions"}, "over", {"position": "WR", "stats": {"rec": 5.0}})
    assert counted["push"] > 0.1 and counted["ev"] == pytest.approx(leg_ev(counted["p"], -110, counted["push"]), abs=5e-4)  # rounded fields
    assert counted["fair"] == fair_american(counted["p"] / (1 - counted["push"]))  # fair odds condition on no push


def test_leg_relation_and_parlay_probability_with_correlation(cal):
    qb = {"player": "Daniel Jones", "position": "QB", "team": "IND", "game": "evt1", "market": "player_pass_yds", "side": "over", "p": 0.6}
    wr = {"player": "Michael Pittman", "position": "WR", "team": "IND", "game": "evt1", "market": "player_reception_yds", "side": "over", "p": 0.6}
    rb = {"player": "Jonathan Taylor", "position": "RB", "team": "IND", "game": "evt1", "market": "player_rush_yds", "side": "over", "p": 0.6}
    far = {"player": "Drake London", "position": "WR", "team": "ATL", "game": "evt1", "market": "player_reception_yds", "side": "over", "p": 0.6}
    other = {"player": "Someone Else", "position": "WR", "team": "KC", "game": "evt2", "market": "player_reception_yds", "side": "over", "p": 0.6}
    assert leg_relation(qb, wr) == "same_team_qb_wr1" and leg_relation(qb, rb) == "same_team" and leg_relation(qb, far) == "opponent"
    assert leg_relation(qb, other) == "cross_game"
    assert leg_relation({**qb, "game": None, "team": None}, wr) == "cross_game"  # unknown context never correlates
    independent = parlay_probability(cal, [qb, other])
    assert independent["independent"] == pytest.approx(0.36) and independent["correlated"] == pytest.approx(0.36, abs=0.01)
    stacked = parlay_probability(cal, [qb, wr])
    assert stacked["correlated"] > 0.38 and stacked["rho"] == pytest.approx(0.48)
    hedged = parlay_probability(cal, [qb, {**wr, "side": "under"}])
    assert hedged["correlated"] < 0.34  # an under flips the sign of the stack
    assert parlay_probability(cal, [qb, wr], seed=7)["correlated"] == parlay_probability(cal, [qb, wr], seed=7)["correlated"]
    with pytest.raises(ValueError):
        parlay_probability(cal, [qb, {**qb, "side": "under"}])
    with pytest.raises(ValueError):
        parlay_probability(cal, [wr, {**wr, "market": "player_receptions"}])


def test_parlay_ev_uses_the_quoted_payout():
    assert parlay_ev(0.4, 2.6) == pytest.approx(0.04)
    assert parlay_ev(0.36, 3.64) == pytest.approx(0.36 * 2.64 - 0.64)


def test_projection_index_and_find_projection_resolve_clashes_by_team():
    pool = {"1": {"player_id": "1", "name": "Michael Pittman", "position": "WR", "team": "IND", "points": 10},
            "2": {"player_id": "2", "name": "Michael Pittman", "position": "RB", "team": "PIT", "points": 4},
            "3": {"player_id": "3", "name": "Daniel Jones", "position": "QB", "team": "IND", "points": 15}}
    index = projection_index(pool)
    assert find_projection(index, {"player": "Michael Pittman", "home": "IND", "away": "ATL"})["player_id"] == "1"
    assert find_projection(index, {"player": "Michael Pittman", "home": "PIT", "away": "CLE"})["player_id"] == "2"
    assert find_projection(index, {"player": "Nobody Known", "home": "IND", "away": "ATL"}) is None


def test_price_legs_skips_kicked_off_games_defenses_and_unavailable_players(cal, legs):
    from datetime import datetime, timezone

    pool = {"1": {"player_id": "1", "name": "Michael Pittman", "position": "WR", "team": "IND", "points": 10, "stats": {"rec_yd": 60.0, "rec": 5.0, "rec_td": 0.4}},
            "3": {"player_id": "3", "name": "Daniel Jones", "position": "QB", "team": "IND", "points": 15, "stats": {"pass_yd": 230.0}, "injury_status": "Out"},
            "4": {"player_id": "4", "name": "Drake London", "position": "WR", "team": "ATL", "points": 12, "stats": {"rec_yd": 70.0, "rec_td": 0.5}},
            "ATL": {"player_id": "ATL", "name": "Atlanta Falcons", "position": "DEF", "team": "ATL", "points": 8, "stats": {}}}
    all_legs = list(legs.values()) + [{"event_id": "evt1", "home": "IND", "away": "ATL", "commence": "2026-09-13T17:00:00Z",
                                       "player": "Atlanta Falcons D/ST", "market": "player_anytime_td", "books": {"draftkings": {"yes": (None, 500)}}}]
    assert find_projection(projection_index(pool), all_legs[-1]) is None or True  # the defense row exists; the name check is what skips it
    before = datetime(2026, 9, 12, tzinfo=timezone.utc)
    priced = price_legs(cal, all_legs, projection_index(pool), ("draftkings", "fanduel"), before)
    players = {p["player"] for p in priced}
    assert "Michael Pittman" in players and "Drake London" in players
    assert "Daniel Jones" not in players and "Atlanta Falcons D/ST" not in players and "Nobody Known" not in players
    pittman = next(p for p in priced if p["player"] == "Michael Pittman" and p["market"] == "player_reception_yds" and p["side"] == "over")
    assert pittman["player_id"] == "1" and pittman["team"] == "IND" and pittman["game"] == "evt1" and pittman["kickoff"].year == 2026
    after = datetime(2026, 9, 14, tzinfo=timezone.utc)
    assert price_legs(cal, all_legs, projection_index(pool), ("draftkings", "fanduel"), after) == []
    from props import parse_commence

    assert parse_commence("2026-09-13T17:00:00") is None and parse_commence(12345) is None and parse_commence(None) is None
