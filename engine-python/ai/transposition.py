"""Transposition table using Zobrist hashes.

Two replacement policies, selected at construction:

* Legacy (``generation_aging=False``, the v1.3 behavior): depth-prefer — a
  new entry never overwrites a strictly deeper one, and on overflow the
  lowest-depth half is evicted with a full sort. That sort is O(n log n)
  over up to a million entries *in the middle of a timed search*; kept only
  so the frozen v13/baseline configs reproduce the 1.3 engine exactly.

* Generation aging (``generation_aging=True``, v1.4): each search bumps a
  generation counter. Depth-prefer applies only within the same search, so
  stale deep entries from previous moves can't block fresh stores. On
  overflow exactly one (oldest-inserted) entry is evicted in O(1) — no
  mid-search latency spike.

Entries also carry the node's static evaluation so hot paths can skip
recomputing it (gated by ``use_tt_static_eval`` in the search).
"""

from __future__ import annotations

TT_EXACT = 0
TT_LOWER = 1
TT_UPPER = 2

_MAX_ENTRIES = 1_000_000


class TTEntry:
    __slots__ = ("depth", "score", "flag", "best_move", "static_eval", "generation")

    def __init__(self, depth: int, score: int, flag: int, best_move,
                 static_eval: int | None = None, generation: int = 0) -> None:
        self.depth = depth
        self.score = score
        self.flag = flag
        self.best_move = best_move
        self.static_eval = static_eval
        self.generation = generation


class TranspositionTable:
    def __init__(self, max_entries: int = _MAX_ENTRIES,
                 generation_aging: bool = False) -> None:
        self._table: dict[int, TTEntry] = {}
        self._max = max_entries
        self._aging = generation_aging
        self._generation = 0

    def new_search(self) -> None:
        """Mark the start of a new search (generation-aging mode)."""
        self._generation += 1

    def get(self, key: int) -> TTEntry | None:
        return self._table.get(key)

    def put(self, key: int, depth: int, score: int, flag: int, best_move,
            static_eval: int | None = None) -> None:
        table = self._table
        existing = table.get(key)
        # An overwrite without a static eval keeps the one already stored —
        # safe across generations because a static eval is a pure function of
        # the position key (same key = same board + side to move = same value).
        if static_eval is None and existing is not None:
            static_eval = existing.static_eval

        if self._aging:
            if (existing is not None
                    and existing.generation == self._generation
                    and existing.depth > depth):
                return  # keep deeper analysis from the same search
            if existing is None and len(table) >= self._max:
                # O(1) eviction: drop the oldest-inserted entry.
                table.pop(next(iter(table)))
            table[key] = TTEntry(depth, score, flag, best_move,
                                 static_eval, self._generation)
            return

        # Legacy depth-prefer policy (v1.3).
        if existing is not None and existing.depth > depth:
            return  # keep deeper analysis
        if existing is None and len(table) >= self._max:
            self._evict_low_depth()
        table[key] = TTEntry(depth, score, flag, best_move,
                             static_eval, self._generation)

    def _evict_low_depth(self) -> None:
        # Sort by depth ascending; drop the lowest half. (Legacy policy only.)
        items = sorted(self._table.items(), key=lambda kv: kv[1].depth)
        cut = self._max // 2
        for k, _ in items[:cut]:
            del self._table[k]

    def clear(self) -> None:
        self._table.clear()
