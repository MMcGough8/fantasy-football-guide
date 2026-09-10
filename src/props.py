"""Pricing player props and small parlays against the books' own lines.

The market sets the centre of every leg's outcome distribution (the consensus line across
books), our calibrated tables give the shape around it, and our projection nudges the centre
by (1 - MARKET_WEIGHT). Each leg is then priced at the *offered* line of each book the owner
uses, which is where line shopping pays. Everything here is pure; `odds.py` fetches and
`props_log.py` records.
"""
import math
from statistics import median

import numpy as np

from calibration import correlation, count_pmf, normal_ppf, outcome_cdf

MARKET_STAT = {
    "player_pass_yds": "pass_yd", "player_pass_tds": "pass_td", "player_rush_yds": "rush_yd",
    "player_receptions": "rec", "player_reception_yds": "rec_yd", "player_anytime_td": "td",
}
COUNT_MARKETS = {"player_receptions", "player_pass_tds"}
TD_MARKET = "player_anytime_td"
PREFERRED_BOOKS = ("draftkings", "fanduel")
MARKET_WEIGHT = 0.7  # the books are sharp; our projection moves the centre by the rest
ONE_WAY_HOLD = 0.05  # a Yes-only anytime-TD price carries about this much hold; re-measured by the log
COUNT_LINE_TO_MEAN = 1 / 3  # a Poisson's mean sits about a third above its median line
SAFE = {"min_probability": 0.55, "min_ev": 0.02, "min_price": -200, "max_ev": 0.12, "max_legs": 2}
MC_DRAWS = 20000
MAX_RHO = 0.95


# ---- odds arithmetic ----

def american_to_decimal(price):
    return 1 + price / 100 if price > 0 else 1 + 100 / abs(price)


def implied_probability(price):
    return 1 / american_to_decimal(price)


def fair_american(p):
    """The break-even American price for a probability (clamped away from 0 and 1)."""
    decimal = 1 / min(max(p, 1e-4), 1 - 1e-4)
    return int(round((decimal - 1) * 100)) if decimal >= 2 else -int(round(100 / (decimal - 1)))


def devig(over_price, under_price):
    """The two sides' probabilities with the book's hold removed (they sum to one)."""
    over, under = implied_probability(over_price), implied_probability(under_price)
    return over / (over + under), under / (over + under)


def one_way_probability(price, hold=ONE_WAY_HOLD):
    return implied_probability(price) / (1 + hold)


# ---- the market ----

def _book_probability(offers, side):
    """This book's de-vigged probability for `side`, or None without the other side."""
    if side == "yes":
        if "yes" in offers and "no" in offers:
            return devig(offers["yes"][1], offers["no"][1])[0]
        return one_way_probability(offers["yes"][1]) if "yes" in offers else None
    if "over" in offers and "under" in offers:
        over, under = devig(offers["over"][1], offers["under"][1])
        return over if side == "over" else under
    return None


def consensus(leg, side):
    """The median line across books and the median de-vigged probability for `side`."""
    lines = [offers[side][0] for offers in leg["books"].values() if side in offers and offers[side][0] is not None]
    probabilities = [p for p in (_book_probability(offers, side) for offers in leg["books"].values()) if p is not None]
    return {
        "line": median(lines) if lines else None,
        "p": median(probabilities) if probabilities else None,
        "books": sum(1 for offers in leg["books"].values() if side in offers),
    }


def centre(consensus_line, projection, k50, weight=MARKET_WEIGHT):
    """Where the outcome's median sits: the market line, nudged toward our calibrated median."""
    ours = projection * k50
    return ours if consensus_line is None else weight * consensus_line + (1 - weight) * ours


def _count_probabilities(pmf, line):
    if line is None:
        return None
    push = pmf.get(int(line), 0.0) if float(line).is_integer() else 0.0
    over = sum(p for k, p in pmf.items() if k > line)
    return over, push, max(0.0, 1.0 - over - push)


def p_over(cal, position, market, projection_stat, centre_value, line):
    """(P(over), P(push), P(under)) at `line` for a distribution centred on `centre_value`;
    None when our tables cannot price it (below the floor, no table, no line)."""
    if market == TD_MARKET:
        p = 1.0 - math.exp(-centre_value)
        return p, 0.0, 1.0 - p
    stat = MARKET_STAT[market]
    if market in COUNT_MARKETS:
        return _count_probabilities(count_pmf(cal, position, stat, centre_value), line)
    if line is None or centre_value <= 0:
        return None
    cdf = outcome_cdf(cal, position, stat, projection_stat, line / centre_value)
    if cdf is None:
        return None
    return 1.0 - cdf, 0.0, cdf


# ---- pricing a leg ----

def book_offers(leg, side, preferred=PREFERRED_BOOKS):
    return [(book, *leg["books"][book][side]) for book in preferred if side in leg["books"].get(book, {})]


def edge_vs_consensus(book_line, consensus_line, side):
    """Yards (or receptions) of room the book gives beyond the consensus, positive when friendly."""
    if book_line is None or consensus_line is None:
        return 0.0
    return consensus_line - book_line if side == "over" else book_line - consensus_line


def leg_ev(p, price, push=0.0):
    """Expected profit per dollar staked; a push returns the stake."""
    return p * (american_to_decimal(price) - 1) - (1 - p - push)


def no_push_probability(p, push):
    """P(win) given the bet is not a push: what fair odds and stakes condition on."""
    return p if push >= 1 else p / (1 - push)


def kelly_fraction(p, price):
    b = american_to_decimal(price) - 1
    return max(0.0, (p * b - (1 - p)) / b)


def stake(p, price, bankroll, fraction=0.25, cap=0.05):
    """A quarter-Kelly stake capped at `cap` of the bankroll; None without a bankroll."""
    if not bankroll:
        return None
    return round(min(cap, fraction * kelly_fraction(p, price)) * bankroll, 2)


def is_safe(p, price, ev, market, rules=SAFE):
    """Whether a leg fits the low-variance, positive-expectation brief; (ok, reason)."""
    if market == TD_MARKET:
        return False, "anytime TD is never safe"
    if p < rules["min_probability"]:
        return False, f"probability under {rules['min_probability']:.0%}"
    if price < rules["min_price"]:
        return False, f"price shorter than {rules['min_price']}"
    if ev > rules["max_ev"]:
        return False, "too good: check the line"
    if ev < rules["min_ev"]:
        return False, f"expected value under {rules['min_ev']:.0%}"
    return True, None


def _projected_stat(projection, market):
    stats = projection.get("stats") or {}
    if market == TD_MARKET:
        return (stats.get("rush_td") or 0) + (stats.get("rec_td") or 0)
    return stats.get(MARKET_STAT[market]) or 0.0


def _centre_for(cal, position, market, projected, consensus_line, p_market, weight):
    """The blended centre: a median for yards, a mean for counts, a rate for touchdowns."""
    if market == TD_MARKET:
        k = (cal.data.get("touchdowns") or {}).get("k", 1.0)
        ours = k * projected
        return ours if p_market is None else weight * -math.log(1 - p_market) + (1 - weight) * ours
    stat = MARKET_STAT[market]
    if market in COUNT_MARKETS:
        fit = cal.count_fit(position, stat) or {}
        if projected < (fit.get("floor") or 0):
            return None
        ours = projected * fit.get("mean_ratio", 1.0)
        return ours if consensus_line is None else weight * (consensus_line + COUNT_LINE_TO_MEAN) + (1 - weight) * ours
    k50 = cal.k50(position, stat, projected)
    return None if k50 is None else centre(consensus_line, projected, k50, weight)


def price_leg(cal, leg, side, projection, weight=MARKET_WEIGHT, preferred=PREFERRED_BOOKS):
    """The best offer on the owner's books with our probability, the market's, and the EV.
    None when the player is not projected or the market cannot be priced."""
    if projection is None:
        return None
    market, position = leg["market"], projection["position"]
    projected = _projected_stat(projection, market)
    cons = consensus(leg, side)
    blended = _centre_for(cal, position, market, projected, cons["line"], cons["p"], weight)
    ours_only = _centre_for(cal, position, market, projected, None, None, weight)
    if blended is None:
        return None
    best = None
    for book, line, price in book_offers(leg, side, preferred):
        probs = p_over(cal, position, market, projected, blended, line)
        if probs is None:
            continue
        over, push, under = probs
        p = over if side in ("over", "yes") else under
        candidate = {"book": book, "line": line, "price": price, "p": round(p, 4), "push": round(push, 4), "ev": round(leg_ev(p, price, push), 4),
                     "p_book": _book_probability(leg["books"][book], side)}
        if best is None or candidate["ev"] > best["ev"]:
            best = candidate
    if best is None:
        return None
    model = p_over(cal, position, market, projected, ours_only, best["line"]) if ours_only else None
    p_model = None if model is None else round(model[0] if side in ("over", "yes") else model[2], 4)
    win = no_push_probability(best["p"], best["push"])
    return {
        **best, "p_market": None if cons["p"] is None else round(cons["p"], 4), "p_model": p_model,
        "p_book": None if best["p_book"] is None else round(best["p_book"], 4), "p_win": round(win, 4),
        "fair": fair_american(win), "consensus_line": cons["line"], "edge": edge_vs_consensus(best["line"], cons["line"], side),
        "books": cons["books"], "market": market, "side": side, "player": leg["player"], "position": position,
    }


# ---- parlays ----

def leg_relation(a, b):
    if a["player"] == b["player"]:
        return "same_player"
    if not a.get("game") or not b.get("game") or a.get("game") != b.get("game") or not a.get("team") or not b.get("team"):
        return "cross_game"  # unknown context never correlates
    if a.get("team") != b.get("team"):
        return "opponent"
    positions, markets = {a["position"], b["position"]}, {a["market"], b["market"]}
    if positions == {"QB", "WR"} and markets == {"player_pass_yds", "player_reception_yds"}:
        return "same_team_qb_wr1"
    if positions == {"QB", "TE"} and markets == {"player_pass_yds", "player_reception_yds"}:
        return "same_team_qb_te1"
    return "same_team"


def _rho(cal, a, b):
    relation = leg_relation(a, b)
    if relation == "same_player":
        raise ValueError(f"two legs on {a['player']} cannot share a parlay")
    rho = correlation(cal, a["market"], b["market"], relation)
    if (a.get("side") == "under") != (b.get("side") == "under"):
        rho = -rho
    return max(-MAX_RHO, min(MAX_RHO, rho))


def parlay_probability(cal, legs, draws=MC_DRAWS, seed=1):
    """Joint hit probability: independent product and a Gaussian-copula Monte Carlo with the
    fitted same-game correlations (negated for an under leg). Seeded, so it is reproducible."""
    n = len(legs)
    if n < 2:
        raise ValueError("a parlay needs at least two legs")
    matrix = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            matrix[i, j] = matrix[j, i] = _rho(cal, legs[i], legs[j])
    thresholds = np.array([normal_ppf(leg["p"]) for leg in legs])
    z = np.random.default_rng(seed).multivariate_normal(np.zeros(n), matrix, size=draws, method="svd")  # svd tolerates a rough matrix
    independent = float(np.prod([leg["p"] for leg in legs]))
    off_diagonal = [matrix[i, j] for i in range(n) for j in range(i + 1, n)]
    return {"independent": round(independent, 4), "correlated": round(float(np.mean(np.all(z < thresholds, axis=1))), 4),
            "rho": round(float(np.mean(off_diagonal)), 4)}


def parlay_ev(p_joint, payout_decimal):
    """Expected profit per dollar at the payout the book quotes (its own correlation pricing)."""
    return p_joint * (payout_decimal - 1) - (1 - p_joint)
