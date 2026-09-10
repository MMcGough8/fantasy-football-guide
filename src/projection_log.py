"""Append-only log of weekly projections and the actual points that followed.

One JSON record per line in `.projection_log.jsonl` (repo root, gitignored;
`PROJECTION_LOG_FILE` overrides the path so headless runs never touch the
owner's log). Projections are logged once per (season, week, league) when the
Start/Sit page renders; actuals are appended on request from Sleeper's weekly
stats. `accuracy_report` joins the two so feed weights and the matchup
coefficients can be tuned from evidence.
"""
import datetime
import json
from collections import defaultdict
import os

import requests

import matchups
from draft_board import POSITIONS
from scoring import score_stats

LOG_FILE = os.getenv("PROJECTION_LOG_FILE") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".projection_log.jsonl"
)
STATS_URL = "https://api.sleeper.com/stats/nfl/{season}/{week}"
TIMEOUT_SECONDS = 30
SOURCES = ("sleeper", "fp", "espn", "blend", "adjusted")
MIN_FEED_SAMPLE = 50  # player-weeks every feed needs before weights are suggested
MAE_FLOOR = 0.01  # a perfect feed still gets a finite weight


class ActualsError(Exception):
    """Raised when Sleeper's weekly stats cannot be fetched, with a user-facing message."""


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def read_records(path):
    """Every record in the log; a missing file is empty and bad lines are skipped."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def append_records(path, records):
    with open(path, "a") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def _same_slot(record, season, week, league_id):
    return (
        str(record.get("season")) == str(season)
        and str(record.get("week")) == str(week)
        and str(record.get("league_id")) == str(league_id)
    )


def is_logged(records, what, season, week, league_id):
    return any(
        r.get("kind") == "logged" and r.get("what") == what and _same_slot(r, season, week, league_id)
        for r in records
    )


def projection_records(season, week, league_id, scoring_code, pool, now=None):
    now = now or _now()
    records = []
    for player_id, row in pool.items():
        matchup = row.get("matchup") or {}
        records.append({
            "kind": "projection", "season": season, "week": week, "league_id": league_id,
            "scoring_code": scoring_code, "player_id": player_id, "name": row.get("name"),
            "position": row.get("position"), "team": row.get("team"), "opponent": row.get("opponent"),
            "points_by_source": row.get("points_by_source") or {}, "blend": row.get("points"),
            "adjusted": row.get("adjusted_points", row.get("points")), "fp_week_rank": row.get("fp_week_rank"),
            "implied": matchup.get("implied"), "dvp_factor": matchup.get("dvp_factor"), "site": matchup.get("site"),
            "logged_at": now,
        })
    return records


def _marker(what, season, week, league_id, count, now):
    return {
        "kind": "logged", "what": what, "season": season, "week": week, "league_id": league_id,
        "count": count, "logged_at": now,
        "coefficients": {"A_IMPLIED": matchups.A_IMPLIED, "B_DVP": matchups.B_DVP, "CAP": matchups.CAP, "SITE": matchups.SITE_TERMS},
    }


def log_projections(path, season, week, league_id, scoring_code, pool, now=None):
    """Append this week's projections once; returns how many were written (0 if already logged)."""
    if is_logged(read_records(path), "projection", season, week, league_id):
        return 0
    now = now or _now()
    records = projection_records(season, week, league_id, scoring_code, pool, now)
    append_records(path, records + [_marker("projection", season, week, league_id, len(records), now)])
    return len(records)


def log_actuals(path, season, week, league_id, actuals, now=None, force=False):
    """Append actual points ({player_id: points}) once (or again with `force`, e.g. after
    stat corrections; the latest actual wins when pairing). Returns how many were written."""
    if not force and is_logged(read_records(path), "actual", season, week, league_id):
        return 0
    now = now or _now()
    records = [
        {"kind": "actual", "season": season, "week": week, "league_id": league_id, "player_id": str(pid), "actual": pts}
        for pid, pts in actuals.items()
    ]
    append_records(path, records + [_marker("actual", season, week, league_id, len(records), now)])
    return len(records)


def fetch_actual_points(season, week, scoring_settings):
    """{player_id: points} for every player who played that week, scored with the
    league's rules and no bonus estimate (a game's bonuses are in the stats).

    Players Sleeper lists with no game played are left out: an injury is not a
    projection miss. A week with no stats at all is an error, never a week of zeros.
    """
    actual = {}
    for position, rec in fetch_week_stats(season, week):
        stats = rec.get("stats") or {}
        if not stats.get("gp"):
            continue
        points = score_stats(stats, scoring_settings, position, estimate_bonuses=False)
        if points is not None:
            actual[str(rec.get("player_id"))] = round(points, 1)
    return actual


def fetch_week_stats(season, week):
    """[(position, Sleeper stats record)] for the week, every position; raises when the week
    has no stats at all (a week that has not been played is an error, never a week of zeros)."""
    rows_seen, out = 0, []
    for position in POSITIONS:
        try:
            resp = requests.get(
                STATS_URL.format(season=season, week=week),
                params={"season_type": "regular", "position[]": position},
                timeout=TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            rows = resp.json() or []
        except requests.RequestException as e:
            raise ActualsError(f"Couldn't fetch week {week} stats from Sleeper ({e})") from e
        except ValueError as e:
            raise ActualsError(f"Sleeper returned an unexpected stats response ({e})") from e
        rows_seen += len(rows)
        out.extend((position, rec) for rec in rows)
    if rows_seen == 0:
        raise ActualsError(f"Sleeper has no stats for week {week} yet")
    return out


def raw_stats_by_player(season, week):
    """{player_id: stats} for every player Sleeper lists that week (the props log grades from this)."""
    return {str(rec.get("player_id")): (rec.get("stats") or {}) for _, rec in fetch_week_stats(season, week)}


def _slot_key(record):
    return (str(record.get("season")), str(record.get("week")), str(record.get("league_id")), str(record.get("player_id")))


def pair_records(records):
    """[(projection, actual)] for every logged projection with an actual. The latest
    projection and the latest actual for a player-week win, so a partial write that was
    logged again or a re-recorded week never double-counts."""
    actuals, projections = {}, {}
    for r in records:
        if r.get("kind") == "actual":
            actuals[_slot_key(r)] = r.get("actual")
        elif r.get("kind") == "projection":
            projections[_slot_key(r)] = r
    return [(p, actuals[key]) for key, p in projections.items() if actuals.get(key) is not None]


def _source_value(projection, source):
    if source == "blend":
        return projection.get("blend")
    if source == "adjusted":
        return projection.get("adjusted")
    return (projection.get("points_by_source") or {}).get(source)


def _errors(pairs):
    """{source: [signed errors]} over the pairs."""
    errors = {source: [] for source in SOURCES}
    for projection, actual in pairs:
        for source in SOURCES:
            value = _source_value(projection, source)
            if value is not None:
                errors[source].append(value - actual)
    return errors


def _summarize(errors):
    return {
        source: {"n": len(e), "mae": round(sum(abs(x) for x in e) / len(e), 2), "bias": round(sum(e) / len(e), 2)}
        for source, e in errors.items() if e
    }


FEEDS = ("sleeper", "fp", "espn")


def _feed_weights(pairs):
    """Inverse-MAE weights over the player-weeks every feed projected, once there are
    MIN_FEED_SAMPLE of them; None otherwise. Disjoint samples are not comparable."""
    common = [(p, a) for p, a in pairs if all((p.get("points_by_source") or {}).get(s) is not None for s in FEEDS)]
    if len(common) < MIN_FEED_SAMPLE:
        return None
    errors = _errors(common)
    inverse = {s: 1 / max(sum(abs(x) for x in errors[s]) / len(errors[s]), MAE_FLOOR) for s in FEEDS}
    return {s: round(w / sum(inverse.values()), 3) for s, w in inverse.items()}


GAP_BUCKETS = [[1, 2], [2, 3], [3, 5], [5, 8], [8, 99]]
NEIGHBOURS = 25


def decision_curve(records, min_points=5.0):
    """Share of same-week, same-position pairs where the higher blend scored more, by gap.
    Same shape as the calibration file's curve, so the two can sit side by side."""
    by = defaultdict(list)
    for projection, actual in pair_records(records):
        if (projection.get("blend") or 0) >= min_points:
            by[(projection.get("season"), projection.get("week"), projection.get("position"))].append((projection["blend"], actual))
    tally = {tuple(b): [0, 0] for b in GAP_BUCKETS}
    for pairs in by.values():
        srt = sorted(pairs, key=lambda pa: -pa[0])
        for i in range(len(srt)):
            for j in range(i + 1, min(i + NEIGHBOURS, len(srt))):
                gap = srt[i][0] - srt[j][0]
                for b in tally:
                    if b[0] <= gap < b[1]:
                        tally[b][0] += 1
                        tally[b][1] += srt[i][1] > srt[j][1]
                        break
    return [{"gap": list(b), "observed": round(w / n, 4) if n else None, "n": n} for b, (n, w) in tally.items()]


def accuracy_report(records, min_points=5.0):
    """MAE and bias per source (overall and by position), feed weights, and the logged
    projections that have no actual in a week whose actuals were recorded."""
    all_pairs = pair_records(records)
    pairs = [(p, a) for p, a in all_pairs if (p.get("blend") or 0) >= min_points]
    overall = _summarize(_errors(pairs))
    by_position = {}
    for position in sorted({p.get("position") for p, _ in pairs if p.get("position")}):
        by_position[position] = _summarize(_errors([(p, a) for p, a in pairs if p.get("position") == position]))
    recorded_weeks = {
        (str(r.get("season")), str(r.get("week")), str(r.get("league_id")))
        for r in records if r.get("kind") == "logged" and r.get("what") == "actual"
    }
    paired = {_slot_key(p) for p, _ in all_pairs}
    unmatched = sum(
        1 for r in records
        if r.get("kind") == "projection" and _slot_key(r)[:3] in recorded_weeks and _slot_key(r) not in paired
    )
    return {"pairs": len(pairs), "overall": overall, "by_position": by_position, "weights": _feed_weights(pairs), "unmatched": unmatched}
