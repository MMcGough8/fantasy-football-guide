"""Odds arithmetic shared by the player-prop and game-line pricers: American prices, de-vigging,
expected value per dollar and Kelly stakes. Pure functions, no market knowledge.
"""
from datetime import datetime

PREFERRED_BOOKS = ("draftkings", "fanduel")  # the owner's books; everything else is reference


def american_to_decimal(price):
    return 1 + price / 100 if price > 0 else 1 + 100 / abs(price)


def implied_probability(price):
    return 1 / american_to_decimal(price)


def fair_american(p):
    """The break-even American price for a probability (clamped away from 0 and 1)."""
    decimal = 1 / min(max(p, 1e-4), 1 - 1e-4)
    return int(round((decimal - 1) * 100)) if decimal >= 2 else -int(round(100 / (decimal - 1)))


def devig(price_a, price_b):
    """The two sides' probabilities with the book's hold removed (they sum to one)."""
    a, b = implied_probability(price_a), implied_probability(price_b)
    return a / (a + b), b / (a + b)


def leg_ev(p, price, push=0.0):
    """Expected profit per dollar staked; a push returns the stake."""
    return p * (american_to_decimal(price) - 1) - (1 - p - push)


def no_push_probability(p, push):
    """P(win) given the bet is not a push: what fair odds and stakes condition on."""
    return p if push >= 1 else p / (1 - push)


def kelly_fraction(p, price):
    b = american_to_decimal(price) - 1
    return max(0.0, (p * b - (1 - p)) / b)


def stake(p, price, bankroll, fraction=0.25, cap=0.05):
    """A quarter-Kelly stake capped at `cap` of the bankroll; None without a bankroll."""
    if not bankroll:
        return None
    return round(min(cap, fraction * kelly_fraction(p, price)) * bankroll, 2)


def parse_commence(value):
    """The Odds API's ISO kickoff (UTC) as an aware datetime, or None for anything else."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except (ValueError, TypeError, AttributeError):
        return None
    return parsed if parsed is not None and parsed.tzinfo is not None else None
