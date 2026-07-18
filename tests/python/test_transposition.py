"""Tests for ai/transposition.py."""

from ai.transposition import (
    TranspositionTable, TT_EXACT, TT_LOWER,
)


def test_put_get_round_trip():
    tt = TranspositionTable()
    tt.put(123, depth=4, score=42, flag=TT_EXACT, best_move=("m",))
    e = tt.get(123)
    assert e is not None
    assert e.depth == 4 and e.score == 42
    assert e.flag == TT_EXACT and e.best_move == ("m",)


def test_depth_prefer_replacement_keeps_deeper():
    """Writing a shallower entry over a deeper one must NOT replace it."""
    tt = TranspositionTable()
    tt.put(1, depth=8, score=100, flag=TT_EXACT, best_move="deep")
    tt.put(1, depth=2, score=999, flag=TT_EXACT, best_move="shallow")
    e = tt.get(1)
    assert e.depth == 8 and e.best_move == "deep"


def test_depth_prefer_replacement_overwrites_equal_or_shallower():
    """Writing an equal-or-deeper entry replaces."""
    tt = TranspositionTable()
    tt.put(1, depth=3, score=100, flag=TT_EXACT, best_move="old")
    tt.put(1, depth=3, score=200, flag=TT_LOWER, best_move="new")
    e = tt.get(1)
    assert e.score == 200 and e.best_move == "new"


def test_eviction_when_full_keeps_deeper_entries():
    """When full, eviction prefers low-depth entries (keep deeper)."""
    tt = TranspositionTable(max_entries=10)
    # Insert 10 shallow entries
    for k in range(10):
        tt.put(k, depth=1, score=k, flag=TT_EXACT, best_move=k)
    # Insert one deep entry — triggers eviction
    tt.put(999, depth=8, score=42, flag=TT_EXACT, best_move="deep")
    deep = tt.get(999)
    assert deep is not None and deep.depth == 8
    # Table should not exceed bound after eviction + insert
    assert len(tt._table) <= 10


# ---------------------------------------------------------------------------
# v1.4: generation aging + static-eval slot (use_tt_generation /
# use_tt_static_eval)
# ---------------------------------------------------------------------------

def test_generation_aging_replaces_stale_deep_entries():
    """A deep entry from a PREVIOUS search must not block fresh stores."""
    tt = TranspositionTable(max_entries=10, generation_aging=True)
    tt.put(1, depth=8, score=100, flag=TT_EXACT, best_move="stale-deep")
    tt.new_search()
    tt.put(1, depth=2, score=7, flag=TT_EXACT, best_move="fresh")
    e = tt.get(1)
    assert e.depth == 2 and e.best_move == "fresh"


def test_generation_aging_same_search_keeps_deeper():
    """Within one search, the depth-prefer rule still applies."""
    tt = TranspositionTable(max_entries=10, generation_aging=True)
    tt.put(1, depth=8, score=100, flag=TT_EXACT, best_move="deep")
    tt.put(1, depth=2, score=7, flag=TT_EXACT, best_move="shallow")
    e = tt.get(1)
    assert e.depth == 8 and e.best_move == "deep"


def test_generation_aging_overflow_evicts_one_entry_not_half():
    """Aging mode must never run the O(n log n) halving sort: at capacity it
    evicts exactly one (oldest-inserted) entry per new insert."""
    tt = TranspositionTable(max_entries=4, generation_aging=True)
    for k in range(4):
        tt.put(k, depth=5, score=k, flag=TT_EXACT, best_move=k)
    tt.put(99, depth=1, score=42, flag=TT_EXACT, best_move="new")
    assert len(tt._table) == 4
    assert tt.get(99) is not None       # new entry was inserted
    assert tt.get(0) is None            # exactly the oldest was evicted
    assert tt.get(1) is not None and tt.get(2) is not None and tt.get(3) is not None


def test_static_eval_round_trip_and_preservation():
    """static_eval is stored, and an overwrite without one keeps the old value."""
    tt = TranspositionTable(generation_aging=True)
    tt.put(5, depth=3, score=10, flag=TT_EXACT, best_move=None, static_eval=123)
    assert tt.get(5).static_eval == 123
    tt.put(5, depth=4, score=20, flag=TT_LOWER, best_move=None)  # no static_eval
    e = tt.get(5)
    assert e.depth == 4 and e.static_eval == 123
