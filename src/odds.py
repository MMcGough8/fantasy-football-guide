"""Game-day lines from The Odds API (free tier) for the current NFL week.

nflverse's schedule carries the market's lines as of its weekly refresh; by game day
the market has moved (weather, late injury news). One call here returns every game
the books currently list (roughly the current week) with per-bookmaker spreads and
totals, which `merge_lines` folds into the nflverse games list before
`matchups.team_context` computes implied totals. Each call costs markets x regions
= 2 credits of the free tier's 500 a month, so the app caches it for hours and never
retries it on the projection feeds' cadence.
"""
import statistics

import requests

ODDS_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
TIMEOUT_SECONDS = 15
REGIONS = "us"
MARKETS = "spreads,totals"

# The API names teams "City Nickname"; Sleeper uses codes.
TEAM_NAMES = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR", "Chicago Bears": "CHI", "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL", "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX", "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC", "Los Angeles Rams": "LAR", "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN", "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT", "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB", "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}


class OddsError(Exception):
    """Raised for any failure fetching or reading The Odds API, with a user-facing message."""


def _header_int(resp, name):
    try:
        return int(resp.headers.get(name))
    except (TypeError, ValueError):
        return None


def _refusal(resp):
    """The API's own error code when it sent one, else the HTTP status."""
    try:
        body = resp.json()
    except ValueError:
        body = None
    code = body.get("error_code") if isinstance(body, dict) else None
    return f"The Odds API refused the request ({code or f'HTTP {resp.status_code}'})"


def fetch_odds(api_key):
    """{"events", "remaining", "used"}: the listed games and the credit counters."""
    params = {"apiKey": api_key, "regions": REGIONS, "markets": MARKETS, "oddsFormat": "american", "dateFormat": "iso"}
    try:
        resp = requests.get(ODDS_URL, params=params, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as e:
        # the exception text can carry the request URL, and with it the key: never echo it
        raise OddsError(f"Couldn't reach The Odds API ({type(e).__name__})") from e
    if resp.status_code >= 400:
        raise OddsError(_refusal(resp))
    try:
        events = resp.json()
    except ValueError as e:
        raise OddsError("The Odds API returned an unexpected response") from e
    return {"events": events, "remaining": _header_int(resp, "x-requests-remaining"), "used": _header_int(resp, "x-requests-used")}


def _market_point(market, home_team):
    """The home team's spread, or the Over total, from one bookmaker market; None otherwise."""
    key = market.get("key")
    wanted = home_team if key == "spreads" else "Over" if key == "totals" else None
    for outcome in market.get("outcomes") or []:
        if outcome.get("name") == wanted and outcome.get("point") is not None:
            return float(outcome["point"])
    return None


def _consensus_line(event):
    """Median across bookmakers, in nflverse's convention (positive spread = home favoured)."""
    spreads, totals, updates = [], [], []
    for book in event.get("bookmakers") or []:
        for market in book.get("markets") or []:
            point = _market_point(market, event.get("home_team"))
            if point is None:
                continue
            (spreads if market["key"] == "spreads" else totals).append(point)
            if market.get("last_update"):
                updates.append(market["last_update"])
    if not spreads and not totals:
        return None
    return {
        "spread_line": round(-statistics.median(spreads) + 0.0, 2) if spreads else None,  # + 0.0 turns -0.0 into 0.0
        "total": round(statistics.median(totals), 2) if totals else None,
        "updated": max(updates) if updates else None,
    }


def parse_events(events):
    """{"lines": {(home, away): {"spread_line", "total", "updated"}}, "missing": [unknown team names]}."""
    lines, missing = {}, []
    for event in events or []:
        names = (event.get("home_team"), event.get("away_team"))
        unknown = [n for n in names if n not in TEAM_NAMES]
        if unknown:
            missing.extend(n for n in unknown if n not in missing)
            continue
        line = _consensus_line(event)
        if line:
            lines[(TEAM_NAMES[names[0]], TEAM_NAMES[names[1]])] = line
    return {"lines": lines, "missing": missing}


def merge_lines(games, lines, week):
    """A new games list: this week's games with a live line get it (and `line_source`
    "live"); everything else passes through marked "nflverse"."""
    merged = []
    for game in games:
        live = lines.get((game["home"], game["away"])) if game["week"] == week else None
        if not live:
            merged.append({**game, "line_source": "nflverse"})
            continue
        merged.append({
            **game,
            "total": live["total"] if live["total"] is not None else game["total"],
            "spread_line": live["spread_line"] if live["spread_line"] is not None else game["spread_line"],
            "line_source": "live",
        })
    return merged
