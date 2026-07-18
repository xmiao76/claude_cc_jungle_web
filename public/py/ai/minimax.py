"""Negamax alpha-beta search with:
- Principal Variation Search (PVS)
- Null Move Pruning (NMP)
- Late Move Reductions (LMR)
- Mate-distance scoring
- Aspiration windows
- Repetition / 50-move draw recognition (v1.4: path-cycle / third-visit rule
  instead of draw-on-any-first-recurrence)
- TT in main search and quiescence (v1.4: generation aging, O(1) eviction,
  cached static evals, TT move tried first in quiescence)
- Killer / counter-move / history heuristics with aging
- SEE-pruned quiescence with delta pruning (v1.4: dedicated noisy-move
  generator and non-terminal eval on the hot paths)
- Stability-based time management with a per-player time bank (v1.4)
- Optional opening book
"""

from __future__ import annotations

import math
import time

from engine.board import Move
from engine.game_state import GameState
from engine.move_generator import (
    generate_capture_moves, generate_noisy_moves, generate_noisy_only,
)
from engine.pieces import Color
from ai.evaluator import evaluate, evaluate_nonterminal, _INF
from ai.transposition import TranspositionTable, TT_EXACT, TT_LOWER, TT_UPPER
from ai.see import see_capture
from ai import opening_book
from ai.search_config import SearchConfig, strong_config
from config import (
    AI_DEPTH_EASY, AI_DEPTH_MEDIUM, AI_TIME_HARD_MS,
    QUIESCENCE_MAX_PLY, EVAL_WEIGHTS,
    NMP_REDUCTION, NMP_MIN_DEPTH, NMP_MIN_PIECES,
    LMR_MIN_DEPTH, LMR_MOVES_BEFORE,
    ASPIRATION_DELTA, ASPIRATION_MIN_DEPTH,
    STABILITY_STOP_ITERS, STABILITY_STOP_MIN_DEPTH, STABILITY_STOP_FRAC,
    TIME_BANK_MAX_FRAC, TIME_EXTEND_MAX_FRAC,
    USE_OPENING_BOOK,
    DEN_BLACK, DEN_BLUE,
)

_MATE = _INF - 1000
_MAX_PLY = 64
_MATE_BOUND = _MATE - _MAX_PLY  # any |score| >= bound is treated as mate-distance

# Log-based LMR reduction matrix (use_lmr_matrix): smoother than the legacy
# integer-step formula; indexed [depth][move_index], both clamped.
_LMR_TABLE: list[list[int]] = [[0] * 64 for _ in range(32)]
for _d in range(1, 32):
    for _i in range(1, 64):
        _LMR_TABLE[_d][_i] = int(0.5 + math.log(_d) * math.log(_i) / 2.0)

_LMR_HISTORY_THRESHOLD = 512   # history score that earns one less reduction

# Smart time management: don't *start* a new iteration if the previous one took
# long enough that the next is predicted to overrun the budget. A started
# iteration still runs to the hard limit and its partial result is kept, so this
# only skips starts that would barely progress. The hard limit bounds wall-clock.
_NEXT_ITER_FACTOR = 1.5


def _mate_in(ply: int) -> int:
    return _MATE - ply


def _mated_in(ply: int) -> int:
    return -_MATE + ply


def _tt_score_to_store(score: int, ply: int) -> int:
    """Adjust mate scores to be ply-independent for TT storage."""
    if score >= _MATE_BOUND:
        return score + ply
    if score <= -_MATE_BOUND:
        return score - ply
    return score


def _tt_score_from_probe(score: int, ply: int) -> int:
    """Reverse adjustment when reading from TT."""
    if score >= _MATE_BOUND:
        return score - ply
    if score <= -_MATE_BOUND:
        return score + ply
    return score


class AIPlayer:
    """AI player using negamax PVS with iterative deepening."""

    def __init__(self, color: Color, difficulty: int = 1,
                 cfg: SearchConfig | None = None) -> None:
        self.color = color
        self.difficulty = difficulty
        self.cfg = cfg or strong_config()
        self._tt = TranspositionTable(generation_aging=self.cfg.use_tt_generation)
        self._nodes = 0
        self._last_depth = 0          # last fully-completed search depth (for bench)
        self._seldepth = 0            # max ply reached including quiescence
        self._completed_root_moves = 0  # root moves finished in the current iteration
        self._start_time = 0.0
        self._time_limit = 0.0
        self._stopped = False
        self._best_move_root: Move | None = None
        # Killer moves: two non-capture beta-cutoff moves per ply.
        self._killers: list[list[Move | None]] = [[None, None] for _ in range(_MAX_PLY)]
        # History heuristic: keyed by (fc, fr, tc, tr).
        self._history: dict[tuple[int, int, int, int], int] = {}
        # Counter-move heuristic: prev_move_tuple -> reply move.
        self._counter: dict[tuple[int, int, int, int], Move] = {}
        # Continuation history (use_cont_history): keyed on the previous
        # move's destination and this move's destination.
        self._cont_hist: dict[tuple[int, int, int, int], int] = {}
        # Capture history (use_capture_history): keyed on
        # (attacker_rank, to_col, to_row, victim_rank).
        self._capt_hist: dict[tuple[int, int, int, int], int] = {}
        # Static evals along the current search path (use_improving).
        self._static_stack: list[int | None] = [None] * _MAX_PLY
        # Repetition tracking for the current search (use_search_repetition):
        # length of the game history at the search root, and occurrence counts
        # of every pre-root position hash.
        self._root_hist_len = 0
        self._pre_root_counts: dict[int, int] = {}
        # Repetition scan floor: index into _hash_history below which path
        # entries are ignored. Raised past a null move so cycles that cross a
        # fictional pass are not scored as claimable repetitions.
        self._null_floor = 0
        # Stability time management (use_stability_time): unused nominal budget
        # banked across this player's moves, spendable on unstable searches.
        self._time_bank = 0.0
        self._base_time_limit = 0.0

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def get_best_move(self, state: GameState, time_budget_ms: int = AI_TIME_HARD_MS) -> Move | None:
        moves = state.legal_moves()
        if not moves:
            return None
        if len(moves) == 1:
            return moves[0]

        # Opening book (only on Hard, for the first dozen plies)
        if USE_OPENING_BOOK and self.difficulty >= 2 and len(state.history) < 12:
            book_move = opening_book.lookup(state)
            if book_move is not None:
                return book_move

        self._nodes = 0
        self._last_depth = 0
        self._seldepth = 0
        self._stopped = False
        self._start_time = time.perf_counter()
        self._reset_search_heuristics()
        self._setup_repetition_tracking(state)
        if self.cfg.use_tt_generation:
            self._tt.new_search()

        if self.difficulty == 0:
            self._time_limit = 999_999.0
            return self._search_fixed_depth(state, AI_DEPTH_EASY)
        if self.difficulty == 1:
            self._time_limit = 999_999.0
            return self._search_fixed_depth(state, AI_DEPTH_MEDIUM)

        base = time_budget_ms / 1000.0
        self._base_time_limit = base
        if self.cfg.use_stability_time:
            # The hard limit may be extended from the bank; the deepening loop
            # only draws on the extension while the best move is unstable.
            self._time_limit = base + min(self._time_bank,
                                          base * TIME_EXTEND_MAX_FRAC)
        else:
            self._time_limit = base
        move = self._search_iterative_deepening(state)
        if self.cfg.use_stability_time:
            used = time.perf_counter() - self._start_time
            bank = self._time_bank + (base - used)
            self._time_bank = min(max(bank, 0.0), base * TIME_BANK_MAX_FRAC)
        return move

    # ------------------------------------------------------------------
    # Search drivers
    # ------------------------------------------------------------------

    def _search_fixed_depth(self, state: GameState, depth: int) -> Move | None:
        self._best_move_root = None
        self._negamax_root(state, depth, -_INF, _INF)
        self._last_depth = depth
        return self._best_move_root

    def _search_iterative_deepening(self, state: GameState) -> Move | None:
        best_move = state.legal_moves()[0]
        self._best_move_root = best_move
        prev_score: int | None = None
        last_iter_dur = 0.0
        use_stability = self.cfg.use_stability_time
        base_limit = self._base_time_limit
        stable_iters = 0   # consecutive completed iterations with the same best
        for depth in range(1, 32):
            # ---- Smart time: decide whether to START this iteration ----
            elapsed = time.perf_counter() - self._start_time
            if self.cfg.use_smart_time:
                # Always complete depth 1; otherwise skip an iteration predicted
                # to overrun the budget (its partial result wouldn't beat the
                # full result we already have).
                if depth > 1 and last_iter_dur > 0.0:
                    if use_stability:
                        # A search whose best move just changed may dip into
                        # the banked extension; a stable one is held to the
                        # nominal budget.
                        start_limit = (self._time_limit if stable_iters == 0
                                       else base_limit)
                    else:
                        start_limit = self._time_limit
                    if elapsed + last_iter_dur * _NEXT_ITER_FACTOR > start_limit:
                        break
            elif self._time_expired():
                break

            iter_start = time.perf_counter()
            self._stopped = False
            self._completed_root_moves = 0

            # Aspiration windows after a few completed iterations.
            if prev_score is not None and depth >= ASPIRATION_MIN_DEPTH:
                delta = ASPIRATION_DELTA
                while True:
                    alpha = prev_score - delta
                    beta = prev_score + delta
                    score = self._negamax_root(state, depth, alpha, beta)
                    if self._stopped:
                        break
                    if score <= alpha or score >= beta:
                        delta *= 4
                        if delta > 1600:
                            score = self._negamax_root(state, depth, -_INF, _INF)
                            break
                        continue
                    break
            else:
                score = self._negamax_root(state, depth, -_INF, _INF)

            if not self._stopped and self._best_move_root is not None:
                if self._best_move_root == best_move:
                    stable_iters += 1
                else:
                    stable_iters = 0
                best_move = self._best_move_root
                prev_score = score
                self._last_depth = depth
            elif (self.cfg.use_partial_iteration
                    and self._completed_root_moves >= 1
                    and self._best_move_root is not None):
                # Interrupted iteration: keep the deeper best move found so far.
                # (It is at worst the previous PV move searched one ply deeper.)
                best_move = self._best_move_root
            last_iter_dur = time.perf_counter() - iter_start
            # Age history between iterations.
            self._age_history()
            if self._time_expired():
                break
            # ---- Stability early stop: bank the rest of the nominal budget ----
            if (use_stability
                    and stable_iters >= STABILITY_STOP_ITERS
                    and self._last_depth >= STABILITY_STOP_MIN_DEPTH
                    and time.perf_counter() - self._start_time
                    >= STABILITY_STOP_FRAC * base_limit):
                break
        return best_move

    def _time_expired(self) -> bool:
        return (time.perf_counter() - self._start_time) >= self._time_limit

    def _reset_search_heuristics(self) -> None:
        for slots in self._killers:
            slots[0] = None
            slots[1] = None
        self._history.clear()
        self._counter.clear()
        self._cont_hist.clear()
        self._capt_hist.clear()

    def _age_history(self) -> None:
        for table in (self._history, self._cont_hist, self._capt_hist):
            for k in list(table.keys()):
                v = table[k] >> 1
                if v == 0:
                    del table[k]
                else:
                    table[k] = v

    # ------------------------------------------------------------------
    # Repetition / draw scoring
    # ------------------------------------------------------------------

    def _setup_repetition_tracking(self, state: GameState) -> None:
        """Record the search-root boundary and pre-root position counts."""
        hist = state._hash_history
        self._root_hist_len = len(hist)
        self._null_floor = self._root_hist_len
        counts: dict[int, int] = {}
        for h in hist:
            counts[h] = counts.get(h, 0) + 1
        self._pre_root_counts = counts

    def _is_search_draw(self, state: GameState) -> bool:
        """Draw detection inside the search tree.

        Legacy rule (``use_search_repetition`` off, the pre-1.4 behavior):
        any position seen before anywhere — game history or search path —
        scores as a draw on its first recurrence. That also zeroes winning
        lines that merely pass through a once-seen position.

        New rule (flag on):
          * a repeat of any position on the current search path is a draw
            (cycle detection), and
          * a position already seen twice in the pre-root game history is a
            draw (the third real visit is at hand).
        A single pre-root occurrence no longer poisons the line.
        """
        if state.is_50_move_draw():
            return True
        if not self.cfg.use_search_repetition:
            return state.is_repetition()
        h = state.board.turn_hash(state.turn)
        hist = state._hash_history
        floor = self._null_floor
        for i in range(len(hist) - 1, floor - 1, -1):
            if hist[i] == h:
                return True
        if floor != self._root_hist_len:
            # Inside a null-move subtree: any repetition of a pre-null
            # position crosses the fictional pass, so it is not claimable.
            return False
        return self._pre_root_counts.get(h, 0) >= 2

    # ------------------------------------------------------------------
    # Move ordering
    # ------------------------------------------------------------------

    def _order_moves(self, moves: list[Move], tt_best: Move | None,
                     ply: int, prev_move: Move | None, board) -> list[Move]:
        if not moves:
            return moves
        killers = self._killers[ply] if 0 <= ply < _MAX_PLY else (None, None)
        counter = None
        if prev_move is not None:
            counter = self._counter.get((prev_move.fc, prev_move.fr, prev_move.tc, prev_move.tr))

        use_mvv_lva = self.cfg.use_mvv_lva_fix
        use_see = self.cfg.use_see_ordering
        capt_hist = self._capt_hist if self.cfg.use_capture_history else None
        cont_hist = self._cont_hist if (self.cfg.use_cont_history
                                        and prev_move is not None) else None
        if cont_hist is not None:
            prev_tc, prev_tr = prev_move.tc, prev_move.tr

        def key(m: Move) -> int:
            if tt_best is not None and m == tt_best:
                return -2_000_000_000 if capt_hist is not None else -1_000_000
            if m.captured:
                if not use_mvv_lva:
                    # Original (victim-only) ordering — preserved for baseline A/B.
                    return -100_000 - abs(m.captured) * 10 + abs(m.captured) - 0
                victim = abs(m.captured)
                attacker = abs(board.get(m.fc, m.fr))
                if use_see:
                    see_val = see_capture(board, m)
                    if see_val < 0:
                        # Losing capture: search it after quiet moves.
                        return 10_000 - see_val
                if capt_hist is not None:
                    # MVV first (victim class is never overridden); capture
                    # history can outvote the LVA attacker preference within
                    # the same victim class (every Jungle animal is unique per
                    # side, so a pure same-class tiebreak would never fire).
                    ch = capt_hist.get((attacker, m.tc, m.tr, victim), 0) >> 3
                    if ch > 2048:
                        ch = 2048
                    return -100_000_000 - victim * 4096 + attacker * 64 - ch
                # MVV-LVA: most valuable victim first, least valuable attacker first.
                return -100_000 - victim * 16 + attacker
            if m == killers[0]:
                return -50_000
            if m == killers[1]:
                return -49_000
            if counter is not None and m == counter:
                return -48_000
            score = self._history.get((m.fc, m.fr, m.tc, m.tr), 0)
            if cont_hist is not None:
                score += cont_hist.get((prev_tc, prev_tr, m.tc, m.tr), 0)
            return -score

        return sorted(moves, key=key)

    # ------------------------------------------------------------------
    # Negamax
    # ------------------------------------------------------------------

    def _negamax_root(self, state: GameState, depth: int, alpha: int, beta: int) -> int:
        original_alpha = alpha
        best_score = -_INF - 1
        self._completed_root_moves = 0

        moves = state.legal_moves()
        if not moves:
            return _mated_in(0)

        tt_entry = self._tt.get(state.board.turn_hash(state.turn))
        tt_best = tt_entry.best_move if tt_entry else None
        moves = self._order_moves(moves, tt_best, ply=0, prev_move=None, board=state.board)
        best_move = moves[0]

        for idx, move in enumerate(moves):
            if self._stopped:
                break
            state.apply_move(move)
            if idx == 0:
                score = -self._negamax(state, depth - 1, -beta, -alpha,
                                        ply=1, prev_move=move, allow_null=True)
            else:
                # PVS null-window probe
                score = -self._negamax(state, depth - 1, -alpha - 1, -alpha,
                                        ply=1, prev_move=move, allow_null=True)
                if not self._stopped and alpha < score < beta:
                    score = -self._negamax(state, depth - 1, -beta, -alpha,
                                            ply=1, prev_move=move, allow_null=True)
            state.undo_move()
            if self._stopped:
                break
            self._completed_root_moves += 1
            if score > best_score:
                best_score = score
                best_move = move
                # Commit improvements immediately so an interrupted deeper
                # iteration can still return its best move so far. The
                # original_alpha guard avoids committing fail-low artifacts from
                # a narrow aspiration window.
                if score > original_alpha:
                    self._best_move_root = best_move
            if score > alpha:
                alpha = score
            if alpha >= beta:
                break

        if not self._stopped and best_move is not None:
            self._best_move_root = best_move
            flag = TT_EXACT
            if best_score <= original_alpha:
                flag = TT_UPPER
            elif best_score >= beta:
                flag = TT_LOWER
            self._tt.put(state.board.turn_hash(state.turn), depth,
                         _tt_score_to_store(best_score, 0), flag, best_move)
        return best_score

    def _negamax(self, state: GameState, depth: int, alpha: int, beta: int,
                 ply: int, prev_move: Move | None, allow_null: bool,
                 excluded: Move | None = None) -> int:
        self._nodes += 1
        if ply > self._seldepth:
            self._seldepth = ply
        if self._nodes & 2047 == 0 and self._time_expired():
            self._stopped = True
            return 0
        if self._stopped:
            return 0

        # Repetition / 50-move draw.
        if ply > 0 and self._is_search_draw(state):
            return 0

        # Mate distance pruning
        alpha = max(alpha, _mated_in(ply))
        beta = min(beta, _mate_in(ply + 1))
        if alpha >= beta:
            return alpha

        is_pv = (beta - alpha) > 1

        tt_key = state.board.turn_hash(state.turn)
        tt_entry = self._tt.get(tt_key)
        tt_best = None
        if tt_entry is not None:
            tt_best = tt_entry.best_move
            # No TT cutoffs in a singular exclusion search: the stored score
            # includes the excluded move, which this search must ignore.
            if excluded is None and tt_entry.depth >= depth and not is_pv:
                tt_score = _tt_score_from_probe(tt_entry.score, ply)
                if tt_entry.flag == TT_EXACT:
                    return tt_score
                if tt_entry.flag == TT_LOWER and tt_score >= beta:
                    return tt_score
                if tt_entry.flag == TT_UPPER and tt_score <= alpha:
                    return tt_score

        # Terminal: explicit winner via game rules.
        if state.result is not None:
            winner = state.result.winner
            if winner == state.turn:
                return _mate_in(ply)
            return _mated_in(ply)

        # ---- Internal iterative reduction (use_iir) ----
        # A deep node with no TT move searches blind; shave a ply and let the
        # re-visit (with a TT move) do the full-depth work efficiently.
        if (self.cfg.use_iir and tt_best is None
                and depth >= self.cfg.iir_min_depth):
            depth -= 1

        if depth <= 0:
            return self._quiesce(state, alpha, beta, qply=0, ply=ply)

        moves = state.legal_moves()
        if not moves:
            return _mated_in(ply)

        # Static eval, computed once for the shallow-depth pruning heuristics.
        # Only meaningful at non-PV nodes outside a mate window.
        static_eval: int | None = None
        improving = True
        not_mate_window = abs(beta) < _MATE_BOUND and abs(alpha) < _MATE_BOUND
        if not is_pv and not_mate_window:
            # Moves are already generated and non-empty here, so the terminal
            # checks inside evaluate() can never fire — skip them when the
            # fast path is enabled. A static eval cached in the TT is reused
            # outright (identical value, so search decisions are unchanged).
            if (self.cfg.use_tt_static_eval and tt_entry is not None
                    and tt_entry.static_eval is not None):
                static_eval = tt_entry.static_eval
            elif self.cfg.use_fast_movegen:
                static_eval = evaluate_nonterminal(state, state.turn, self.cfg)
            else:
                static_eval = evaluate(state, state.turn, self.cfg)

            # ---- Improving heuristic: static-eval trend vs two plies up ----
            if ply < _MAX_PLY:
                self._static_stack[ply] = static_eval
            if self.cfg.use_improving and ply >= 2:
                prior = self._static_stack[ply - 2]
                if prior is not None:
                    improving = static_eval > prior

            # ---- Reverse futility pruning (a.k.a. static null move) ----
            if self.cfg.use_rfp and depth <= self.cfg.rfp_max_depth:
                threshold = self.cfg.rfp_margin * depth
                if self.cfg.use_improving and improving:
                    # An improving position fails high more readily.
                    threshold -= self.cfg.rfp_margin // 2
                if static_eval - threshold >= beta:
                    return static_eval

            # ---- Razoring: far below alpha at shallow depth → verify with qsearch ----
            if (self.cfg.use_razoring and depth <= self.cfg.razor_max_depth
                    and static_eval + self.cfg.razor_margin < alpha):
                q = self._quiesce(state, alpha, beta, qply=0, ply=ply)
                if self._stopped:
                    return 0
                if q < alpha:
                    return q
        elif ply < _MAX_PLY:
            self._static_stack[ply] = None

        # ---- Null move pruning ----
        if (allow_null and not is_pv and excluded is None
                and depth >= NMP_MIN_DEPTH
                and state.board.alive_count(state.turn) >= NMP_MIN_PIECES):
            if static_eval is None:
                if (self.cfg.use_tt_static_eval and tt_entry is not None
                        and tt_entry.static_eval is not None):
                    static_eval = tt_entry.static_eval
                elif self.cfg.use_fast_movegen:
                    static_eval = evaluate_nonterminal(state, state.turn, self.cfg)
                else:
                    static_eval = evaluate(state, state.turn, self.cfg)
            if static_eval >= beta:
                if self.cfg.use_nmp_dynamic:
                    # Reduce more when deep and when far above beta.
                    surplus = (static_eval - beta) // 200
                    r = NMP_REDUCTION + depth // 6 + (surplus if surplus < 2 else 2)
                    r = min(r, depth - 1)
                else:
                    r = NMP_REDUCTION
                state.apply_null()
                prev_floor = self._null_floor
                self._null_floor = len(state._hash_history)
                null_score = -self._negamax(state, depth - 1 - r,
                                            -beta, -beta + 1, ply + 1,
                                            prev_move=None, allow_null=False)
                self._null_floor = prev_floor
                state.undo_null()
                if self._stopped:
                    return 0
                if null_score >= beta:
                    # Don't return mate scores from null search.
                    if null_score >= _MATE_BOUND:
                        null_score = beta
                    return null_score

        # ---- ProbCut (use_probcut) ----
        # If a winning/equal capture verified at reduced depth already lands a
        # margin above beta, this node will almost surely fail high — cut now.
        if (self.cfg.use_probcut and not is_pv and excluded is None
                and depth >= self.cfg.probcut_min_depth and not_mate_window
                and static_eval is not None):
            pc_beta = beta + self.cfg.probcut_margin
            # Skip when the TT already proves this node can't reach pc_beta
            # at comparable depth.
            tt_blocks = (tt_entry is not None and tt_entry.depth >= depth - 3
                         and tt_entry.flag != TT_LOWER
                         and _tt_score_from_probe(tt_entry.score, ply) < pc_beta)
            if not tt_blocks:
                caps = sorted((m for m in moves if m.captured),
                              key=lambda m: -abs(m.captured))
                tried = 0
                for move in caps:
                    if tried >= self.cfg.probcut_max_moves:
                        break
                    if see_capture(state.board, move) < 0:
                        continue
                    tried += 1
                    state.apply_move(move)
                    score = -self._negamax(state, depth - 4, -pc_beta,
                                           -pc_beta + 1, ply + 1,
                                           prev_move=move, allow_null=True)
                    state.undo_move()
                    if self._stopped:
                        return 0
                    if score >= pc_beta:
                        self._tt.put(tt_key, depth - 3,
                                     _tt_score_to_store(score, ply),
                                     TT_LOWER, move, static_eval)
                        return score

        moves = self._order_moves(moves, tt_best, ply, prev_move, board=state.board)

        # ---- Singular extension (use_singular) ----
        # If every alternative to a deep TT move fails a margin below its
        # score, the TT move is forced — search it one ply deeper.
        extension = 0
        if (self.cfg.use_singular and excluded is None and ply > 0
                and depth >= self.cfg.singular_min_depth
                and tt_best is not None and tt_entry is not None
                and tt_entry.flag != TT_UPPER
                and tt_entry.depth >= depth - 3
                and abs(tt_entry.score) < _MATE_BOUND
                and len(moves) >= 2):
            tt_score = _tt_score_from_probe(tt_entry.score, ply)
            s_beta = tt_score - self.cfg.singular_margin * depth
            s_score = self._negamax(state, (depth - 1) // 2, s_beta - 1, s_beta,
                                    ply, prev_move, allow_null=False,
                                    excluded=tt_best)
            if self._stopped:
                return 0
            if s_score < s_beta:
                extension = 1
            elif s_beta >= beta:
                # Multi-cut: even without the TT move the node beats beta.
                return s_beta

        # Opponent den square: a quiet move that enters it is a *winning* move and
        # must never be futility/late-move pruned.
        opp_den = DEN_BLACK if state.turn == Color.BLUE else DEN_BLUE

        best_score = -_INF
        best_move: Move | None = None
        original_alpha = alpha

        for idx, move in enumerate(moves):
            if excluded is not None and move == excluded:
                continue
            is_quiet = not move.captured and (move.tc, move.tr) != opp_den
            ext = extension if (extension and move == tt_best) else 0

            # ---- Forward pruning of late / hopeless quiet moves (non-PV only) ----
            if is_quiet and best_move is not None and not is_pv and not_mate_window:
                # Late move pruning (move-count based); a worsening position
                # earns half the move budget.
                if self.cfg.use_lmp:
                    limit = self.cfg.lmp_base + depth * depth
                    if self.cfg.use_improving and not improving:
                        limit = limit // 2 + 1
                    if idx >= limit:
                        continue
                # Futility pruning near the frontier (tighter when worsening).
                if (self.cfg.use_futility and static_eval is not None
                        and depth <= self.cfg.futility_max_depth):
                    margin = self.cfg.futility_margin
                    if self.cfg.use_improving and not improving:
                        margin -= 50
                    if static_eval + margin <= alpha:
                        continue

            # ---- SEE pruning of losing captures at shallow depth ----
            if (move.captured and self.cfg.use_see_prune
                    and best_move is not None and not is_pv and not_mate_window
                    and depth <= self.cfg.see_prune_max_depth
                    and see_capture(state.board, move)
                        < -self.cfg.see_prune_margin * depth):
                continue

            state.apply_move(move)

            # ---- Late Move Reductions ----
            do_full_search = True
            score = 0
            if (idx >= LMR_MOVES_BEFORE and depth >= LMR_MIN_DEPTH
                    and not move.captured
                    and (ply >= _MAX_PLY or move != self._killers[ply][0])
                    and (ply >= _MAX_PLY or move != self._killers[ply][1])):
                if self.cfg.use_lmr_matrix:
                    r = _LMR_TABLE[depth if depth < 32 else 31][idx if idx < 64 else 63]
                    if (self._history.get((move.fc, move.fr, move.tc, move.tr), 0)
                            >= _LMR_HISTORY_THRESHOLD):
                        r -= 1   # well-proven quiet move: reduce less
                    if is_pv:
                        r -= 1
                else:
                    r = 1 + (depth // 6) + (idx // 6)
                r = min(r, depth - 2)
                if r > 0:
                    score = -self._negamax(state, depth - 1 - r, -alpha - 1, -alpha,
                                            ply + 1, prev_move=move, allow_null=True)
                    if self._stopped:
                        state.undo_move()
                        return 0
                    do_full_search = score > alpha

            if do_full_search:
                if idx == 0:
                    score = -self._negamax(state, depth - 1 + ext, -beta, -alpha,
                                            ply + 1, prev_move=move, allow_null=True)
                else:
                    score = -self._negamax(state, depth - 1 + ext, -alpha - 1, -alpha,
                                            ply + 1, prev_move=move, allow_null=True)
                    if not self._stopped and alpha < score < beta:
                        score = -self._negamax(state, depth - 1 + ext, -beta, -alpha,
                                                ply + 1, prev_move=move, allow_null=True)

            state.undo_move()
            if self._stopped:
                return 0
            if score > best_score:
                best_score = score
                best_move = move
            if score > alpha:
                alpha = score
            if alpha >= beta:
                # Beta cutoff: capture history for captures.
                if move.captured and self.cfg.use_capture_history:
                    ck = (abs(state.board.get(move.fc, move.fr)),
                          move.tc, move.tr, abs(move.captured))
                    self._capt_hist[ck] = (self._capt_hist.get(ck, 0)
                                           + depth * depth)
                # Beta cutoff: record killer + counter + history for non-captures
                if not move.captured and 0 <= ply < _MAX_PLY:
                    slots = self._killers[ply]
                    if slots[0] != move:
                        slots[1] = slots[0]
                        slots[0] = move
                    self._history[(move.fc, move.fr, move.tc, move.tr)] = (
                        self._history.get((move.fc, move.fr, move.tc, move.tr), 0)
                        + depth * depth
                    )
                    if prev_move is not None:
                        self._counter[(prev_move.fc, prev_move.fr,
                                       prev_move.tc, prev_move.tr)] = move
                        if self.cfg.use_cont_history:
                            ck = (prev_move.tc, prev_move.tr,
                                  move.tc, move.tr)
                            self._cont_hist[ck] = (
                                self._cont_hist.get(ck, 0) + depth * depth
                            )
                break

        if best_move is None and excluded is not None:
            # Every non-excluded move was skipped/absent: fail low, so the
            # exclusion search reports the TT move as singular.
            return alpha

        if best_move is not None and excluded is None:
            flag = TT_EXACT
            if best_score <= original_alpha:
                flag = TT_UPPER
            elif best_score >= beta:
                flag = TT_LOWER
            self._tt.put(tt_key, depth, _tt_score_to_store(best_score, ply),
                         flag, best_move, static_eval)

        return best_score

    # ------------------------------------------------------------------
    # Quiescence search
    # ------------------------------------------------------------------

    def _quiesce(self, state: GameState, alpha: int, beta: int,
                 qply: int, ply: int) -> int:
        self._nodes += 1
        if self._nodes & 2047 == 0 and self._time_expired():
            self._stopped = True
            return 0

        # Terminal check (winner already declared).
        if state.result is not None:
            winner = state.result.winner
            if winner == state.turn:
                return _mate_in(ply + qply)
            return _mated_in(ply + qply)

        # TT probe (depth-0 entries usable as bounds).
        tt_key = state.board.turn_hash(state.turn)
        tt_entry = self._tt.get(tt_key)
        if tt_entry is not None and tt_entry.depth >= 0:
            tt_score = _tt_score_from_probe(tt_entry.score, ply + qply)
            if tt_entry.flag == TT_EXACT:
                return tt_score
            if tt_entry.flag == TT_LOWER and tt_score >= beta:
                return tt_score
            if tt_entry.flag == TT_UPPER and tt_score <= alpha:
                return tt_score

        # Stand-pat: the fast path skips the stalemate detection inside
        # evaluate() (state.result was checked above; a full legal-move
        # generation per leaf just to catch the rare no-move stalemate would
        # double the quiescence cost). A TT-cached static eval is identical
        # by construction, so it is reused outright.
        if (self.cfg.use_tt_static_eval and tt_entry is not None
                and tt_entry.static_eval is not None):
            stand_pat = tt_entry.static_eval
        elif self.cfg.use_fast_movegen:
            stand_pat = evaluate_nonterminal(state, state.turn, self.cfg)
        else:
            stand_pat = evaluate(state, state.turn, self.cfg)
        if qply >= QUIESCENCE_MAX_PLY:
            return stand_pat
        if stand_pat >= beta:
            return beta
        if alpha < stand_pat:
            alpha = stand_pat

        delta_margin = EVAL_WEIGHTS["delta_margin"]
        board = state.board
        opp_den = DEN_BLACK if state.turn == Color.BLUE else DEN_BLUE
        if self.cfg.use_fast_movegen:
            moves = generate_noisy_only(board, state.turn)
            if not self.cfg.use_noisy_den_quiescence:
                moves = [m for m in moves if m.captured]
        elif self.cfg.use_noisy_den_quiescence:
            moves = generate_noisy_moves(board, state.turn)
        else:
            moves = generate_capture_moves(board, state.turn)
        # MVV-LVA ordering (least-valuable attacker breaks victim ties), then SEE
        # filter. Den-entry moves (winning) sort first; the TT best move (if
        # enabled) in front of everything.
        if self.cfg.use_mvv_lva_fix:
            qs_tt = tt_entry.best_move if (self.cfg.use_qsearch_tt_move
                                           and tt_entry is not None) else None
            moves.sort(key=lambda m: (
                -2_000_000 if m == qs_tt
                else -1_000_000 if (m.tc, m.tr) == opp_den
                else -abs(m.captured) * 16 + abs(board.get(m.fc, m.fr))))
        else:
            moves.sort(key=lambda m: -abs(m.captured))

        for move in moves:
            # Entering the enemy den wins immediately — the fastest mate from here.
            if (move.tc, move.tr) == opp_den:
                return _mate_in(ply + qply)
            # Delta pruning: even capturing this victim won't reach alpha.
            if stand_pat + abs(move.captured) + delta_margin < alpha:
                continue
            # SEE prune: skip clearly losing captures.
            if see_capture(board, move) < 0:
                continue

            state.apply_move(move)
            score = -self._quiesce(state, -beta, -alpha, qply + 1, ply)
            state.undo_move()
            if self._stopped:
                return 0
            if score >= beta:
                return beta
            if score > alpha:
                alpha = score

        return alpha
