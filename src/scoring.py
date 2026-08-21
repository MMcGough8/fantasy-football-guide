"""Score Sleeper stat projections with a league's own scoring settings."""
import math
import re

# Sleeper's K and DEF projections are too sparse to rescore (no short-range FG
# splits, one game of points-allowed), so Sleeper's own preset total is more
# trustworthy than anything we could compute for those positions.
PRESET_FALLBACK_POSITIONS = {"K", "DEF"}
PRESET_FALLBACK_KEY = "pts_std"

# Per-game threshold bonuses ("2 pts for a 100-yard rushing game") are not in
# season-total projections. Sleeper's key names encode the stat and threshold:
#   bonus_rush_yd_100 -> rush_yd >= 100, bonus_pass_cmp_25 -> pass_cmp >= 25.
THRESHOLD_BONUS = re.compile(r"^bonus_(pass_yd|rush_yd|rec_yd|rush_att|pass_cmp)_(\d+)$")

# Per-game spread for yardage stats, as a fraction of the per-game mean plus a
# floor. Checked against 14 real seasons; a 100 yd/game back clears 100 in about
# half his games.
PER_GAME_SD_RATIO = 0.33
PER_GAME_SD_FLOOR = 10.0
# Count stats (carries, completions) vary far less game to game than yards.
COUNT_STAT_SD = {"rush_att": 5.0, "pass_cmp": 5.5}
MAX_GAMES = 17  # Sleeper reports 18 (weeks, not games) for every player


def _normal_tail(z):
    """P(Z >= z) for a standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2))


def estimate_threshold_bonuses(stats, scoring_settings):
    """Expected bonus points per threshold key, estimated from season totals."""
    games = min(stats.get("gp") or 0, MAX_GAMES)
    if games <= 0:
        return {}
    estimates = {}
    for key, weight in scoring_settings.items():
        match = THRESHOLD_BONUS.match(key)
        if not match or not weight:
            continue
        stat, threshold = match.group(1), float(match.group(2))
        season_total = stats.get(stat) or 0
        if season_total <= 0:
            continue
        per_game = season_total / games
        sd = COUNT_STAT_SD.get(stat, PER_GAME_SD_RATIO * per_game + PER_GAME_SD_FLOOR)
        expected_games = games * _normal_tail((threshold - per_game) / sd)
        estimates[key] = round(expected_games * weight, 2)
    return estimates


# Long-TD bonuses are the one offensive category that is neither projected nor
# estimable from season totals.
LONG_TD_BONUS = re.compile(r"^(pass|rush|rec)_td_(40|50)p$")


def unprojected_bonus_keys(scoring_settings):
    """Non-zero offensive scoring keys the board cannot account for at all."""
    return sorted(k for k, w in scoring_settings.items() if w and LONG_TD_BONUS.match(k))


def score_stats(stats, scoring_settings, position):
    """Return projected points under `scoring_settings`, or None if no settings."""
    if not scoring_settings:
        return None
    if position in PRESET_FALLBACK_POSITIONS:
        return stats.get(PRESET_FALLBACK_KEY)
    total = 0.0
    for stat, value in stats.items():
        weight = scoring_settings.get(stat)
        if weight and value:
            total += value * weight
    total += sum(estimate_threshold_bonuses(stats, scoring_settings).values())
    return round(total, 1)


def _per(weight):
    """'1 per 25' style for fractional yardage weights, or the plain number."""
    if 0 < weight < 1:
        return f"1 per {round(1 / weight)}"
    return f"{weight:g} per"


def scoring_summary(scoring_settings):
    """One-line plain-English summary of the scoring rules that move the board."""
    s = scoring_settings
    parts = []
    if s.get("rec"):
        parts.append(f"{s['rec']:g} per reception")
    if s.get("pass_td"):
        parts.append(f"{s['pass_td']:g} per passing TD")
    if s.get("pass_yd"):
        parts.append(f"{_per(s['pass_yd'])} passing yds")
    if s.get("rush_yd") and s.get("rush_yd") == s.get("rec_yd"):
        parts.append(f"{_per(s['rush_yd'])} rushing/receiving yds")
    else:
        if s.get("rush_yd"):
            parts.append(f"{_per(s['rush_yd'])} rushing yds")
        if s.get("rec_yd"):
            parts.append(f"{_per(s['rec_yd'])} receiving yds")
    if s.get("pass_int"):
        parts.append(f"{s['pass_int']:g} per INT")
    if any(THRESHOLD_BONUS.match(k) and w for k, w in s.items()):
        parts.append("100-yd game bonuses")
    if any(LONG_TD_BONUS.match(k) and w for k, w in s.items()):
        parts.append("long-TD bonuses")
    return ", ".join(parts)
