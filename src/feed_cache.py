"""A small on-disk cache for feed results, so a server restart does not refetch every feed.
Streamlit's in-memory cache still serves reruns; this layer sits inside the cached loaders and
answers a cold start from `.feed_cache/` (`FEED_CACHE_DIR` overrides it; headless runs must point
it at a scratch path, since faked feeds would otherwise be served to the live app). Entries are
pickles of the loader's own return value with the time they were fetched; a stale, missing or
unreadable entry is a miss, a failed fetch is never stored, and file trouble is never an error.
"""
import os
import pickle
import re
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.getenv("FEED_CACHE_DIR") or os.path.join(REPO_ROOT, ".feed_cache")


def slug(*parts):
    """A filesystem-safe name from the loader's key parts."""
    return "-".join(re.sub(r"[^A-Za-z0-9_.]", "_", str(p)) for p in parts)


def _path(name):
    return os.path.join(CACHE_DIR, f"{name}.pkl")


def load(name, max_age, now=None):
    """The stored value when it is younger than `max_age` seconds, else None."""
    try:
        with open(_path(name), "rb") as f:
            entry = pickle.load(f)
        if (now or time.time()) - entry["fetched_at"] > max_age:
            return None
        return entry["value"]
    except (OSError, ValueError, TypeError, KeyError, pickle.UnpicklingError, EOFError, AttributeError, ImportError):
        return None


def save(name, value):
    """Store the value (written to a temp file, then renamed, so a reader never sees half an entry)."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = _path(name) + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump({"fetched_at": time.time(), "value": value}, f)
        os.replace(tmp, _path(name))
        return True
    except (OSError, pickle.PicklingError):
        return False


def cached(name, max_age, fetch, ok=lambda value: True):
    """The disk entry when fresh, else `fetch()`; the result is stored only when `ok(result)`."""
    hit = load(name, max_age)
    if hit is not None:
        return hit
    value = fetch()
    if ok(value):
        save(name, value)
    return value


def clear(prefixes):
    """Remove the entries whose name starts with one of `prefixes` (as `slug` builds them)."""
    removed = 0
    try:
        names = os.listdir(CACHE_DIR)
    except OSError:
        return 0
    for filename in names:
        if filename.endswith(".pkl") and any(filename.startswith(f"{p}-") or filename == f"{p}.pkl" for p in prefixes):
            try:
                os.remove(os.path.join(CACHE_DIR, filename))
                removed += 1
            except OSError:
                pass
    return removed
