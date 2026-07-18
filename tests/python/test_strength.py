"""Strength / regression tests for the enhanced engine.

These are DETERMINISTIC gates — a tactical suite the engine must solve and an
effective-depth floor — plus a small non-flaky self-play smoke test. The large
statistical win-rate gauntlet lives in ``tools/strength_harness.py`` and is run
manually (see the plan's Validation Commands).
"""

from dataclasses import fields, replace

from engine.board import Board
from engine.game_state import GameState
from engine.pieces import Animal, Color, make_piece_id
from ai.minimax import AIPlayer
from ai.search_config import (
    SearchConfig, baseline_config, strong_config,
    v13_strong_config, v14_strong_config,
    _V13_BOOL_FLAGS, _V14_BOOL_FLAGS,
)
from tools.strength_harness import play_match, play_one
from config import DEN_BLACK


def make_gs(*piece_specs) -> GameState:
    gs = GameState()
    gs.board = Board()
    for (c, r, color, animal) in piece_specs:
        pid = make_piece_id(color, animal)
        gs.board.place_piece(c, r, pid)
    return gs


def midgame() -> GameState:
    """A reproducible, out-of-book 12-piece midgame with Blue to move."""
    gs = make_gs(
        (0, 6, Color.BLUE, Animal.ELEPHANT), (2, 6, Color.BLUE, Animal.WOLF),
        (4, 6, Color.BLUE, Animal.LEOPARD), (6, 6, Color.BLUE, Animal.RAT),
        (0, 8, Color.BLUE, Animal.TIGER), (6, 8, Color.BLUE, Animal.LION),
        (0, 2, Color.BLACK, Animal.RAT), (2, 2, Color.BLACK, Animal.LEOPARD),
        (4, 2, Color.BLACK, Animal.WOLF), (6, 2, Color.BLACK, Animal.ELEPHANT),
        (0, 0, Color.BLACK, Animal.LION), (6, 0, Color.BLACK, Animal.TIGER),
    )
    gs.turn = Color.BLUE
    return gs


# ---------------------------------------------------------------------------
# SearchConfig sanity
# ---------------------------------------------------------------------------

def test_baseline_disables_all_bool_flags():
    base = baseline_config()
    assert all(getattr(base, f.name) is False
               for f in fields(SearchConfig) if isinstance(f.default, bool))


def test_strong_enables_all_bool_flags():
    strong = strong_config()
    assert all(getattr(strong, f.name) is True
               for f in fields(SearchConfig) if isinstance(f.default, bool))


# ---------------------------------------------------------------------------
# Tactical suite — deterministic strength gates
# ---------------------------------------------------------------------------

def test_strong_finds_den_entry_win():
    """One move from the enemy den → take the win."""
    gs = make_gs(
        (3, 1, Color.BLUE, Animal.WOLF),
        (6, 8, Color.BLACK, Animal.ELEPHANT),
    )
    gs.turn = Color.BLUE
    ai = AIPlayer(Color.BLUE, 2, strong_config())
    move = ai.get_best_move(gs, time_budget_ms=300)
    assert move is not None
    assert (move.tc, move.tr) == DEN_BLACK


def test_strong_avoids_horizon_blunder():
    """Quiescence gate: must not grab a Wolf that a Lion immediately recaptures.

    Tested at difficulty 0 (fixed shallow depth) so the trap sits exactly on the
    horizon — this exercises quiescence/SEE. The tactic is on the left edge with
    Blue up material and its den safe, so the position is not lost and avoiding
    the trade is genuinely best.
    """
    gs = make_gs(
        (0, 4, Color.BLUE, Animal.TIGER),
        (0, 5, Color.BLACK, Animal.WOLF),
        (0, 6, Color.BLACK, Animal.LION),
        (6, 8, Color.BLUE, Animal.ELEPHANT),
        (5, 8, Color.BLUE, Animal.LION),
        (6, 0, Color.BLACK, Animal.RAT),
    )
    gs.turn = Color.BLUE
    ai = AIPlayer(Color.BLUE, 0, strong_config())
    move = ai.get_best_move(gs)
    assert move is not None
    assert (move.fc, move.fr, move.tc, move.tr) != (0, 4, 0, 5)


def test_strong_prefers_faster_mate():
    """Two winning ideas exist; pick the immediate den entry."""
    gs = make_gs(
        (3, 1, Color.BLUE, Animal.WOLF),
        (0, 8, Color.BLUE, Animal.LION),
        (6, 0, Color.BLACK, Animal.RAT),
    )
    gs.turn = Color.BLUE
    ai = AIPlayer(Color.BLUE, 2, strong_config())
    move = ai.get_best_move(gs, time_budget_ms=500)
    assert move is not None
    assert (move.tc, move.tr) == DEN_BLACK


# ---------------------------------------------------------------------------
# Move ordering (Task 2)
# ---------------------------------------------------------------------------

def test_order_mvv_lva_prefers_cheap_attacker():
    """Two pieces can take the same victim → try the cheaper attacker first."""
    gs = make_gs(
        (0, 4, Color.BLACK, Animal.CAT),    # victim (rank 2)
        (0, 3, Color.BLUE, Animal.DOG),     # cheap attacker (rank 3) — preferred
        (0, 5, Color.BLUE, Animal.LION),    # expensive attacker (rank 7)
    )
    gs.turn = Color.BLUE
    ai = AIPlayer(Color.BLUE, 2, strong_config())
    ordered = ai._order_moves(gs.legal_moves(), None, 0, None, gs.board)
    dog_cap = next(m for m in ordered if (m.fc, m.fr, m.tc, m.tr) == (0, 3, 0, 4))
    lion_cap = next(m for m in ordered if (m.fc, m.fr, m.tc, m.tr) == (0, 5, 0, 4))
    assert ordered.index(dog_cap) < ordered.index(lion_cap)


def test_order_see_demotes_losing_capture():
    """A capture that loses material to recapture is ordered behind quiet moves."""
    gs = make_gs(
        (3, 4, Color.BLUE, Animal.TIGER),   # attacker (rank 6)
        (3, 5, Color.BLACK, Animal.WOLF),   # victim (rank 4), defended
        (3, 6, Color.BLACK, Animal.LION),   # defender (rank 7) > Tiger
    )
    gs.turn = Color.BLUE
    ai = AIPlayer(Color.BLUE, 2, strong_config())
    ordered = ai._order_moves(gs.legal_moves(), None, 0, None, gs.board)
    losing_cap = next(m for m in ordered if (m.fc, m.fr, m.tc, m.tr) == (3, 4, 3, 5))
    quiet = next(m for m in ordered if m.captured == 0)
    assert ordered.index(quiet) < ordered.index(losing_cap)


# ---------------------------------------------------------------------------
# Den-aware quiescence (Task 6)
# ---------------------------------------------------------------------------

def test_generate_noisy_includes_den_entry():
    """Noisy moves include the (non-capture) den-entry move; captures do not."""
    from engine.move_generator import generate_noisy_moves, generate_capture_moves
    gs = make_gs(
        (3, 1, Color.BLUE, Animal.WOLF),    # one step from black den (3,0)
        (0, 0, Color.BLACK, Animal.LION),
    )
    den_move = (3, 1, 3, 0)
    noisy = {(m.fc, m.fr, m.tc, m.tr) for m in generate_noisy_moves(gs.board, Color.BLUE)}
    caps = {(m.fc, m.fr, m.tc, m.tr) for m in generate_capture_moves(gs.board, Color.BLUE)}
    assert den_move in noisy
    assert den_move not in caps


def test_quiescence_sees_den_dash():
    """At a leaf node, quiescence must see a one-move den win (noisy quiescence)."""
    gs = make_gs(
        (3, 1, Color.BLUE, Animal.WOLF),
        (0, 0, Color.BLACK, Animal.LION),
    )
    gs.turn = Color.BLUE
    ai = AIPlayer(Color.BLUE, 2, strong_config())
    score = ai._quiesce(gs, -10_000_000, 10_000_000, qply=0, ply=0)
    assert score > 1_000_000, f"quiescence missed the den dash (score={score})"


# ---------------------------------------------------------------------------
# Effective-depth floor
# ---------------------------------------------------------------------------

def test_effective_depth_floor():
    """Hard search reaches a sane depth within 1s on a midgame position.

    Uses an out-of-book midgame (the opening book would otherwise short-circuit
    the search and leave _last_depth at 0).
    """
    ai = AIPlayer(Color.BLUE, 2, strong_config())
    ai.get_best_move(midgame(), time_budget_ms=1000)
    assert ai._last_depth >= 4, f"only reached depth {ai._last_depth}"


def test_tiny_budget_returns_legal_move():
    """Even a 1ms budget must yield a legal move, never None (partial iteration)."""
    gs = midgame()
    legal = {(m.fc, m.fr, m.tc, m.tr) for m in gs.legal_moves()}
    ai = AIPlayer(Color.BLUE, 2, strong_config())
    move = ai.get_best_move(gs, time_budget_ms=1)
    assert move is not None
    assert (move.fc, move.fr, move.tc, move.tr) in legal


# ---------------------------------------------------------------------------
# Search repetition scoring (v1.4, use_search_repetition)
# ---------------------------------------------------------------------------
# Legacy rule: any position seen before ANYWHERE (game history or search path)
# scores 0 on its first recurrence — throwing away wins that pass through a
# once-seen position. New rule: only in-search-path cycles and third real
# visits (pre-root count >= 2) are draws.

def _two_lions() -> GameState:
    gs = make_gs(
        (0, 8, Color.BLUE, Animal.LION),
        (6, 0, Color.BLACK, Animal.LION),
    )
    gs.turn = Color.BLUE
    return gs


def _play_cycle(gs: GameState) -> None:
    for sq in ((0, 8, 0, 7), (6, 0, 6, 1), (0, 7, 0, 8), (6, 1, 6, 0)):
        mv = next(m for m in gs.legal_moves()
                  if (m.fc, m.fr, m.tc, m.tr) == sq)
        gs.apply_move(mv)


def test_search_repetition_single_pre_root_visit_not_draw():
    """One earlier game occurrence must not poison the line (new rule),
    though the legacy (v13) rule scored it as a draw."""
    gs = _two_lions()
    _play_cycle(gs)   # current position now occurred once before in the game

    ai_new = AIPlayer(Color.BLUE, 2, strong_config())
    ai_new._setup_repetition_tracking(gs)
    assert ai_new._is_search_draw(gs) is False

    ai_old = AIPlayer(Color.BLUE, 2, v13_strong_config())
    ai_old._setup_repetition_tracking(gs)
    assert ai_old._is_search_draw(gs) is True


def test_search_repetition_third_visit_draws():
    """Two pre-root occurrences -> the third visit is a draw (shuffle guard)."""
    gs = _two_lions()
    _play_cycle(gs)
    _play_cycle(gs)
    ai = AIPlayer(Color.BLUE, 2, strong_config())
    ai._setup_repetition_tracking(gs)
    assert ai._is_search_draw(gs) is True


def test_search_repetition_in_path_cycle_draws():
    """A cycle occurring entirely inside the search path is a draw."""
    gs = _two_lions()
    ai = AIPlayer(Color.BLUE, 2, strong_config())
    ai._setup_repetition_tracking(gs)   # root = start position
    _play_cycle(gs)                     # simulate the search path revisiting it
    assert ai._is_search_draw(gs) is True


def test_search_repetition_ignores_cycles_across_null_moves():
    """A 'repetition' whose loop crosses a null move includes a fictional
    pass, so it is not claimable and must not score as a draw. (Reaching the
    pre-null position again requires an odd closed walk — only possible via
    river jumps — so the floor semantics are pinned directly here.)"""
    gs = _two_lions()
    ai = AIPlayer(Color.BLUE, 2, strong_config())
    ai._setup_repetition_tracking(gs)
    # Simulate: the current position occurred on the search path once...
    gs._hash_history.append(gs.board.turn_hash(gs.turn))
    # ...but a null move happened after that occurrence.
    ai._null_floor = len(gs._hash_history)
    assert ai._is_search_draw(gs) is False
    # Without the intervening null it is a genuine in-path cycle.
    ai._null_floor = ai._root_hist_len
    assert ai._is_search_draw(gs) is True


def test_search_wins_through_once_seen_position():
    """A winning line through a position seen ONCE earlier in the game must
    still be found; the legacy rule scored that line 0 and avoided it."""
    gs = make_gs(
        (3, 2, Color.BLUE, Animal.WOLF),      # two steps from the Black den
        (6, 6, Color.BLUE, Animal.ELEPHANT),  # Blue clearly winning on material
        (0, 3, Color.BLACK, Animal.RAT),
    )
    gs.turn = Color.BLUE
    # Pretend the position after Wolf (3,2)->(3,1) already occurred once.
    mv = next(m for m in gs.legal_moves()
              if (m.fc, m.fr, m.tc, m.tr) == (3, 2, 3, 1))
    gs.apply_move(mv)
    seen_once = gs.board.turn_hash(gs.turn)
    gs.undo_move()
    gs._hash_history.append(seen_once)

    new_move = AIPlayer(Color.BLUE, 0, strong_config()).get_best_move(gs)
    old_move = AIPlayer(Color.BLUE, 0, v13_strong_config()).get_best_move(gs)
    assert (new_move.fc, new_move.fr, new_move.tc, new_move.tr) == (3, 2, 3, 1)
    assert (old_move.fc, old_move.fr, old_move.tc, old_move.tr) != (3, 2, 3, 1)


# ---------------------------------------------------------------------------
# v1.3 engine freeze — regression control for the strength gate
# ---------------------------------------------------------------------------
# The (move, nodes) tuples below were captured at the 1.3 release commit with
# fixed-depth searches. They pin the *behavior* of the flag-off code paths:
# if one of these fails, an engine change leaked outside its SearchConfig flag
# and "selfplay --a strong --b v13" no longer measures the 1.3 engine.

def _fixed_depth_signature(cfg: SearchConfig, gs: GameState, difficulty: int):
    ai = AIPlayer(gs.turn, difficulty, cfg)
    move = ai.get_best_move(gs)
    return (move.fc, move.fr, move.tc, move.tr, ai._nodes)


def _start_position() -> GameState:
    gs = GameState()
    gs.new_game()
    return gs


def test_v13_flags_match_the_13_release():
    """v13 = every 1.3-era bool flag on, every newer bool flag off."""
    v13 = v13_strong_config()
    for f in fields(SearchConfig):
        if isinstance(f.default, bool):
            expected = f.name in _V13_BOOL_FLAGS
            assert getattr(v13, f.name) is expected, f.name


def test_v13_signature_reproduces_13_engine():
    """v13 search behavior is byte-identical to the shipped 1.3 engine."""
    assert _fixed_depth_signature(v13_strong_config(), midgame(), 1) \
        == (6, 6, 6, 5, 3793)
    assert _fixed_depth_signature(v13_strong_config(), _start_position(), 0) \
        == (1, 7, 2, 7, 1049)


def test_stability_time_extension_capped():
    """A banked surplus extends the hard limit, capped at +50% of nominal."""
    ai = AIPlayer(Color.BLUE, 2, strong_config())
    ai._time_bank = 10.0
    ai.get_best_move(midgame(), time_budget_ms=300)
    assert 0.3 <= ai._time_limit <= 0.3 * 1.5 + 1e-9


def test_stability_time_off_keeps_nominal_limit():
    """With the flag off (v13/baseline), the bank must never be applied."""
    cfg = replace(strong_config(), use_stability_time=False)
    ai = AIPlayer(Color.BLUE, 2, cfg)
    ai._time_bank = 10.0
    ai.get_best_move(midgame(), time_budget_ms=300)
    assert ai._time_limit == 0.3


def test_stability_time_banks_unused_budget():
    """A trivial position (mate on the board) finishes early and banks time."""
    gs = make_gs(
        (3, 1, Color.BLUE, Animal.WOLF),
        (6, 8, Color.BLACK, Animal.ELEPHANT),
    )
    gs.turn = Color.BLUE
    ai = AIPlayer(Color.BLUE, 2, strong_config())
    ai.get_best_move(gs, time_budget_ms=800)
    assert ai._time_bank > 0.0


def test_v15_search_flags_are_wired():
    """Each v1.5 search flag must actually change the search tree: flag-on
    and flag-off fixed-depth signatures differ (node counts diverge)."""
    base = strong_config()
    for flag in ("use_lmr_matrix", "use_improving", "use_cont_history"):
        on = _fixed_depth_signature(replace(base, **{flag: True}),
                                    midgame(), 1)
        off = _fixed_depth_signature(replace(base, **{flag: False}),
                                     midgame(), 1)
        assert on != off, f"{flag} appears to have no effect on the search"


def test_lmr_matrix_shape_sane():
    """Reductions grow with depth and move index and never go negative in
    the table itself (adjustments are clamped by the r > 0 guard)."""
    from ai.minimax import _LMR_TABLE
    assert _LMR_TABLE[3][4] <= _LMR_TABLE[10][4] <= _LMR_TABLE[31][4]
    assert _LMR_TABLE[8][4] <= _LMR_TABLE[8][20] <= _LMR_TABLE[8][63]
    assert all(r >= 0 for row in _LMR_TABLE for r in row)


def test_tt_static_eval_cache_is_search_neutral():
    """Reusing cached static evals must not change any search decision —
    identical best move and node count, only wall-time improves."""
    on = replace(strong_config(), use_tt_static_eval=True)
    off = replace(strong_config(), use_tt_static_eval=False)
    assert _fixed_depth_signature(on, midgame(), 1) \
        == _fixed_depth_signature(off, midgame(), 1)


def test_baseline_signature_immutable():
    """The all-off engine (harness control) never changes behavior."""
    assert _fixed_depth_signature(baseline_config(), midgame(), 1) \
        == (6, 6, 6, 5, 5343)
    assert _fixed_depth_signature(baseline_config(), _start_position(), 0) \
        == (6, 6, 6, 5, 1785)


def test_v14_flags_match_the_14_release():
    """v14 = every 1.4-era bool flag on, every newer bool flag off."""
    v14 = v14_strong_config()
    for f in fields(SearchConfig):
        if isinstance(f.default, bool):
            expected = f.name in _V14_BOOL_FLAGS
            assert getattr(v14, f.name) is expected, f.name


def test_v14_signature_reproduces_14_engine():
    """v14 pinned at the v1.5 freeze instant (post trap-rule fix), where it
    was captured equal to strong_config() — the v1.5 gate control."""
    assert _fixed_depth_signature(v14_strong_config(), midgame(), 1) \
        == (6, 6, 6, 5, 3793)
    assert _fixed_depth_signature(v14_strong_config(), _start_position(), 0) \
        == (1, 7, 2, 7, 1049)


# ---------------------------------------------------------------------------
# Harness smoke — non-flaky (checks it runs and totals are consistent)
# ---------------------------------------------------------------------------

def test_harness_play_one_returns_color_or_none():
    res = play_one(strong_config(), baseline_config(),
                   budget_ms=50, max_moves=20, opening_seed=1, opening_plies=4)
    assert res is None or res in (Color.BLUE, Color.BLACK)


def test_harness_match_totals_consistent():
    res = play_match(strong_config(), strong_config(),
                     games=2, budget_ms=50, max_moves=20, opening_plies=4, seed=7)
    assert res["games"] == res["a_wins"] + res["b_wins"] + res["draws"]
    assert 0.0 <= res["a_score"] <= 1.0
