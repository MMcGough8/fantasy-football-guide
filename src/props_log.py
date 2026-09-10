"""The props log: every line pulled, every bet the owner records, the raw stats to grade them,
and the outcomes. Append-only JSON lines at `.props_log.jsonl` (`PROPS_LOG_FILE` overrides it),
using `projection_log`'s reader and writer. After a few weeks `calibration_report` says how
honest our probabilities were and which blend weight the log would have preferred.
"""
import datetime
import math
import os
import uuid
from collections import defaultdict
from statistics import mean

from game_lines import GAME_MARKETS, grade_game_leg
from projection_log import append_records, is_logged, read_records

LOG_FILE = os.getenv("PROPS_LOG_FILE") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".props_log.jsonl")
LEAGUE_ID = "props"  # the log's marker slot; props are league-independent
STAT_FOR_MARKET = {"player_pass_yds": "pass_yd", "player_pass_tds": "pass_td", "player_rush_yds": "rush_yd", "player_receptions": "rec", "player_reception_yds": "rec_yd"}
TD_KEYS = ("rush_td", "rec_td", "kr_td", "pr_td")
LINE_FIELDS = ("player", "player_id", "position", "market", "side", "book", "line", "price", "p", "p_line", "p_model", "p_market", "p_book",
               "push", "ev", "ev_line", "consensus_line", "event_id", "home", "away")
PROBABILITY_BUCKETS = [[0.0, 0.45], [0.45, 0.5], [0.5, 0.55], [0.55, 0.6], [0.6, 0.65], [0.65, 0.7], [0.7, 1.01]]
WEIGHT_GRID = [round(w / 10, 1) for w in range(11)]


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _marker(what, season, week, count, now, pulled_at=None):
    return {"kind": "logged", "what": what, "season": season, "week": week, "league_id": LEAGUE_ID, "count": count, "logged_at": now, "pulled_at": pulled_at}


def line_records(season, week, priced, now):
    return [{"kind": "line", "season": season, "week": week, "league_id": LEAGUE_ID, "pulled_at": now, **{f: p.get(f) for f in LINE_FIELDS}} for p in priced]


def log_lines(path, season, week, priced, pulled_at=None):
    """Append the priced legs of one pull. Every pull is logged (line movement is a signal),
    but the same pull, identified by its `pulled_at`, is never logged twice."""
    pulled_at = pulled_at or _now()
    if any(r.get("kind") == "logged" and r.get("what") == "lines" and r.get("pulled_at") == pulled_at for r in read_records(path)):
        return 0
    records = line_records(season, week, priced, pulled_at)
    append_records(path, records + [_marker("lines", season, week, len(records), _now(), pulled_at)])
    return len(records)


def record_bet(path, bet, now=None):
    bet_id = uuid.uuid4().hex[:8]
    append_records(path, [{"kind": "bet", "id": bet_id, "placed_at": now or _now(), "league_id": LEAGUE_ID, **bet}])
    return bet_id


def log_stats(path, season, week, stats_by_player, now=None):
    """Raw weekly stats for every player who played, once per week, so legs can be graded."""
    if is_logged(read_records(path), "stats", season, week, LEAGUE_ID):
        return 0
    now = now or _now()
    records = [{"kind": "stats", "season": season, "week": week, "league_id": LEAGUE_ID, "player_id": str(pid), "stats": s}
               for pid, s in stats_by_player.items() if (s or {}).get("gp")]
    append_records(path, records + [_marker("stats", season, week, len(records), now)])
    return len(records)


def _stat_value(market, stats):
    if market == "player_anytime_td":
        return sum(stats.get(k) or 0 for k in TD_KEYS)
    return stats.get(STAT_FOR_MARKET[market]) or 0


def grade_leg(leg, stats):
    """win | loss | push | void for one leg against the player's raw weekly stats (a game leg
    against the final score stored under its game id)."""
    if leg.get("market") in GAME_MARKETS:
        return grade_game_leg(leg, stats)
    if not stats or not stats.get("gp"):
        return "void"
    value = _stat_value(leg["market"], stats)
    if leg["side"] == "yes":
        return "win" if value >= 1 else "loss"
    line = leg["line"]
    if value == line:
        return "push"
    won = value > line if leg["side"] == "over" else value < line
    return "win" if won else "loss"


def _decimal(price):
    return 1 + price / 100 if price > 0 else 1 + 100 / abs(price)


def grade_bet(bet, stats_by_player):
    """The outcome record for a bet: singles pay their price, parlays the quoted payout with
    void legs dropped and the payout re-multiplied from the leg prices."""
    results = [{**leg, "result": grade_leg(leg, stats_by_player.get(str(leg["player_id"])))} for leg in bet["legs"]]
    live = [r for r in results if r["result"] != "void"]
    stake = bet.get("stake") or 0.0
    if not live:
        result, profit = "void", 0.0
    elif any(r["result"] == "loss" for r in live):
        result, profit = "loss", -stake
    elif all(r["result"] == "push" for r in live):
        result, profit = "push", 0.0
    else:
        winners = [r for r in live if r["result"] == "win"]
        payout = _payout(bet, winners)
        result, profit = "win", stake * (payout - 1)
    return {"kind": "outcome", "bet_id": bet["id"], "season": bet.get("season"), "week": bet.get("week"), "league_id": LEAGUE_ID,
            "result": result, "profit": round(profit, 2), "stake": stake, "legs": results}


def _payout(bet, winners):
    """Decimal payout: the bet's own price for a single (an odds boost is the bet's price, not the
    leg's), the quoted parlay payout when every leg stood, else re-multiplied from the leg prices."""
    if len(bet["legs"]) == 1 and bet.get("price"):
        return _decimal(bet["price"])
    if len(winners) == len(bet["legs"]) and bet.get("quoted_payout"):
        return bet["quoted_payout"]
    return math.prod(_decimal(r["price"]) for r in winners)


def _stats_by_week(records):
    out = defaultdict(dict)
    for r in records:
        if r.get("kind") == "stats":
            out[(str(r["season"]), int(r["week"]))][str(r["player_id"])] = r["stats"]
    return out


def grade_bets(path):
    """Grade every recorded bet whose week has stats in the log and no outcome yet."""
    records = read_records(path)
    graded = {r["bet_id"] for r in records if r.get("kind") == "outcome"}
    stats = _stats_by_week(records)
    outcomes = []
    for bet in records:
        if bet.get("kind") != "bet" or bet["id"] in graded:
            continue
        week_stats = stats.get((str(bet.get("season")), int(bet.get("week") or 0)))
        if week_stats:
            outcomes.append(grade_bet(bet, week_stats))
    if outcomes:
        append_records(path, outcomes)
    return len(outcomes)


def bet_summary(records):
    bets = [r for r in records if r.get("kind") == "bet"]
    outcomes = [r for r in records if r.get("kind") == "outcome" and r["result"] != "void"]
    staked = sum(o["stake"] for o in outcomes)
    profit = sum(o["profit"] for o in outcomes)
    return {"bets": len(bets), "graded": len(outcomes), "staked": round(staked, 2), "profit": round(profit, 2), "roi": round(profit / staked, 4) if staked else None}


def line_outcomes(records):
    """[(line record, result)] for logged lines whose week has stats; pushes and voids are dropped."""
    stats = _stats_by_week(records)
    out = []
    for r in records:
        if r.get("kind") != "line":
            continue
        week_stats = stats.get((str(r["season"]), int(r["week"])))
        if not week_stats:
            continue
        result = grade_leg(r, week_stats.get(str(r["player_id"])))
        if result in ("win", "loss"):
            out.append((r, result))
    return out


def _logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _blend(p_model, p_market, weight):
    z = weight * _logit(p_market) + (1 - weight) * _logit(p_model)
    return 1 / (1 + math.exp(-z))


def _market_at_line(r):
    """The book's own de-vigged probability at the line we priced (falls back to the consensus)."""
    return r.get("p_book") if r.get("p_book") is not None else r.get("p_market")


def fit_market_weight(outcomes):
    """A diagnostic: the logit-blend weight between our probability and the book's, both at the
    offered line, that minimises log loss over the graded lines. It says how much to trust the
    book versus us; it is not the centre-space MARKET_WEIGHT itself. None with nothing to fit."""
    usable = [(r, res) for r, res in outcomes if r.get("p_model") is not None and _market_at_line(r) is not None]
    if not usable:
        return None
    best = None
    for w in WEIGHT_GRID:
        loss = -mean(math.log(max(_blend(r["p_model"], _market_at_line(r), w) if res == "win" else 1 - _blend(r["p_model"], _market_at_line(r), w), 1e-9)) for r, res in usable)
        if best is None or loss < best[0]:
            best = (loss, w)
    return best[1]


def calibration_report(records):
    """Predicted vs observed hit rate by probability bucket and by market, and the preferred weight."""
    outcomes = line_outcomes(records)
    ours = [(r, res) for r, res in outcomes if r.get("market") not in GAME_MARKETS]  # a game leg's p is the market's own, calibrated by construction
    buckets = []
    for lo, hi in PROBABILITY_BUCKETS:
        rs = [(r, res) for r, res in ours if r.get("p") is not None and lo <= r["p"] < hi]
        buckets.append({"p": [lo, hi], "n": len(rs), "predicted": round(mean(r["p"] for r, _ in rs), 4) if rs else None,
                        "observed": round(mean(res == "win" for _, res in rs), 4) if rs else None})
    by_market = defaultdict(list)
    for r, res in outcomes:
        by_market[r["market"]].append((r, res))
    markets = {m: {"n": len(rs), "predicted": round(mean(r["p"] for r, _ in rs), 4), "observed": round(mean(res == "win" for _, res in rs), 4)}
               for m, rs in by_market.items()}
    return {"graded": len(outcomes), "buckets": buckets, "by_market": markets, "weight": fit_market_weight(outcomes)}
