import os
import time

import feed_cache
from feed_cache import cached, clear, load, save, slug


def test_slug_is_filesystem_safe_and_stable():
    assert slug("weekly_fp", "2026", 1, "PPR") == "weekly_fp-2026-1-PPR"
    assert slug("odd/name", "a b") == "odd_name-a_b"


def test_save_then_load_within_the_age_and_not_after(tmp_path, monkeypatch):
    monkeypatch.setattr(feed_cache, "CACHE_DIR", str(tmp_path))
    value = {"ok": True, "data": {("IND", "ATL"): {"spread_line": 3.0}}}  # tuple keys survive (pickle, not JSON)
    assert save("lines", value) is True
    assert load("lines", 60) == value
    assert load("lines", 60, now=time.time() + 61) is None  # stale
    assert load("missing", 60) is None
    (tmp_path / "lines.pkl").write_bytes(b"not a pickle")
    assert load("lines", 60) is None  # corrupt: as good as missing


def test_cached_fetches_once_and_never_stores_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(feed_cache, "CACHE_DIR", str(tmp_path))
    calls = []

    def fetch():
        calls.append(1)
        return {"ok": len(calls) > 1, "n": len(calls)}

    assert cached("feed", 60, fetch, ok=lambda v: v["ok"]) == {"ok": False, "n": 1}  # a failure is returned but not kept
    assert cached("feed", 60, fetch, ok=lambda v: v["ok"]) == {"ok": True, "n": 2}
    assert cached("feed", 60, fetch, ok=lambda v: v["ok"]) == {"ok": True, "n": 2}  # served from disk
    assert len(calls) == 2


def test_clear_removes_matching_entries_only(tmp_path, monkeypatch):
    monkeypatch.setattr(feed_cache, "CACHE_DIR", str(tmp_path))
    save(slug("fp", "2026"), 1)
    save(slug("weekly_fp", "2026", 1), 2)
    save(slug("schedule", "2026"), 3)
    assert clear(("fp", "weekly_fp")) == 2
    assert load(slug("schedule", "2026"), 60) == 3 and load(slug("fp", "2026"), 60) is None
    assert clear(("nothing",)) == 0


def test_an_unwritable_dir_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(feed_cache, "CACHE_DIR", str(tmp_path / "file.txt" / "nope"))
    (tmp_path / "file.txt").write_text("x")
    assert save("a", 1) is False and load("a", 60) is None
