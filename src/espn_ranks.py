import json
import os

_espn_cache = None
_berry_cache = None


def _normalize(name):
    n = name.lower().replace(".", "").replace("'", "")
    for suffix in (" jr", " sr", " iii", " ii"):
        n = n.replace(suffix, "")
    return n.strip()


def match_key(name, position):
    """Cross-source player key. Defenses match on nickname because sites disagree
    on the form ("Houston Texans" vs "Texans D/ST")."""
    key = _normalize(name)
    if position in ("DEF", "DST"):
        tokens = [t for t in key.replace("/", " ").split() if t not in ("d", "st", "dst")]
        return tokens[-1] if tokens else key
    return key


def _load(filename):
    path = os.path.join(os.path.dirname(__file__), filename)
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def load_espn_ranks():
    global _espn_cache
    if _espn_cache is None:
        _espn_cache = {
            match_key(r["name"], r.get("position", "")): r["espn_rank"]
            for r in _load("espn_rankings.json")
        }
    return _espn_cache


def load_berry_ranks():
    global _berry_cache
    if _berry_cache is None:
        _berry_cache = {
            match_key(r["name"], r.get("position", "")): r["berry_rank"]
            for r in _load("berry_rankings.json")
        }
    return _berry_cache


def attach_ranks(board, live_espn_ranks=None, fp_ranks=None):
    """Attach ESPN, Berry and FantasyPros ranks, a market consensus, and disagreement flags.

    `live_espn_ranks` ({match_key: rank} from ESPN's API) wins over the committed
    espn_rankings.json snapshot when present. `fp_ranks` is {match_key: {rank, std,
    tier, ...}} from FantasyPros' expert consensus.
    """
    espn = load_espn_ranks()
    berry = load_berry_ranks()
    live = live_espn_ranks or {}
    fp = fp_ranks or {}

    # Sleeper rank = each player's VOR order on the board
    by_vor = sorted(board, key=lambda p: p.get("vor", 0), reverse=True)
    for i, p in enumerate(by_vor, start=1):
        p["sleeper_rank"] = i

    for p in board:
        key = match_key(p["name"], p["position"])
        p["espn_rank"] = live.get(key, espn.get(key))
        p["berry_rank"] = berry.get(key)
        fp_entry = fp.get(key) or {}
        p["fp_rank"] = fp_entry.get("rank")
        p["fp_rank_std"] = fp_entry.get("std")
        p["fp_tier"] = fp_entry.get("tier")

        # Consensus = the market's view (experts and sites), not the board's own order
        market = [r for r in (p["fp_rank"], p["espn_rank"], p["berry_rank"]) if r is not None]
        p["consensus"] = round(sum(market) / len(market), 1) if market else p["sleeper_rank"]

        # Disagreement = how far the board's order sits from any market rank
        ranks = [p["sleeper_rank"]] + market
        p["rank_spread"] = max(ranks) - min(ranks)
        p["disagreement"] = p["rank_spread"] >= 15
    return board


# Backwards-compatible alias, in case draft_board still calls the old name
attach_espn_ranks = attach_ranks
