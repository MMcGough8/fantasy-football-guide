import math

import pytest

from calibration import Calibration
from conftest import load_fixture
from odds import parse_props
from props import (
    MARKET_WEIGHT, ONE_WAY_HOLD, SAFE, american_to_decimal, best_price, centre, consensus, devig, edge_vs_consensus,
    fair_american, implied_probability, is_safe, kelly_fraction, leg_ev, leg_relation, one_way_probability, p_over,
    parlay_ev, parlay_probability, price_leg, stake,
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
    assert MARKET_WEIGHT == 0.7


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


def test_best_price_prefers_the_owners_books_and_the_highest_payout(legs):
    leg = legs[("Michael Pittman", "player_reception_yds")]
    assert best_price(leg, "over") == ("draftkings", 52.5, -115)  # the lower line wins for an over
    assert best_price(leg, "under") == ("fanduel", 54.5, -106)  # for an under the higher line wins before the price
    assert best_price(leg, "over", preferred=("betmgm",)) == ("betmgm", 52.5, -105)
    assert best_price(leg, "yes") is None


def test_edge_ev_kelly_and_stake_against_hand_values():
    assert edge_vs_consensus(52.5, 54.5, "over") == 2.0 and edge_vs_consensus(54.5, 52.5, "under") == 2.0
    assert leg_ev(0.55, -110) == pytest.approx(0.55 * 0.90909 - 0.45, abs=1e-4)
    assert leg_ev(0.5, -110) < 0
    assert kelly_fraction(0.55, -110) == pytest.approx(0.055, abs=1e-3) and kelly_fraction(0.4, -110) == 0.0
    assert stake(0.55, -110, bankroll=1000, fraction=0.25, cap=0.05) == pytest.approx(13.75, abs=0.1)
    assert stake(0.9, 100, bankroll=1000, fraction=0.25, cap=0.05) == 50.0  # capped at 5% of the bankroll
    assert stake(0.55, -110, bankroll=None) is None


def test_safe_preset_accepts_and_rejects_the_boundaries():
    assert is_safe(0.56, -110, 0.07, "player_reception_yds") == (True, None)
    assert is_safe(0.54, -110, 0.03, "player_reception_yds") == (False, "probability under 55%")
    assert is_safe(0.60, -250, 0.03, "player_reception_yds") == (False, "price shorter than -200")
    assert is_safe(0.60, -110, 0.01, "player_reception_yds") == (False, "expected value under 2%")
    assert is_safe(0.70, -110, 0.15, "player_reception_yds") == (False, "too good: check the line")
    assert is_safe(0.60, 120, 0.10, "player_anytime_td") == (False, "anytime TD is never safe")
    assert SAFE["max_legs"] == 2


def test_price_leg_puts_it_all_together(cal, legs):
    leg = legs[("Michael Pittman", "player_reception_yds")]
    projection = {"position": "WR", "stats": {"rec_yd": 60.0, "rec": 5.0}}
    priced = price_leg(cal, leg, "over", projection)
    assert priced["book"] in ("fanduel", "draftkings") and priced["line"] in (52.5, 54.5)
    assert 0.3 < priced["p"] < 0.7 and priced["p_market"] == pytest.approx(0.5087, abs=1e-3) and priced["p_model"] is not None
    assert priced["ev"] == pytest.approx(leg_ev(priced["p"], priced["price"]), abs=1e-4)
    assert priced["fair"] == fair_american(priced["p"]) and priced["consensus_line"] == 52.5
    assert price_leg(cal, leg, "over", None) is None  # nobody we project is not a bet


def test_leg_relation_and_parlay_probability_with_correlation(cal):
    qb = {"player": "Daniel Jones", "position": "QB", "team": "IND", "game": "evt1", "market": "player_pass_yds", "side": "over", "p": 0.6}
    wr = {"player": "Michael Pittman", "position": "WR", "team": "IND", "game": "evt1", "market": "player_reception_yds", "side": "over", "p": 0.6}
    rb = {"player": "Jonathan Taylor", "position": "RB", "team": "IND", "game": "evt1", "market": "player_rush_yds", "side": "over", "p": 0.6}
    far = {"player": "Drake London", "position": "WR", "team": "ATL", "game": "evt1", "market": "player_reception_yds", "side": "over", "p": 0.6}
    other = {"player": "Someone Else", "position": "WR", "team": "KC", "game": "evt2", "market": "player_reception_yds", "side": "over", "p": 0.6}
    assert leg_relation(qb, wr) == "same_team_qb_wr1" and leg_relation(qb, rb) == "same_team" and leg_relation(qb, far) == "opponent"
    assert leg_relation(qb, other) == "cross_game"
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
