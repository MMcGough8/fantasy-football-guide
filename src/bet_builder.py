"""Turning priced legs into decisions: a plain-English reason for every leg, this week's best
singles under the safe rules, the best two-leg parlays among them, and the labels the page shows.
Pure; the app renders what comes back.
"""
from itertools import combinations

from odds_math import american_to_decimal
from props import SAFE, TD_MARKET, is_safe, parlay_ev, parlay_probability

MARKET_LABELS = {"player_pass_yds": "Pass yds", "player_pass_tds": "Pass TDs", "player_rush_yds": "Rush yds", "player_receptions": "Receptions",
                 "player_reception_yds": "Rec yds", "player_anytime_td": "Anytime TD"}
GAME_MARKET_LABELS = {"spread": "Spread", "total": "Total", "moneyline": "Moneyline"}
BOOK_LABELS = {"draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM", "betrivers": "BetRivers", "betonlineag": "BetOnline",
               "bovada": "Bovada", "betus": "BetUS", "lowvig": "LowVig", "mybookieag": "MyBookie"}
SIDE_LABELS = {"over": "O", "under": "U", "yes": "TD", "home": "", "away": ""}
UNITS = {"player_pass_yds": "yards", "player_rush_yds": "yards", "player_reception_yds": "yards", "player_receptions": "receptions", "player_pass_tds": "touchdowns"}
BEST_SINGLES = 5
BEST_PARLAYS = 3
PARLAY_POOL = 12  # the safe legs a parlay may draw from, best first


def market_label(market):
    return MARKET_LABELS.get(market) or GAME_MARKET_LABELS.get(market) or market


def book_label(book):
    return BOOK_LABELS.get(book, book)


def _points_phrase(edge):
    if not edge:
        return "at the consensus number"
    size = abs(edge)
    amount = "half a point" if size == 0.5 else "a point" if size == 1 else f"{size:g} points"
    return f"{amount} {'better' if edge > 0 else 'worse'}"


def _versus(entry, signed):
    cons = entry.get("consensus_line")
    if cons is None:
        return ""
    shown = f"{cons:+g}" if signed else f"{cons:g}"
    return f"against a consensus {shown}, {_points_phrase(entry.get('edge') or 0.0)}"


def leg_reason(entry):
    """One sentence on where this leg's edge comes from and how often it lands."""
    book, price, fair = book_label(entry["book"]), entry["price"], entry["fair"]
    p = entry.get("p_win", entry["p"])
    market, side, line = entry["market"], entry["side"], entry.get("line")
    if market == TD_MARKET:
        return f"{book} pays {price:+d}; a touchdown lands {p:.0%} of the time, fair {fair:+d}. A long shot, not a bankroll bet."
    if market == "moneyline":
        return f"{book} pays {price:+d} on {entry['player']} to win; the market makes that {p:.0%}, fair {fair:+d}."
    if market == "spread":
        return f"{book} gives {entry['player']} {line:+g} {_versus(entry, signed=True)}; covers {p:.0%} of the time. Pays {price:+d}, fair {fair:+d}."
    if market == "total":
        return f"{book} posts {line:g} {_versus(entry, signed=False)} on the {side}, which lands {p:.0%} of the time. Pays {price:+d}, fair {fair:+d}."
    edge = entry.get("edge") or 0.0
    if edge > 0 and entry.get("consensus_line") is not None:
        return (f"{book} posts {line:g} against a consensus {entry['consensus_line']:g}: {edge:g} {UNITS[market]} of room on the {side},"
                f" which lands {p:.0%} of the time. Pays {price:+d}, fair {fair:+d}.")
    return f"{book} pays {price:+d} on the {side} {line:g} where fair is {fair:+d}; it lands {p:.0%} of the time."


def _safe(entry, rules):
    return is_safe(entry["p"], entry["price"], entry["ev"], entry["market"], entry["ev_line"], rules)


def best_singles(priced, rules=SAFE, limit=BEST_SINGLES):
    """The legs that pass the safe rules, best line-shopping edge first."""
    safe = [e for e in priced if _safe(e, rules)[0]]
    return sorted(safe, key=lambda e: -e["ev_line"])[:limit]


NEVER_SAFE = ("anytime TD is never safe", f"price shorter than {SAFE['min_price']}")  # no line move fixes these


def nearly_safe(priced, rules=SAFE, limit=BEST_SINGLES):
    """[(leg, why it fails)] for the legs closest to passing: those a better line or a touch more
    probability would admit, best edge first. Touchdowns and heavy favourites never qualify."""
    failing = [(e, _safe(e, rules)[1]) for e in priced if not _safe(e, rules)[0]]
    candidates = [pair for pair in failing if pair[1] not in NEVER_SAFE]
    return sorted(candidates, key=lambda pair: -pair[0]["ev_line"])[:limit]


def best_parlays(cal, priced, rules=SAFE, limit=BEST_PARLAYS, pool=PARLAY_POOL):
    """Two-leg parlays from the safe legs, ranked by expected value at the product of the leg
    prices (what a book pays for independent legs; a same-game quote will differ). Pairs the
    parlay rules reject (one player twice, both sides of a market) are skipped."""
    candidates = best_singles(priced, rules, pool)
    parlays = []
    for a, b in combinations(candidates, 2):
        try:
            joint = parlay_probability(cal, [a, b])
        except ValueError:
            continue
        payout = american_to_decimal(a["price"]) * american_to_decimal(b["price"])
        parlays.append({"legs": [a, b], "joint": joint["correlated"], "independent": joint["independent"], "rho": joint["rho"],
                        "payout": round(payout, 2), "ev": round(parlay_ev(joint["correlated"], payout), 4)})
    return sorted(parlays, key=lambda p: -p["ev"])[:limit]
