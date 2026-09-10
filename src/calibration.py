"""The measured behaviour of the projection engine, read from `calibration.json`.

`src/backtest.py` writes the file from past seasons (2023-2025 at first); everything
here is a lookup into it: how far projections run hot by position, how wide the miss
is, the shape of each stat's outcome around its median, how counts disperse, the
touchdown rate, same-game correlations and weather factors. Start/Sit uses the
points half; Props uses the rest. No fitting happens here.
"""
import bisect
import json
import math
import os

from scoring import _normal_tail

CALIBRATION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration.json")
POISSON_DISPERSION_LIMIT = 1.2  # var/mean at or under this is Poisson; above it a negative binomial
COUNT_TAIL = 1e-9


class Calibration:
    """A loaded calibration file. `data` is the raw dict; the methods below are lookups."""

    def __init__(self, data):
        self.data = data

    @classmethod
    def load(cls, path=CALIBRATION_FILE):
        with open(path) as f:
            return cls(json.load(f))

    def points_fit(self, position):
        fits = self.data.get("points") or {}
        return fits.get(position) or fits.get("default") or {"a": 0.0, "b": 1.0, "c0": 3.5, "c1": 0.3}

    def _table(self, position, stat):
        return ((self.data.get("ratio_tables") or {}).get(position) or {}).get(stat)

    def band(self, position, stat, projection):
        """The ratio table for this projection size, or None below the floor / without a table."""
        table = self._table(position, stat)
        if not table or projection < table["floor"]:
            return None
        for band in table["bands"]:
            if band["lo"] <= projection < band["hi"]:
                return band
        return None

    def k50(self, position, stat, projection):
        band = self.band(position, stat, projection)
        return band["k50"] if band else None

    def games(self):
        """The game-line fit (margin and total spread around the market); {} when not fitted."""
        return self.data.get("games") or {}

    def count_fit(self, position, stat):
        return ((self.data.get("counts") or {}).get(position) or {}).get(stat)

    def weather_factor(self, position, kind, value):
        """Multiplier on a projection for wind (mph) or cold (degrees F); 1.0 when unknown."""
        table = (self.data.get("weather") or {}).get(kind)
        if not table or position not in table:
            return 1.0
        for (lo, hi), factor in zip(table["bands"], table[position]):
            if lo <= value < hi:
                return factor
        return 1.0


def shrink_points(cal, position, points):
    """The recalibrated expectation of a weekly projection (QB runs hot; most others are near identity)."""
    fit = cal.points_fit(position)
    return fit["a"] + fit["b"] * points


CALIBRATED_POSITIONS = ("QB", "RB", "WR", "TE")  # K and DEF fits extrapolate over a 2-point range; unvalidated


def _calibrated(cal, row, value):
    """The recalibrated value for a lineup row: identity for K/DEF and for a player who cannot score
    (a bye or a missing projection stays 0), never below zero."""
    if row.get("reason") or row.get("position") not in CALIBRATED_POSITIONS:
        return value
    return max(0.0, shrink_points(cal, row.get("position"), value))


def calibrate_rows(cal, rows):
    """New rows carrying `calibrated_points` (and `calibrated_adjusted_points`, from the matchup-adjusted
    number when the row has one) so cross-position comparisons and totals use the recalibrated scale."""
    out = []
    for r in rows:
        points = r.get("points") or 0.0
        adjusted = r.get("adjusted_points") if r.get("adjusted_points") is not None else points
        out.append({**r, "calibrated_points": round(_calibrated(cal, r, points), 1), "calibrated_adjusted_points": round(_calibrated(cal, r, adjusted), 1)})
    return out


def error_sd(cal, position, points):
    """The standard deviation of the miss on a weekly projection of this size."""
    fit = cal.points_fit(position)
    return fit["c0"] + fit["c1"] * points


def normal_cdf(z):
    return 1.0 - _normal_tail(z)


def swap_confidence(gap, sd_a, sd_b):
    """P(the higher-projected player scores more) for a projection gap between two players."""
    spread = math.hypot(sd_a, sd_b)
    if spread <= 0:
        return 1.0 if gap > 0 else 0.5
    return normal_cdf(gap / spread)


def outcome_cdf(cal, position, stat, projection, x):
    """P(actual <= x * median) from the band's quantile table; None when the stat has no table
    for a projection this size. Flat beyond the table's ends, right-continuous at a mass point."""
    band = cal.band(position, stat, projection)
    if band is None:
        return None
    xs, ps = band["x"], band["p"]
    if x < xs[0]:
        return 0.0
    if x >= xs[-1]:
        return 1.0
    i = bisect.bisect_right(xs, x) - 1  # the last table point at or below x
    if xs[i + 1] == xs[i]:
        return ps[i + 1]
    return ps[i] + (ps[i + 1] - ps[i]) * (x - xs[i]) / (xs[i + 1] - xs[i])


def _poisson_pmf(mean):
    pmf, k, term, total = {}, 0, math.exp(-mean), 0.0
    while total < 1 - COUNT_TAIL and k < 200:
        pmf[k] = term
        total += term
        k += 1
        term *= mean / k
    return pmf


def _negative_binomial_pmf(mean, dispersion):
    """Mean `mean`, variance `dispersion * mean` (dispersion > 1)."""
    r = mean / (dispersion - 1)
    p = r / (r + mean)
    pmf, k, total = {}, 0, 0.0
    log_pr = r * math.log(p)
    while total < 1 - COUNT_TAIL and k < 400:
        log_term = math.lgamma(k + r) - math.lgamma(k + 1) - math.lgamma(r) + log_pr + k * math.log(1 - p)
        pmf[k] = math.exp(log_term)
        total += pmf[k]
        k += 1
    return pmf


def count_pmf(cal, position, stat, mean):
    """{k: P(X = k)} for a count stat (receptions, passing TDs) with the fitted dispersion."""
    fit = cal.count_fit(position, stat) or {}
    dispersion = fit.get("dispersion", 1.0)
    if mean <= 0:
        return {0: 1.0}
    if dispersion <= POISSON_DISPERSION_LIMIT:
        return _poisson_pmf(mean)
    return _negative_binomial_pmf(mean, dispersion)


def td_probability(cal, projected_tds):
    """P(at least one touchdown) from the projected count: a Poisson with the fitted factor."""
    if projected_tds <= 0:
        return 0.0
    k = (cal.data.get("touchdowns") or {}).get("k", 1.0)
    return 1.0 - math.exp(-k * projected_tds)


def correlation(cal, market_a, market_b, relation):
    """Residual correlation between two legs' outcomes; keyed by market pair and relation,
    symmetric, with per-relation defaults and 0 across games."""
    table = cal.data.get("correlations") or {}
    pairs = table.get("pairs") or {}
    for key in (f"{market_a}|{market_b}|{relation}", f"{market_b}|{market_a}|{relation}"):
        if key in pairs:
            return pairs[key]
    if relation.startswith("same_team"):
        return table.get("default_same_team", 0.0)
    if relation.startswith("opponent"):
        return table.get("default_opponent", 0.0)
    return table.get("default_cross_game", 0.0)


# Acklam's rational approximation of the normal quantile (relative error ~1e-9)
_A = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
_B = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00)
_P_LOW = 0.02425


def normal_ppf(p):
    """The standard normal quantile for 0 < p < 1."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be strictly between 0 and 1")
    if p < _P_LOW or p > 1 - _P_LOW:
        q = math.sqrt(-2 * math.log(p if p < _P_LOW else 1 - p))
        z = (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1)
        return z if p < _P_LOW else -z
    q = p - 0.5
    r = q * q
    return (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1)
