"""Team markets (spread, total, moneyline) priced the way the props are: the market's consensus
is the centre, a normal spread of the final margin (or points) around the closing line, fitted on
three seasons of nflverse scores (`calibration.json` "games"), is the shape, and each side is
priced at the offered line of the owner's books. No projection of ours enters, so the only edge
is line shopping. Grading settles on the final score. Pure; `odds.py` fetches.
"""
from statistics import median

from calibration import correlation, normal_cdf
from odds_math import PREFERRED_BOOKS, devig, fair_american, leg_ev, no_push_probability, parse_commence

GAME_MARKETS = ("spread", "total", "moneyline")
MARGIN_MARKETS = ("spread", "moneyline")  # both settle on a team's margin
SIDES = {"spread": ("home", "away"), "moneyline": ("home", "away"), "total": ("over", "under")}
OTHER_SIDE = {"home": "away", "away": "home", "over": "under", "under": "over"}
POSITIONS = {"spread": "TEAM", "moneyline": "TEAM", "total": "GAME"}
SD_KEY = {"spread": "margin_sd", "moneyline": "margin_sd", "total": "total_sd"}
BISECTION_STEPS = 40
CENTRE_SEARCH_SDS = 6


def is_game_leg(leg):
    return leg.get("market") in GAME_MARKETS


# ---- the market ----

def _book_probability(offers, side):
    """This book's de-vigged probability for `side`, or None without the other side."""
    other = OTHER_SIDE[side]
    if side in offers and other in offers:
        return devig(offers[side][1], offers[other][1])[0]
    return None


def game_consensus(leg, side):
    """The median line across books for `side` (None for a moneyline) and the median de-vigged probability."""
    lines = [offers[side][0] for offers in leg["books"].values() if side in offers and offers[side][0] is not None]
    probabilities = [p for p in (_book_probability(offers, side) for offers in leg["books"].values()) if p is not None]
    return {"line": median(lines) if lines else None, "p": median(probabilities) if probabilities else None,
            "books": sum(1 for offers in leg["books"].values() if side in offers)}


# ---- the shape ----

def cover_probabilities(mu, sd, need):
    """(P(win), P(push), P(loss)) that an integer-valued outcome ~ Normal(mu, sd) beats `need`:
    a margin must exceed minus the spread, a total must exceed its line. A whole-number `need`
    can land exactly (a push); the half-unit continuity correction keeps the integers honest."""
    if float(need).is_integer():
        at_or_below = normal_cdf((need + 0.5 - mu) / sd)
        win = 1.0 - at_or_below
        push = at_or_below - normal_cdf((need - 0.5 - mu) / sd)
    else:
        win, push = 1.0 - normal_cdf((need - mu) / sd), 0.0
    return win, push, max(0.0, 1.0 - win - push)


def solve_centre(p, need, sd):
    """The mean at which the outcome beats `need` with no-push probability `p` (bisection: the
    probability rises with the mean)."""
    lo, hi = need - CENTRE_SEARCH_SDS * sd, need + CENTRE_SEARCH_SDS * sd
    for _ in range(BISECTION_STEPS):
        mid = (lo + hi) / 2
        win, push, _ = cover_probabilities(mid, sd, need)
        lo, hi = (mid, hi) if no_push_probability(win, push) < p else (lo, mid)
    return (lo + hi) / 2


def _need(market, side, line):
    """What the side's outcome must beat: a team's margin must clear minus its spread, the points
    must clear the total (negated for the under, whose outcome is minus the points); a moneyline is a win."""
    if market == "moneyline":
        return 0.0
    if market == "spread":
        return -line
    return line if side == "over" else -line


# ---- pricing ----

def _edge(market, side, line, consensus_line):
    """Points the book gives beyond the consensus, positive when friendly (a moneyline has no line)."""
    if market == "moneyline" or line is None or consensus_line is None:
        return 0.0
    if market == "spread" or side == "under":
        return round(line - consensus_line, 2)
    return round(consensus_line - line, 2)


def _best_offer(leg, side, cons, mu, sd, preferred):
    """The owner's book with the best expected value for `side`, priced at its own line."""
    market, best = leg["market"], None
    for book in preferred:
        offer = leg["books"].get(book, {}).get(side)
        if offer is None:
            continue
        line, price = offer
        win, push = (cons["p"], 0.0) if market == "moneyline" else cover_probabilities(mu, sd, _need(market, side, line))[:2]
        candidate = {"book": book, "line": line, "price": price, "p": round(win, 4), "push": round(push, 4),
                     "ev": round(leg_ev(win, price, push), 4), "p_book": _book_probability(leg["books"][book], side)}
        if best is None or candidate["ev"] > best["ev"]:
            best = candidate
    return best


def _team(leg, side):
    return leg["home"] if side == "home" else leg["away"]


def price_game_leg(cal, leg, side, preferred=PREFERRED_BOOKS):
    """One side of a game market at the owner's best book, in the same shape as a priced prop
    (`p_model` is None: nothing of ours enters). None without a two-sided consensus, or without
    a fitted shape for a spread or total."""
    market = leg["market"]
    cons = game_consensus(leg, side)
    if cons["p"] is None:
        return None
    sd = cal.games().get(SD_KEY[market])
    if market != "moneyline" and (sd is None or cons["line"] is None):
        return None
    mu = None if market == "moneyline" else solve_centre(cons["p"], _need(market, side, cons["line"]), sd)
    best = _best_offer(leg, side, cons, mu, sd, preferred)
    if best is None:
        return None
    win = no_push_probability(best["p"], best["push"])
    on_team = market in MARGIN_MARKETS
    return {
        **best, "p_line": best["p"], "ev_line": best["ev"], "p_market": round(cons["p"], 4), "p_model": None,
        "p_book": None if best["p_book"] is None else round(best["p_book"], 4), "p_win": round(win, 4), "fair": fair_american(win),
        "consensus_line": cons["line"], "edge": _edge(market, side, best["line"], cons["line"]), "books": cons["books"],
        "market": market, "side": side, "position": POSITIONS[market],
        "player": _team(leg, side) if on_team else f"{leg['away']} at {leg['home']}", "team": _team(leg, side) if on_team else None,
        "player_id": f"game:{leg['away']}@{leg['home']}", "game": leg["event_id"], "event_id": leg["event_id"],
        "home": leg["home"], "away": leg["away"], "commence": leg.get("commence"), "injury_status": None,
    }


def price_game_legs(cal, legs, books, now):
    """Every side of every game market that has not kicked off, priced on the owner's books."""
    priced = []
    for leg in legs:
        kickoff = parse_commence(leg.get("commence"))
        if kickoff and kickoff <= now:
            continue
        for side in SIDES[leg["market"]]:
            entry = price_game_leg(cal, leg, side, preferred=tuple(books))
            if entry is not None:
                priced.append({**entry, "kickoff": kickoff})
    return priced


def leg_label(entry):
    """How a game leg is named in the slip: 'IND -2.5 spread', 'IND moneyline', 'ATL at IND Over 47.5'."""
    market = entry["market"]
    if market == "spread":
        return f"{entry['player']} {entry['line']:+g} spread"
    if market == "moneyline":
        return f"{entry['player']} moneyline"
    return f"{entry['player']} {'Over' if entry['side'] == 'over' else 'Under'} {entry['line']:g}"


# ---- parlays ----

def favourite_sign(entry):
    """+1 for the favourite, -1 for the underdog, 0 at a pick'em, read from the side's consensus."""
    if entry["market"] == "spread":
        line = entry.get("consensus_line")
        return 0 if not line else (1 if line < 0 else -1)
    p = entry.get("p_market")
    return 0 if p is None or p == 0.5 else (1 if p > 0.5 else -1)


def _game_pair_rho(cal, a, b):
    if a["market"] == b["market"]:
        raise ValueError("both sides of one game market cannot share a parlay")
    if {a["market"], b["market"]} == set(MARGIN_MARKETS):
        raise ValueError("a spread and a moneyline on the same game are one bet: pick one")
    margin, total = (a, b) if a["market"] in MARGIN_MARKETS else (b, a)
    rho = correlation(cal, "spread", "total", "same_game") * favourite_sign(margin)
    return -rho if total["side"] == "under" else rho


def game_rho(cal, a, b):
    """Residual correlation for a pair with at least one game leg: a player's stat against his own
    team's margin (or the opponent's, sign flipped) or the game's points; the favourite covering
    against the over for two game legs; 0 across games. ValueError for two legs that are one bet."""
    if not a.get("game") or a.get("game") != b.get("game"):
        return 0.0
    if is_game_leg(a) and is_game_leg(b):
        return _game_pair_rho(cal, a, b)
    prop, game = (a, b) if is_game_leg(b) else (b, a)
    if not prop.get("team"):
        return 0.0  # unknown context never correlates
    if game["market"] == "total":
        relation = "same_game"
    else:
        relation = "same_team" if game.get("team") == prop.get("team") else "opponent"
    rho = correlation(cal, prop["market"], game["market"], relation)
    if prop.get("side") == "under":
        rho = -rho
    if game["market"] == "total" and game["side"] == "under":
        rho = -rho
    return rho


# ---- grading ----

def grade_game_leg(leg, scores):
    """win | loss | push | void for a game leg against {"home_score", "away_score"}."""
    if not scores or scores.get("home_score") is None or scores.get("away_score") is None:
        return "void"
    home, away = scores["home_score"], scores["away_score"]
    if leg["market"] == "total":
        value = (home + away) - leg["line"]
        value = value if leg["side"] == "over" else -value
    else:
        margin = home - away if leg["side"] == "home" else away - home
        value = margin + (leg["line"] or 0.0) if leg["market"] == "spread" else margin
    return "win" if value > 0 else "loss" if value < 0 else "push"


def game_scores(games, week):
    """{"game:AWAY@HOME": {home_score, away_score, gp}} for the week's played games (the props
    log stores them beside the player stats, under the id game legs carry)."""
    return {f"game:{g['away']}@{g['home']}": {"home_score": g["home_score"], "away_score": g["away_score"], "gp": 1}
            for g in games if g["week"] == week and g.get("home_score") is not None and g.get("away_score") is not None}
