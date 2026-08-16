//! Negamax with principal variation search.
//!
//! Feature-for-feature the Python engine's search — aspiration windows, PVS,
//! null-move pruning, reverse futility, razoring, futility, late-move pruning and
//! reductions, killers / counter-moves / history, a transposition table, and a
//! quiescence search with SEE and delta pruning — with its known defects fixed
//! rather than carried across. Those are called out at the point where each
//! decision is made.
//!
//! Jungle has no check, so the tactical analogue of a mate threat is **den
//! entry**: a move onto the enemy den wins immediately and unconditionally. That
//! shapes the search in three places — such a move is ordered first, it is never
//! reduced or pruned, and reaching one ends the node at once.

use std::sync::atomic::{AtomicBool, Ordering as AtomicOrdering};
use std::sync::Arc;
use std::time::Duration;

use crate::clock::now_ms;

use jungle_core::bitboard::enemy_den;
use jungle_core::position::Position;
use jungle_core::types::{Move, MoveList};
use jungle_core::generate_into;
use jungle_eval::evaluate_with;
use jungle_eval::params::EvalParams;

use crate::ordering::{Heuristics, OrderedMoves};
use crate::params::SearchParams;
use crate::score::*;
use crate::tt::{TranspositionTable, BOUND_EXACT, BOUND_LOWER, BOUND_UPPER};

/// How often to look at the clock. Checking every node costs more than it saves.
const CLOCK_INTERVAL: u64 = 2047;

/// History score that earns one less late-move reduction.
const LMR_HISTORY_THRESHOLD: i32 = 512;

const LMR_MAX_DEPTH: usize = 32;
const LMR_MAX_MOVES: usize = 64;

/// `int(0.5 + ln(depth) * ln(index) / 2)`, the reduction matrix the Python engine
/// adopted in v1.5. Smoother than the integer-step `1 + depth/6 + idx/6` it
/// replaces: that one steps a whole ply at a time, so it under-reduces the early
/// late moves and over-reduces the deep ones.
///
/// Built once on first use rather than as a `const fn`, because `ln` is not
/// available in const context.
fn lmr_table() -> &'static [[i8; LMR_MAX_MOVES]; LMR_MAX_DEPTH] {
    use std::sync::OnceLock;
    static TABLE: OnceLock<[[i8; LMR_MAX_MOVES]; LMR_MAX_DEPTH]> = OnceLock::new();
    TABLE.get_or_init(|| {
        let mut t = [[0i8; LMR_MAX_MOVES]; LMR_MAX_DEPTH];
        for (d, row) in t.iter_mut().enumerate().skip(1) {
            for (i, cell) in row.iter_mut().enumerate().skip(1) {
                *cell = (0.5 + (d as f64).ln() * (i as f64).ln() / 2.0) as i8;
            }
        }
        t
    })
}

#[derive(Clone, Debug, Default)]
pub struct Limits {
    pub depth: Option<i32>,
    pub nodes: Option<u64>,
    pub movetime: Option<Duration>,
}

impl Limits {
    pub fn depth(d: i32) -> Limits {
        Limits {
            depth: Some(d),
            ..Default::default()
        }
    }
    /// A fixed node budget: deterministic, so an A/B match is reproducible and
    /// free of timing noise. This is the limit tuning matches should use.
    pub fn nodes(n: u64) -> Limits {
        Limits {
            nodes: Some(n),
            ..Default::default()
        }
    }
    pub fn movetime(ms: u64) -> Limits {
        Limits {
            movetime: Some(Duration::from_millis(ms)),
            ..Default::default()
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct SearchResult {
    pub best_move: Option<Move>,
    pub score: i32,
    pub depth: i32,
    pub seldepth: usize,
    pub nodes: u64,
    pub elapsed: Duration,
}

pub struct Searcher {
    tt: TranspositionTable,
    heur: Heuristics,
    params: SearchParams,
    eval_params: EvalParams,
    nodes: u64,
    seldepth: usize,
    stopped: bool,
    stop_flag: Arc<AtomicBool>,
    /// Milliseconds, from `clock::now_ms`, not an `Instant`: see `src/clock.rs`.
    start_ms: f64,
    deadline_ms: Option<f64>,
    node_limit: Option<u64>,
    root_best: Option<Move>,
    root_moves_done: usize,
    /// Static evaluation per ply, for the improving heuristic. `EVAL_NONE` where
    /// the node never computed one.
    static_stack: [i32; MAX_PLY],
}

impl Searcher {
    pub fn new(tt_megabytes: usize) -> Searcher {
        Searcher::with_params(tt_megabytes, SearchParams::default())
    }

    /// A searcher with non-default tuning. Used by the A/B harness so two
    /// configurations can play each other in one process.
    pub fn with_params(tt_megabytes: usize, params: SearchParams) -> Searcher {
        Searcher::with_all_params(tt_megabytes, params, EvalParams::default())
    }

    /// A searcher with non-default search *and* evaluation tuning.
    ///
    /// One `EvalParams` for the whole searcher, shared by the evaluation, move
    /// ordering, static exchange evaluation and quiescence delta pruning. They
    /// must agree about what a piece is worth; giving each its own copy is how
    /// they quietly stop agreeing.
    pub fn with_all_params(
        tt_megabytes: usize,
        params: SearchParams,
        eval_params: EvalParams,
    ) -> Searcher {
        Searcher {
            tt: TranspositionTable::new(tt_megabytes),
            heur: Heuristics::new(),
            params,
            eval_params,
            nodes: 0,
            seldepth: 0,
            stopped: false,
            stop_flag: Arc::new(AtomicBool::new(false)),
            start_ms: now_ms(),
            deadline_ms: None,
            node_limit: None,
            root_best: None,
            root_moves_done: 0,
            static_stack: [EVAL_NONE; MAX_PLY],
        }
    }

    /// A handle another thread can set to abort the search promptly.
    pub fn stop_handle(&self) -> Arc<AtomicBool> {
        Arc::clone(&self.stop_flag)
    }

    /// Forget everything learned. Between unrelated positions only: the table and
    /// the history are worth carrying across the moves of one game.
    pub fn reset(&mut self) {
        self.tt.clear();
        self.heur.clear();
    }

    pub fn nodes(&self) -> u64 {
        self.nodes
    }

    pub fn hashfull(&self) -> usize {
        self.tt.hashfull()
    }

    /// Wall time since `think()` started, for `SearchResult`.
    fn elapsed(&self) -> Duration {
        Duration::from_secs_f64(((now_ms() - self.start_ms) / 1000.0).max(0.0))
    }

    #[inline(always)]
    fn out_of_time(&self) -> bool {
        if self.stop_flag.load(AtomicOrdering::Relaxed) {
            return true;
        }
        if let Some(limit) = self.node_limit {
            if self.nodes >= limit {
                return true;
            }
        }
        match self.deadline_ms {
            Some(d) => now_ms() >= d,
            None => false,
        }
    }

    #[inline(always)]
    fn check_clock(&mut self) -> bool {
        if self.nodes & CLOCK_INTERVAL == 0 && self.out_of_time() {
            self.stopped = true;
        }
        self.stopped
    }

    // -----------------------------------------------------------------
    // Iterative deepening
    // -----------------------------------------------------------------

    pub fn think(&mut self, pos: &mut Position, limits: &Limits) -> SearchResult {
        self.nodes = 0;
        self.seldepth = 0;
        self.stopped = false;
        self.root_best = None;
        self.stop_flag.store(false, AtomicOrdering::Relaxed);
        // Stale evaluations from the previous search would make `improving`
        // compare this position against an unrelated one.
        self.static_stack = [EVAL_NONE; MAX_PLY];
        self.start_ms = now_ms();
        self.deadline_ms = limits
            .movetime
            .map(|d| self.start_ms + d.as_secs_f64() * 1000.0);
        self.node_limit = limits.nodes;
        self.tt.new_generation();

        let max_depth = limits.depth.unwrap_or(MAX_PLY as i32 - 2);

        let mut root = MoveList::new();
        generate_into(pos, &mut root);
        if root.is_empty() {
            return SearchResult {
                best_move: None,
                score: mated_in(0),
                elapsed: self.elapsed(),
                ..Default::default()
            };
        }
        // One legal move: play it. Searching would only tell us what we already
        // have to do, and the clock is better spent on the next position.
        if root.len() == 1 {
            return SearchResult {
                best_move: Some(root[0]),
                score: 0,
                depth: 1,
                seldepth: 1,
                nodes: 1,
                elapsed: self.elapsed(),
            };
        }

        let mut best = root[0];
        let mut score = 0;
        let mut completed = 0;
        let mut previous: Option<i32> = None;
        let mut last_iteration_ms = 0.0f64;

        for depth in 1..=max_depth {
            // Don't start an iteration we can predict will not finish; the
            // partial result is usually worth less than the time it costs.
            if depth > 1 {
                if let Some(d) = self.deadline_ms {
                    if now_ms() + last_iteration_ms * 1.5 > d {
                        break;
                    }
                }
            }

            let iteration_start_ms = now_ms();
            let iteration_score = self.aspiration(pos, depth, previous);

            if self.stopped {
                // Keep a better move found before the interruption: the moves are
                // ordered best-first, so a partial iteration has still searched
                // the most promising ones.
                if self.root_moves_done >= 1 {
                    if let Some(m) = self.root_best {
                        best = m;
                        score = iteration_score;
                        completed = depth;
                    }
                }
                break;
            }

            if let Some(m) = self.root_best {
                best = m;
            }
            score = iteration_score;
            previous = Some(iteration_score);
            completed = depth;
            last_iteration_ms = now_ms() - iteration_start_ms;
            self.heur.age();

            // A forced mate is found; searching deeper cannot improve on it.
            if is_mate_score(iteration_score) {
                break;
            }
        }

        SearchResult {
            best_move: Some(best),
            score,
            depth: completed,
            seldepth: self.seldepth,
            nodes: self.nodes,
            elapsed: self.elapsed(),
        }
    }

    /// Search one depth, narrowing the window around the previous score and
    /// widening on a fail.
    fn aspiration(&mut self, pos: &mut Position, depth: i32, previous: Option<i32>) -> i32 {
        let Some(prev) = previous.filter(|_| depth >= self.params.aspiration_min_depth) else {
            return self.search_root(pos, depth, -INF, INF);
        };

        let mut delta = self.params.aspiration_delta;
        let mut alpha = (prev - delta).max(-INF);
        let mut beta = (prev + delta).min(INF);

        loop {
            let score = self.search_root(pos, depth, alpha, beta);
            if self.stopped {
                return score;
            }
            if score <= alpha {
                // Fail low: widen downward but keep beta, so the re-search stays
                // narrow on the side that is not in doubt.
                beta = (alpha + beta) / 2;
                alpha = (score - delta).max(-INF);
            } else if score >= beta {
                beta = (score + delta).min(INF);
            } else {
                return score;
            }
            delta += delta / 2;
            if delta > 32 * self.params.aspiration_delta {
                return self.search_root(pos, depth, -INF, INF);
            }
        }
    }

    fn search_root(&mut self, pos: &mut Position, depth: i32, mut alpha: i32, beta: i32) -> i32 {
        let key = pos.key();
        let tt_move = self.tt.probe(key, 0).and_then(|h| h.mv);

        let mut moves = MoveList::new();
        generate_into(pos, &mut moves);
        let mut ordered = OrderedMoves::new(
            pos,
            moves,
            tt_move,
            &self.heur,
            0,
            None,
            self.params.use_capture_history,
            // No previous move at the root, so continuation history has nothing
            // to key on.
            false,
            &self.eval_params,
        );

        let original_alpha = alpha;
        let mut best_score = -INF;
        let mut best_move = None;
        let mut idx = 0usize;
        self.root_moves_done = 0;

        while let Some(m) = ordered.next_move() {
            pos.make(m);
            let score = if idx == 0 {
                -self.negamax(pos, depth - 1, -beta, -alpha, 1, Some(m), true)
            } else {
                let s = -self.negamax(pos, depth - 1, -alpha - 1, -alpha, 1, Some(m), true);
                if !self.stopped && s > alpha && s < beta {
                    -self.negamax(pos, depth - 1, -beta, -alpha, 1, Some(m), true)
                } else {
                    s
                }
            };
            pos.unmake();

            if self.stopped {
                break;
            }
            self.root_moves_done += 1;

            if score > best_score {
                best_score = score;
                best_move = Some(m);
                // Commit mid-iteration so an interrupted search still improves on
                // the previous depth rather than discarding its work.
                if score > original_alpha {
                    self.root_best = Some(m);
                }
            }
            if score > alpha {
                alpha = score;
            }
            if alpha >= beta {
                break;
            }
            idx += 1;
        }

        if let Some(m) = best_move {
            let bound = if best_score <= original_alpha {
                BOUND_UPPER
            } else if best_score >= beta {
                BOUND_LOWER
            } else {
                BOUND_EXACT
            };
            self.tt
                .store(key, Some(m), best_score, 0, depth as i8, bound, 0);
        }
        best_score
    }

    // -----------------------------------------------------------------
    // Main search
    // -----------------------------------------------------------------

    #[allow(clippy::too_many_arguments)]
    fn negamax(
        &mut self,
        pos: &mut Position,
        mut depth: i32,
        mut alpha: i32,
        mut beta: i32,
        ply: i32,
        prev: Option<Move>,
        allow_null: bool,
    ) -> i32 {
        self.nodes += 1;
        self.seldepth = self.seldepth.max(ply as usize);
        if self.check_clock() {
            return 0;
        }

        // Draws. Repetition is a search-side draw score, not a game rule.
        if ply > 0 && (pos.is_repetition() || pos.is_fifty_move_draw()) {
            return 0;
        }

        // Mate-distance pruning: if we already have a mate at least this fast,
        // nothing deeper here can matter.
        alpha = alpha.max(mated_in(ply));
        beta = beta.min(mate_in(ply + 1));
        if alpha >= beta {
            return alpha;
        }

        let is_pv = beta - alpha > 1;
        let key = pos.key();

        let hit = self.tt.probe(key, ply);
        let mut tt_move = None;
        let mut tt_eval = EVAL_NONE;
        if let Some(ref h) = hit {
            tt_move = h.mv;
            tt_eval = h.eval;
            if h.depth as i32 >= depth && !is_pv {
                let usable = match h.bound {
                    BOUND_EXACT => true,
                    BOUND_LOWER => h.score >= beta,
                    BOUND_UPPER => h.score <= alpha,
                    _ => false,
                };
                if usable {
                    return h.score;
                }
            }
        }

        // Already decided by the rules.
        if let Some(winner) = pos.result() {
            return if winner == pos.side_to_move() {
                mate_in(ply)
            } else {
                mated_in(ply)
            };
        }

        // Internal iterative reduction. A node deep enough to matter that has
        // never been stored is one the search has not found interesting yet;
        // spend a ply less on it and let the next iteration, which will have a
        // move to try first, spend the full amount.
        if self.params.use_iir && depth >= self.params.iir_min_depth && tt_move.is_none() {
            depth -= 1;
        }

        if depth <= 0 {
            return self.quiesce(pos, alpha, beta, ply, 0);
        }

        let mut moves = MoveList::new();
        generate_into(pos, &mut moves);
        if moves.is_empty() {
            // No legal move is a loss for the side to move, not a draw.
            return mated_in(ply);
        }

        let side = pos.side_to_move();
        let den = enemy_den(side.index());

        let outside_mate_window = alpha.abs() < MATE_BOUND && beta.abs() < MATE_BOUND;
        let mut static_eval = None;
        let mut improving = false;

        // Mark this ply unknown up front. Principal-variation nodes and nodes
        // inside the mate window never compute a static evaluation, and leaving
        // the slot holding whatever an unrelated earlier line put there would
        // have a node two plies below compare itself against a different game.
        if (ply as usize) < MAX_PLY {
            self.static_stack[ply as usize] = EVAL_NONE;
        }

        if !is_pv && outside_mate_window {
            // The table already stores a static evaluation for this key; taking
            // it saves a full pass over the board at every node that has been
            // here before. The slot was written and never read until now.
            let eval = if self.params.use_tt_eval && tt_eval != EVAL_NONE {
                tt_eval
            } else {
                evaluate_with(pos, side, &self.eval_params)
            };
            static_eval = Some(eval);
            let p = self.params;

            // Improving: is this position better than it was on our previous
            // turn? A position going the wrong way earns less benefit of the
            // doubt from every forward-pruning rule below.
            if (ply as usize) < MAX_PLY {
                self.static_stack[ply as usize] = eval;
            }
            if p.use_improving && ply >= 2 {
                let prior = self.static_stack[(ply - 2) as usize];
                improving = prior != EVAL_NONE && eval > prior;
            }

            // Reverse futility: so far ahead that giving up a few plies still
            // beats beta.
            let rfp_threshold = if p.use_improving && improving {
                // An improving position fails high more readily.
                p.rfp_margin * depth - p.rfp_margin / 2
            } else {
                p.rfp_margin * depth
            };
            if p.use_rfp && depth <= p.rfp_max_depth && eval - rfp_threshold >= beta {
                return eval;
            }

            // Razoring: so far behind that only a tactic saves us; let quiescence
            // decide whether one exists.
            if p.use_razoring && depth <= p.razor_max_depth && eval + p.razor_margin < alpha {
                let q = self.quiesce(pos, alpha, beta, ply, 0);
                if self.stopped {
                    return 0;
                }
                if q < alpha {
                    return q;
                }
            }

            // Null-move pruning. Withheld when the side to move is down to a
            // couple of pieces, where zugzwang-like positions make passing a
            // materially different proposition.
            if p.use_nmp
                && allow_null
                && depth >= p.nmp_min_depth
                && pos.alive_count(side) >= p.nmp_min_pieces
                && eval >= beta
            {
                pos.make_null();
                let null_score =
                    -self.negamax(pos, depth - 1 - p.nmp_reduction, -beta, -beta + 1, ply + 1, None, false);
                pos.unmake_null();
                if self.stopped {
                    return 0;
                }
                if null_score >= beta {
                    // A mate score proved by *not moving* is not a real mate.
                    return if null_score >= MATE_BOUND { beta } else { null_score };
                }
            }
        }

        let mut ordered = OrderedMoves::new(
            pos,
            moves,
            tt_move,
            &self.heur,
            ply as usize,
            prev,
            self.params.use_capture_history,
            self.params.use_cont_history,
            &self.eval_params,
        );
        let original_alpha = alpha;
        let mut best_score = -INF;
        let mut best_move = None;
        let mut idx = 0usize;
        let mut quiets_tried = 0usize;
        let mut tried: [Move; 32] = [Move(0); 32];

        while let Some(m) = ordered.next_move() {
            let is_capture = pos.piece_at(m.to()).is_some();
            let enters_den = m.to() == den;

            // Entering the enemy den wins on the spot. Ordering puts it first, so
            // this ends the node immediately -- with the score the search would
            // have produced had it played the move and looked.
            if enters_den {
                best_score = mate_in(ply + 1);
                best_move = Some(m);
                break;
            }

            let is_quiet = !is_capture;

            // Forward pruning of late or hopeless quiet moves, once we already
            // have something to fall back on.
            if is_quiet && best_move.is_some() && !is_pv && outside_mate_window {
                let p = self.params;
                // A worsening position earns half the move budget.
                let mut lmp_limit = p.lmp_base + (depth * depth) as usize;
                if p.use_improving && !improving {
                    lmp_limit = lmp_limit / 2 + 1;
                }
                if p.use_lmp && quiets_tried >= lmp_limit {
                    idx += 1;
                    continue;
                }
                if let Some(eval) = static_eval {
                    // ...and a tighter futility margin.
                    let margin = if p.use_improving && !improving {
                        p.futility_margin - 50
                    } else {
                        p.futility_margin
                    };
                    if p.use_futility && depth <= p.futility_max_depth && eval + margin <= alpha {
                        idx += 1;
                        continue;
                    }
                }
            }

            // Losing captures at shallow depth, once there is a fallback move.
            // Quiescence already declines these; doing it here as well stops the
            // main search paying full depth to reach the same conclusion.
            if is_capture
                && best_move.is_some()
                && !is_pv
                && outside_mate_window
                && self.params.use_see_prune
                && depth <= self.params.see_prune_max_depth
                && crate::see::see_with(pos, m, &self.eval_params) < -self.params.see_prune_margin * depth
            {
                idx += 1;
                continue;
            }

            pos.make(m);

            // Late-move reductions. Never inside the principal variation, and
            // never for a capture -- reducing the moves that change material is
            // how a search talks itself out of a tactic.
            // A killer is a quiet move already known to refute something at this
            // ply, so reducing it would search the one quiet move with evidence
            // behind it *less* deeply than the ones without. Measured: leaving
            // this out cost about 56 Elo at fixed depth 5 against the engine this
            // one replaces, while looking like a harmless simplification.
            let reducible = is_quiet
                && (self.params.lmr_reduce_killers || !self.heur.is_killer(ply as usize, m));
            if self.params.use_lmr
                && !is_pv
                && idx >= self.params.lmr_moves_before
                && depth >= self.params.lmr_min_depth
                && reducible
            {
                let mut r = if self.params.use_lmr_log {
                    let d = (depth as usize).min(LMR_MAX_DEPTH - 1);
                    let i = idx.min(LMR_MAX_MOVES - 1);
                    let mut r = lmr_table()[d][i] as i32;
                    // A quiet move with a history of causing cutoffs has earned
                    // one ply back.
                    if self.heur.history_of(side, m) >= LMR_HISTORY_THRESHOLD {
                        r -= 1;
                    }
                    r
                } else {
                    1 + (depth / 6) + (idx as i32 / 6)
                };
                r = r.min(depth - 2);
                if r > 0 {
                    let reduced =
                        -self.negamax(pos, depth - 1 - r, -alpha - 1, -alpha, ply + 1, Some(m), true);
                    if self.stopped {
                        pos.unmake();
                        return 0;
                    }
                    if reduced <= alpha {
                        // The reduced search agrees this move is not worth more;
                        // take its word rather than paying for a full one.
                        pos.unmake();
                        if reduced > best_score {
                            best_score = reduced;
                            best_move = Some(m);
                        }
                        if quiets_tried < tried.len() {
                            tried[quiets_tried] = m;
                        }
                        quiets_tried += 1;
                        idx += 1;
                        continue;
                    }
                }
            }

            let score = if idx == 0 {
                -self.negamax(pos, depth - 1, -beta, -alpha, ply + 1, Some(m), true)
            } else {
                let s = -self.negamax(pos, depth - 1, -alpha - 1, -alpha, ply + 1, Some(m), true);
                if !self.stopped && s > alpha && s < beta {
                    -self.negamax(pos, depth - 1, -beta, -alpha, ply + 1, Some(m), true)
                } else {
                    s
                }
            };

            pos.unmake();
            if self.stopped {
                return 0;
            }

            if score > best_score {
                best_score = score;
                best_move = Some(m);
            }
            if score > alpha {
                alpha = score;
            }
            if alpha >= beta {
                if is_quiet {
                    let n = quiets_tried.min(tried.len());
                    self.heur
                        .record_cutoff(side, m, depth, ply as usize, prev, &tried[..n]);
                } else if self.params.use_capture_history {
                    // The position is already unmade, so read the ranks back off
                    // the board rather than remembering them across the search.
                    if let (Some(attacker), Some(victim)) =
                        (pos.piece_at(m.from()), pos.piece_at(m.to()))
                    {
                        self.heur.record_capture_cutoff(
                            side,
                            attacker.rank(),
                            m.to(),
                            victim.rank(),
                            depth,
                        );
                    }
                }
                break;
            }
            if is_quiet {
                if quiets_tried < tried.len() {
                    tried[quiets_tried] = m;
                }
                quiets_tried += 1;
            }
            idx += 1;
        }

        if let Some(m) = best_move {
            let bound = if best_score <= original_alpha {
                BOUND_UPPER
            } else if best_score >= beta {
                BOUND_LOWER
            } else {
                BOUND_EXACT
            };
            // `EVAL_NONE`, not 0: a principal-variation node never computed a
            // static evaluation, and 0 would read back as a genuine dead-level
            // one. Harmless while nobody read the slot; a silent lie now that
            // `use_tt_eval` does.
            self.tt.store(
                key,
                Some(m),
                best_score,
                static_eval.unwrap_or(EVAL_NONE),
                depth as i8,
                bound,
                ply,
            );
        }

        best_score
    }

    // -----------------------------------------------------------------
    // Quiescence
    // -----------------------------------------------------------------

    /// Search the tactical continuations only, so evaluation is never applied in
    /// the middle of an exchange.
    ///
    /// Fail-soft, unlike the Python version, which returned `beta` on a cutoff
    /// while the main search returned a real score. That mismatch was contained
    /// only because quiescence never wrote to the transposition table; making the
    /// two agree removes the trap rather than the containment.
    fn quiesce(&mut self, pos: &mut Position, mut alpha: i32, beta: i32, ply: i32, qply: i32) -> i32 {
        self.nodes += 1;
        self.seldepth = self.seldepth.max((ply + qply) as usize);
        if self.check_clock() {
            return 0;
        }

        let distance = ply + qply;

        if let Some(winner) = pos.result() {
            return if winner == pos.side_to_move() {
                mate_in(distance)
            } else {
                mated_in(distance)
            };
        }

        let mut moves = MoveList::new();
        generate_into(pos, &mut moves);
        if moves.is_empty() {
            return mated_in(distance);
        }
        if pos.is_fifty_move_draw() {
            return 0;
        }

        let side = pos.side_to_move();
        let den = enemy_den(side.index());

        // A den entry available here wins outright; no need to evaluate anything.
        for &m in moves.as_slice() {
            if m.to() == den {
                return mate_in(distance + 1);
            }
        }

        let stand_pat = evaluate_with(pos, side, &self.eval_params);
        if qply >= self.params.quiescence_max_ply {
            return stand_pat;
        }
        let mut best = stand_pat;
        if stand_pat >= beta {
            return stand_pat;
        }
        if stand_pat > alpha {
            alpha = stand_pat;
        }

        // Captures only from here.
        let mut noisy = MoveList::new();
        for &m in moves.as_slice() {
            if pos.piece_at(m.to()).is_some() {
                noisy.push(m);
            }
        }
        let mut ordered = OrderedMoves::new(
            pos,
            noisy,
            None,
            &self.heur,
            ply as usize,
            None,
            self.params.use_capture_history,
            false,
            &self.eval_params,
        );

        while let Some((m, order_score)) = ordered.next_scored() {
            // Ordering has already run static exchange evaluation and sorted the
            // losing captures into their own band below everything else, so the
            // first one it yields means the rest are losing too.
            if OrderedMoves::is_losing_capture(order_score) {
                break;
            }

            let victim = pos.piece_at(m.to()).expect("noisy move without a victim");
            let victim_value = self.eval_params.piece_values[victim.rank() as usize];

            // Delta pruning: even winning this piece for free does not reach
            // alpha. Note this must use the victim's *value*, not its rank -- the
            // Python engine once compared a rank (1..8) against a centipawn
            // margin, which collapsed the test into "am I 200 behind?" and then
            // pruned every capture, free material included.
            if stand_pat + victim_value + self.params.delta_margin < alpha {
                continue;
            }

            pos.make(m);
            let score = -self.quiesce(pos, -beta, -alpha, ply, qply + 1);
            pos.unmake();
            if self.stopped {
                return 0;
            }

            if score > best {
                best = score;
            }
            if score >= beta {
                return score;
            }
            if score > alpha {
                alpha = score;
            }
        }

        best
    }
}

