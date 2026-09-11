"""The knockout (survivor) plan: a win chance for every team-week from the best line available,
and the pick that maximises the chance of surviving every week with posted lines. Distinct teams
for distinct weeks is a matching problem, solved exactly (a small Hungarian solver: greedy fails
on the very case the feature exists for, a team that is the best pick now and the only strong
pick later). Pure; the app supplies the schedule, the live legs and the clock.
"""
import math

from calibration import normal_cdf
from game_lines import game_consensus
from odds_math import devig

DEFAULT_MARGIN_SD = 12.6  # calibration.json "games.margin_sd" is 12.64; this is the no-file fallback
P_FLOOR = 1e-4  # keeps log(p) finite
INFEASIBLE_COST = 1e3  # above 8 weeks x |log(P_FLOOR)| = 74, so a team without a game never beats one with a game
MIN_PRICED_SHARE = 0.75  # a week joins the plan once three quarters of its games have a line; the rest are absent that week
TIE_RATE = 0.0012  # one tie in 816 games, 2023-25 nflverse; the normal margin puts 3% on a tie, so the spread route is rescaled to this
SITE_WORDS = {"home": "vs", "away": "at", "neutral": "vs"}


# ---- win chances ----

def spread_win_probability(spread_line, sd):
    """P(the home team wins outright) from nflverse's spread (positive = home favoured): a normal
    final margin centred on the spread, integer margins, and a tie (margin 0) is not a win."""
    return 1.0 - normal_cdf((0.5 - spread_line) / sd)


def moneyline_win_probability(home_price, away_price):
    return devig(home_price, away_price)[0]


def live_win_probabilities(game_legs):
    """{(home, away): P(home wins)} from the props pull's moneyline legs: each side's de-vigged
    consensus, renormalised (two medians across books do not sum to exactly one)."""
    out = {}
    for leg in game_legs:
        if leg.get("market") != "moneyline":
            continue
        home, away = game_consensus(leg, "home")["p"], game_consensus(leg, "away")["p"]
        if home is not None and away is not None and home + away > 0:
            out[(leg["home"], leg["away"])] = home / (home + away)
    return out


def spread_pair(spread_line, sd):
    """(P(home wins), P(away wins)) from the spread, the two sides rescaled to sum to 1 - TIE_RATE so a
    spread-priced game ranks on the same scale as a moneyline-priced one."""
    home, away = spread_win_probability(spread_line, sd), spread_win_probability(-spread_line, sd)
    scale = (1.0 - TIE_RATE) / (home + away)
    return home * scale, away * scale


def game_win_probability(game, live, sd):
    """(P(home wins), source): the live consensus, else nflverse's moneyline (unless the game carries a
    live spread, which is fresher than the weekly moneyline snapshot), else the spread; (None, None)
    when nothing prices the game."""
    pair = (game["home"], game["away"])
    if pair in live:
        return live[pair], "live"
    has_moneyline = game.get("home_moneyline") is not None and game.get("away_moneyline") is not None
    if has_moneyline and game.get("line_source") != "live":
        return moneyline_win_probability(game["home_moneyline"], game["away_moneyline"]), "moneyline"
    if game.get("spread_line") is not None:
        return spread_pair(game["spread_line"], sd)[0], "spread"
    return None, None


def _away_probability(game, p_home, source, sd):
    return spread_pair(game["spread_line"], sd)[1] if source == "spread" else 1.0 - p_home


def week_probabilities(games, week, sd, live=None):
    """{"p": {team: P(win)}, "opponents": {team: {"opponent", "site"}}, "sources": {(home, away):
    source}, "unpriced": games without a line, "games": games not yet played} for one week."""
    live = live or {}
    p, opponents, sources, unpriced, count = {}, {}, {}, 0, 0
    for game in games:
        if game["week"] != week or game.get("home_score") is not None:
            continue
        count += 1
        p_home, source = game_win_probability(game, live, sd)
        if p_home is None:
            unpriced += 1
            continue
        home, away = game["home"], game["away"]
        p[home], p[away] = p_home, _away_probability(game, p_home, source, sd)
        opponents[home] = {"opponent": away, "site": "neutral" if game.get("neutral") else "home"}
        opponents[away] = {"opponent": home, "site": "neutral" if game.get("neutral") else "away"}
        sources[(home, away)] = source
    return {"p": p, "opponents": opponents, "sources": sources, "unpriced": unpriced, "games": count}


def priced_weeks(games, first_week, sd, live_by_week=None):
    """{"weeks": {week: week_probabilities(...)}, "dropped": week | None}: the selected week and
    every following week with lines on at least MIN_PRICED_SHARE of its games (the books hold a
    game back now and then; its teams are simply absent that week). The first week short of
    that ends the horizon and is reported."""
    live_by_week = live_by_week or {}
    weeks, dropped = {}, None
    for week in sorted({g["week"] for g in games if g["week"] >= first_week}):
        info = week_probabilities(games, week, sd, live_by_week.get(week))
        priced = info["games"] - info["unpriced"]
        if week != first_week and (not info["games"] or priced < MIN_PRICED_SHARE * info["games"]):
            dropped = week
            break
        weeks[week] = info
    return {"weeks": weeks, "dropped": dropped}


# ---- the plan ----

def hungarian(cost):
    """The column assigned to each row in the minimum-cost assignment of n rows to m >= n
    columns (the potentials form, O(n^2 m)); pure lists, the matrix is at most 8 x 32."""
    n, m = len(cost), len(cost[0])
    if n > m:
        raise ValueError("hungarian needs at least as many columns as rows")
    inf = float("inf")
    u, v, p, way = [0.0] * (n + 1), [0.0] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv, used = [inf] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], inf, 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j], way[j] = cur, j0
                if minv[j] < delta:
                    delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    assignment = [0] * n
    for j in range(1, m + 1):
        if p[j]:
            assignment[p[j] - 1] = j - 1
    return assignment


def best_future(weeks_p, weeks, excluded):
    """The assignment of distinct teams to `weeks` maximising the product of win chances, with
    `excluded` teams removed: {"picks": {week: team}, "survival": product, "unfilled": weeks no
    team could take}."""
    weeks = list(weeks)
    if not weeks:
        return {"picks": {}, "survival": 1.0, "unfilled": []}
    teams = sorted({t for w in weeks for t in weeks_p.get(w, {})} - set(excluded))
    columns = teams + [None] * max(0, len(weeks) - len(teams))  # pad so every week gets a column
    cost = [[INFEASIBLE_COST if t is None or t not in weeks_p[w] else -math.log(max(weeks_p[w][t], P_FLOOR)) for t in columns] for w in weeks]
    picks, survival, unfilled = {}, 1.0, []
    for week, column in zip(weeks, hungarian(cost)):
        team = columns[column]
        if team is None or team not in weeks_p[week]:
            unfilled.append(week)
            survival *= P_FLOOR  # a week with nobody left to pick is as good as elimination; never free
            continue
        picks[week] = team
        survival *= weeks_p[week][team]
    return {"picks": picks, "survival": survival, "unfilled": unfilled}


def _matchup(info, team):
    where = info["opponents"].get(team) or {}
    return f"{SITE_WORDS.get(where.get('site'), 'vs')} {where.get('opponent', '?')}"


def reason(row, info, future_weeks):
    """One sentence on why the row ranks where it does."""
    team, p = row["team"], row["p_win"]
    if row["rank"] == 0:
        if not future_weeks:
            return f"{team} wins {p:.0%} of the time {_matchup(info, team)}; no lines posted beyond this week, so nothing is being saved."
        first = min(row["plan"]) if row["plan"] else None
        kept = f"; the plan keeps {row['plan'][first]} for week {first} ({row['plan_p'][first]:.0%})" if first else ""
        return f"{team} wins {p:.0%} of the time {_matchup(info, team)}{kept}."
    if row["saved_for"]:
        return (f"{team} wins {row['saved_p']:.0%} of the time in week {row['saved_for']}; save it"
                f" (using it now costs {row['future_cost'] * 100:.0f} points of plan survival).")
    return f"{team} wins {p:.0%} of the time {_matchup(info, team)} and is never the plan's pick later."


def rank_candidates(weeks, week, used, locked):
    """This week's pickable teams ranked by the chance of surviving every priced week if picked
    now: P(win now) x the best plan for the later weeks without this team. `weeks` is
    `priced_weeks(...)["weeks"]`; `used` teams are out everywhere, `locked` ones this week only."""
    info = weeks[week]
    future = sorted(w for w in weeks if w > week)
    weeks_p = {w: weeks[w]["p"] for w in weeks}
    whole = best_future(weeks_p, future, used)
    rows = []
    for team, p in info["p"].items():
        if team in used or team in locked:
            continue
        saved_for = next((w for w, t in whole["picks"].items() if t == team), None)
        without = best_future(weeks_p, future, set(used) | {team}) if saved_for else whole
        rows.append({"team": team, "p_win": p, "opponent": info["opponents"][team]["opponent"], "site": info["opponents"][team]["site"],
                     "horizon": p * without["survival"], "future_cost": round(whole["survival"] - without["survival"], 4),
                     "saved_for": saved_for, "saved_p": weeks_p[saved_for][team] if saved_for else None,
                     "plan": without["picks"], "plan_p": {w: weeks_p[w][t] for w, t in without["picks"].items()}})
    rows.sort(key=lambda r: (-r["horizon"], -r["p_win"], r["team"]))
    return [{**row, "rank": i, "reason": reason({**row, "rank": i}, info, future)} for i, row in enumerate(rows)]
