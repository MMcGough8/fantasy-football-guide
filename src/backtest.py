"""Backtest the weekly projection engine on past seasons and write `calibration.json`.

    .venv/bin/python src/backtest.py --seasons 2023 2024 2025 [--cache .backtest_cache] [--out src/calibration.json]

Sleeper, FantasyPros and ESPN all still serve past weekly projections, and Sleeper the
box scores, so a season can be rebuilt the way the app builds a week (three feeds, per-stat
median blend, PPR scoring) and scored against what happened. The fits below are pure
functions over the rows `backtest_data.load_season` returns; the CLI at the bottom pulls,
fits, writes the file and prints the audit tables. Everything the app reads back goes
through `calibration.py`.
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from statistics import mean, median

import numpy as np
from dotenv import load_dotenv

import backtest_data
from projection_log import GAP_BUCKETS, NEIGHBOURS  # one definition of the decision buckets

SKILL = ("QB", "RB", "WR", "TE")
QUANTILE_POINTS = [round(i / 20, 2) for i in range(21)]
PROP_FLOORS = {"pass_yd": 150.0, "rush_yd": 25.0, "rec_yd": 25.0}
COUNT_FLOORS = {"rec": 2.5, "pass_td": 0.8}
RATIO_STATS = {"QB": ("pass_yd", "rush_yd"), "RB": ("rush_yd", "rec_yd"), "WR": ("rec_yd",), "TE": ("rec_yd",)}
COUNT_STATS = {"QB": ("pass_td",), "RB": ("rec",), "WR": ("rec",), "TE": ("rec",)}
WIND_BANDS = [[0, 5], [5, 10], [10, 15], [15, 999]]
COLD_BANDS = [[-99, 32], [32, 50], [50, 70], [70, 999]]
COLD_REFERENCE_BAND = 2  # 50-70F is "normal"; cold factors are relative to it
MIN_BAND_N = 40
MIN_POINTS = 2.0
STARTER_POINTS = 5.0


def quantile_table(ratios, k50):
    """The 21-point quantile table of actual / median (ratios divided by the band's median ratio)."""
    xs = np.array(sorted(r / k50 for r in ratios))
    return {"p": QUANTILE_POINTS, "x": [round(float(np.quantile(xs, p)), 4) for p in QUANTILE_POINTS]}


def _band(rows, stat, lo, hi):
    ratios = [r["act"][stat] / r["proj"][stat] for r in rows]
    k50 = float(median(ratios))
    return {"lo": round(lo, 2), "hi": round(hi, 2), "k50": round(k50, 4), "n": len(rows), **quantile_table(ratios, k50)}


def fit_ratio_tables(rows, floors):
    """Per (position, stat) three projection-tercile bands, each with its median ratio and table."""
    tables = defaultdict(dict)
    for pos, stats in RATIO_STATS.items():
        for stat in stats:
            floor = floors.get(stat)
            if floor is None:
                continue
            rs = sorted((r for r in rows if r["pos"] == pos and r["proj"][stat] >= floor), key=lambda r: r["proj"][stat])
            if len(rs) < 3 * MIN_BAND_N:
                continue
            cut = len(rs) // 3
            parts = [rs[:cut], rs[cut:2 * cut], rs[2 * cut:]]
            bands = []
            for i, part in enumerate(parts):
                lo = floor if i == 0 else part[0]["proj"][stat]
                hi = 1e9 if i == 2 else parts[i + 1][0]["proj"][stat]
                bands.append(_band(part, stat, lo, hi))
            tables[pos][stat] = {"floor": floor, "bands": bands}
    return dict(tables)


def _spread_fit(rs):
    """sd of the miss as a line in the projection, from projection buckets of at least MIN_BAND_N rows."""
    rs = sorted(rs, key=lambda r: r["ppr_proj"])
    size = max(MIN_BAND_N, len(rs) // 8)
    points = []
    for i in range(0, len(rs) - size + 1, size):
        chunk = rs[i:i + size]
        points.append((mean(r["ppr_proj"] for r in chunk), float(np.std([r["ppr_actual"] - r["ppr_proj"] for r in chunk]))))
    if len({round(p, 3) for p, _ in points}) < 2:  # one bucket, or all at one projection: no slope to fit
        sd = float(np.std([r["ppr_actual"] - r["ppr_proj"] for r in rs])) if rs else 3.5
        return 0.0, sd
    c1, c0 = np.polyfit([p for p, _ in points], [s for _, s in points], 1)
    return float(c1), float(c0)


def fit_points(rows):
    """Per position (and pooled as "default"): actual = a + b * projection, and the spread c0 + c1 * projection."""
    fits = {}
    scored = [r for r in rows if r["ppr_actual"] is not None and r["ppr_proj"] >= MIN_POINTS]
    for pos in list(SKILL) + ["K", "DEF", "default"]:
        rs = scored if pos == "default" else [r for r in scored if r["pos"] == pos]
        if len(rs) < 2 * MIN_BAND_N:
            continue
        b, a = np.polyfit([r["ppr_proj"] for r in rs], [r["ppr_actual"] for r in rs], 1)
        c1, c0 = _spread_fit(rs)
        fits[pos] = {"a": round(float(a), 3), "b": round(float(b), 4), "c0": round(c0, 3), "c1": round(c1, 4), "n": len(rs)}
    return fits


def fit_counts(rows, floors):
    """Per (position, count stat): the mean ratio and the Pearson dispersion around the calibrated mean."""
    fits = defaultdict(dict)
    for pos, stats in COUNT_STATS.items():
        for stat in stats:
            floor = floors.get(stat)
            rs = [r for r in rows if r["pos"] == pos and floor is not None and r["proj"][stat] >= floor]
            if len(rs) < 2 * MIN_BAND_N:
                continue
            mean_ratio = mean(r["act"][stat] for r in rs) / mean(r["proj"][stat] for r in rs)
            expected = [r["proj"][stat] * mean_ratio for r in rs]
            dispersion = mean((r["act"][stat] - e) ** 2 for r, e in zip(rs, expected)) / mean(expected)
            fits[pos][stat] = {"floor": floor, "mean_ratio": round(mean_ratio, 4), "dispersion": round(dispersion, 4), "n": len(rs)}
    return dict(fits)


def fit_td_factor(rows):
    """k in P(at least one TD) = 1 - exp(-k * projected TDs), averaged over narrow projection bands."""
    rs = [(r["proj"]["rush_td"] + r["proj"]["rec_td"], r["act"]["rush_td"] + r["act"]["rec_td"]) for r in rows if r["pos"] in ("RB", "WR", "TE")]
    rs = [(p, a) for p, a in rs if p >= 0.1]
    ks, weights = [], []
    for lo in np.arange(0.1, 1.5, 0.1):
        band = [(p, a) for p, a in rs if lo <= p < lo + 0.1]
        if len(band) < MIN_BAND_N:
            continue
        scored = mean(a >= 1 for _, a in band)
        if 0 < scored < 1:
            ks.append(-np.log(1 - scored) / mean(p for p, _ in band))
            weights.append(len(band))
    k = float(np.average(ks, weights=weights)) if ks else 1.0
    return {"k": round(k, 4), "n": len(rs)}


def _residual(r, stat):
    return r["act"][stat] - r["proj"][stat]


def _leaders(team_rows):
    def top(pos, stat):
        rs = [r for r in team_rows if r["pos"] == pos]
        return max(rs, key=lambda r: r["proj"][stat]) if rs else None

    return top("QB", "pass_yd"), top("WR", "rec_yd"), top("TE", "rec_yd"), top("RB", "rush_yd")


STAT_MARKET = {"pass_yd": "player_pass_yds", "pass_td": "player_pass_tds", "rush_yd": "player_rush_yds", "rec": "player_receptions", "rec_yd": "player_reception_yds"}
GAME_MARGIN_MARKETS = ("spread", "moneyline")  # both settle on the team's margin


def _corr(samples):
    return round(float(np.corrcoef(*zip(*samples))[0, 1]), 4)


def fit_games(games):
    """How far final scores land from the closing market: the spread of the home margin around
    the spread line and of the points around the total (the shape game legs are priced with), the
    mean residuals (reported, never applied: the market stays the centre) and the correlation
    between the favourite covering and the game going over (for same-game parlays)."""
    margins, totals, favourite = [], [], []
    for g in games:
        scores = (g.get("home_score"), g.get("away_score"), g.get("spread_line"), g.get("total"))
        if any(v is None for v in scores):
            continue
        home_score, away_score, spread_line, total_line = scores
        margins.append(home_score - away_score - spread_line)
        totals.append(home_score + away_score - total_line)
        favourite.append(margins[-1] * (1 if spread_line > 0 else -1 if spread_line < 0 else 0))
    if len(margins) < MIN_BAND_N:
        return {"n": len(margins), "margin_sd": None, "total_sd": None, "margin_bias": None, "total_bias": None, "favourite_over_rho": 0.0}
    return {"n": len(margins), "margin_sd": round(float(np.std(margins)), 2), "total_sd": round(float(np.std(totals)), 2),
            "margin_bias": round(float(np.mean(margins)), 2), "total_bias": round(float(np.mean(totals)), 2),
            "favourite_over_rho": _corr(list(zip(favourite, totals)))}


def fit_team_game_correlations(rows):
    """Residual correlations between a player's stat and his own team's margin (spread and
    moneyline legs) or the game's points (total legs), keyed the way `calibration.correlation`
    looks them up. The opponent's margin is the negative of the team's, so those keys carry
    the flipped sign."""
    floors = {**PROP_FLOORS, **COUNT_FLOORS}
    samples = defaultdict(list)
    for r in rows:
        if r.get("margin_resid") is None or r.get("total_resid") is None:
            continue
        for stat, market in STAT_MARKET.items():
            if r["proj"].get(stat, 0) >= floors[stat]:
                samples[(market, "margin")].append((_residual(r, stat), r["margin_resid"]))
                samples[(market, "total")].append((_residual(r, stat), r["total_resid"]))
    pairs = {}
    for (market, against), v in samples.items():
        if len(v) < MIN_BAND_N:
            continue
        rho = _corr(v)
        if against == "total":
            pairs[f"{market}|total|same_game"] = rho
            continue
        for game_market in GAME_MARGIN_MARKETS:
            pairs[f"{market}|{game_market}|same_team"] = rho
            pairs[f"{market}|{game_market}|opponent"] = -rho
    return pairs


def fit_correlations(rows):
    """Residual correlations for the leg pairs a parlay can hold, keyed by market pair and relation."""
    by_game = defaultdict(list)
    for r in rows:
        by_game[(r["season"], r["week"], r["game"])].append(r)
    samples = defaultdict(list)
    for grp in by_game.values():
        teams = defaultdict(list)
        for r in grp:
            teams[r["team"]].append(r)
        qbs = []
        for team_rows in teams.values():
            qb, wr, te, rb = _leaders(team_rows)
            if qb and wr:
                samples["player_pass_yds|player_reception_yds|same_team_qb_wr1"].append((_residual(qb, "pass_yd"), _residual(wr, "rec_yd")))
            if qb and te:
                samples["player_pass_yds|player_reception_yds|same_team_qb_te1"].append((_residual(qb, "pass_yd"), _residual(te, "rec_yd")))
            if qb and rb:
                samples["player_pass_yds|player_rush_yds|same_team"].append((_residual(qb, "pass_yd"), _residual(rb, "rush_yd")))
            if qb:
                qbs.append(qb)
            starters = [r for r in team_rows if r["ppr_actual"] is not None and r["ppr_proj"] >= 8]
            for i in range(len(starters)):
                for j in range(i + 1, len(starters)):
                    samples["same_team"].append((starters[i]["ppr_actual"] - starters[i]["ppr_proj"], starters[j]["ppr_actual"] - starters[j]["ppr_proj"]))
        if len(qbs) == 2:
            samples["player_pass_yds|player_pass_yds|opponent"].append((_residual(qbs[0], "pass_yd"), _residual(qbs[1], "pass_yd")))
        sides = [[r for r in rs if r["ppr_actual"] is not None and r["ppr_proj"] >= 8] for rs in teams.values()]
        if len(sides) == 2:
            for a in sides[0]:
                for b in sides[1]:
                    samples["opponent"].append((a["ppr_actual"] - a["ppr_proj"], b["ppr_actual"] - b["ppr_proj"]))
    corr = {k: round(float(np.corrcoef(*zip(*v))[0, 1]), 4) for k, v in samples.items() if len(v) >= 30}
    pairs = {k: v for k, v in corr.items() if "|" in k}
    return {"pairs": pairs, "default_same_team": corr.get("same_team", 0.0), "default_opponent": corr.get("opponent", 0.0), "default_cross_game": 0.0}


def _band_factors(rs, key, bands):
    means = []
    for lo, hi in bands:
        band = [r["ppr_actual"] / r["ppr_proj"] for r in rs if r[key] is not None and lo <= r[key] < hi]
        means.append(mean(band) if band else None)
    return means


def fit_weather(rows, min_n=MIN_BAND_N):
    """Per position, the outcome ratio in each wind and cold band relative to calm / mild weather."""
    outdoor = [r for r in rows if r["roof"] not in ("dome", "closed") and r["ppr_actual"] is not None and r["ppr_proj"] >= STARTER_POINTS]
    out = {"wind": {"bands": WIND_BANDS}, "cold": {"bands": COLD_BANDS}}
    for kind, key, bands, ref in (("wind", "wind", WIND_BANDS, 0), ("cold", "temp", COLD_BANDS, COLD_REFERENCE_BAND)):
        for pos in list(SKILL) + ["K", "DEF"]:
            rs = [r for r in outdoor if r["pos"] == pos]
            counts = [sum(1 for r in rs if r[key] is not None and lo <= r[key] < hi) for lo, hi in bands]
            if counts[ref] < min_n:
                continue
            means = _band_factors(rs, key, bands)
            base = means[ref]
            out[kind][pos] = [round(m / base, 3) if (m is not None and n >= min_n) else 1.0 for m, n in zip(means, counts)]
            out[kind][pos][ref] = 1.0
    return out


def decision_curve(rows):
    """Share of same-week, same-position pairs where the higher projection scored more, by gap."""
    by = defaultdict(list)
    for r in rows:
        if r["ppr_actual"] is not None and r["ppr_proj"] >= STARTER_POINTS:
            by[(r["season"], r["week"], r["pos"])].append(r)
    tally = {tuple(b): [0, 0] for b in GAP_BUCKETS}
    for grp in by.values():
        srt = sorted(grp, key=lambda r: -r["ppr_proj"])
        for i in range(len(srt)):
            for j in range(i + 1, min(i + NEIGHBOURS, len(srt))):
                gap = srt[i]["ppr_proj"] - srt[j]["ppr_proj"]
                for b in tally:
                    if b[0] <= gap < b[1]:
                        tally[b][0] += 1
                        tally[b][1] += srt[i]["ppr_actual"] > srt[j]["ppr_actual"]
                        break
    return [{"gap": list(b), "observed": round(w / n, 4) if n else None, "n": n} for b, (n, w) in tally.items()]


def build_calibration(rows, seasons, today, games=()):
    game_fit = fit_games(games)
    correlations = fit_correlations(rows)
    correlations["pairs"] = {
        **correlations["pairs"], **fit_team_game_correlations(rows),
        **{f"{m}|total|same_game": game_fit["favourite_over_rho"] for m in GAME_MARGIN_MARKETS},
    }
    return {
        "fitted_on": today,
        "seasons": list(seasons),
        "note": "written by src/backtest.py; read through src/calibration.py",
        "points": fit_points(rows),
        "ratio_tables": fit_ratio_tables(rows, PROP_FLOORS),
        "counts": fit_counts(rows, COUNT_FLOORS),
        "touchdowns": fit_td_factor(rows),
        "correlations": correlations,
        "games": game_fit,
        "weather": fit_weather(rows),
        "decision_curve": decision_curve(rows),
    }


def report(rows_by_season):
    """The audit tables, per season, so a season that looks different from the others is visible."""
    for season, rows in rows_by_season.items():
        scored = [r for r in rows if r["ppr_actual"] is not None and r["pos"] in SKILL]
        qb = [r for r in scored if r["pos"] == "QB" and r["ppr_proj"] >= 10]
        curve = {tuple(b["gap"]): b["observed"] for b in decision_curve(rows)}
        print(f"{season}: n={len(scored)} · three feeds {sum(r['feeds'] == 3 for r in scored)} · MAE {mean(abs(r['ppr_actual'] - r['ppr_proj']) for r in scored):.2f}"
              f" · QB bias {mean(r['ppr_proj'] - r['ppr_actual'] for r in qb):+.2f} · P(correct) gap 2-3 {curve[(2, 3)]:.1%}, 5-8 {curve[(5, 8)]:.1%}")
        for pos, stats in RATIO_STATS.items():
            for stat in stats:
                rs = [r for r in rows if r["pos"] == pos and r["proj"][stat] >= PROP_FLOORS[stat]]
                if rs:
                    print(f"    {pos} {stat} k50 {median(r['act'][stat] / r['proj'][stat] for r in rs):.2f} (n={len(rs)})")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Backtest the projection engine and write calibration.json")
    parser.add_argument("--seasons", nargs="+", default=["2023", "2024", "2025"])
    parser.add_argument("--cache", default=backtest_data.CACHE_DIR)
    parser.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration.json"))
    parser.add_argument("--no-pull", action="store_true", help="fit from the cache only")
    args = parser.parse_args(argv)
    load_dotenv(os.path.join(backtest_data.REPO_ROOT, ".env"))  # FANTASYPROS_API_KEY for the pull
    rows_by_season = {}
    for season in args.seasons:
        if not args.no_pull:
            backtest_data.pull_season(season, args.cache)
        rows_by_season[season] = backtest_data.load_season(season, args.cache)
    report(rows_by_season)
    rows = [r for rs in rows_by_season.values() for r in rs]
    data = build_calibration(rows, args.seasons, backtest_data.today(), backtest_data.load_games(args.seasons, args.cache))
    with open(args.out, "w") as f:
        json.dump(data, f, indent=1)
    print(f"wrote {args.out}: {len(rows)} player-weeks over {len(args.seasons)} seasons")
    return 0


if __name__ == "__main__":
    sys.exit(main())
