//! The score scale.
//!
//! One scale only: `|static eval| << |mate score| < INF`. The Python engine once
//! had `evaluate` return `±INF` for a decided position, which is *larger* than
//! any real mate -- it outranked every mate, destroyed mate-distance preference,
//! and wrote out-of-window scores into the transposition table. Evaluation must
//! never return a terminal score; the search owns terminality, because only it
//! knows the ply distance.
//!
//! Everything fits in `i16` so transposition entries stay 16 bytes.

/// Alpha/beta sentinel. Never a real score.
pub const INF: i32 = 32_000;
/// A win at ply 0. Real mate scores are `MATE - ply`.
pub const MATE: i32 = 30_000;
/// Deepest ply the search will visit.
pub const MAX_PLY: usize = 128;
/// Any score at least this large is a mate score rather than an evaluation.
pub const MATE_BOUND: i32 = MATE - MAX_PLY as i32;

/// "No static evaluation is known here."
///
/// A transposition entry's `eval` slot needs a value even at nodes that never
/// computed one — principal-variation nodes and nodes inside the mate window
/// skip the whole pruning block. Writing 0 there would be indistinguishable from
/// a genuine dead-level evaluation, so a reader would silently trust a made-up
/// number. `i16::MIN` cannot be produced by `evaluate`, which is bounded well
/// inside the mate scale.
pub const EVAL_NONE: i32 = i16::MIN as i32;

/// We win, `ply` plies from the root.
#[inline(always)]
pub const fn mate_in(ply: i32) -> i32 {
    MATE - ply
}

/// We lose, `ply` plies from the root.
#[inline(always)]
pub const fn mated_in(ply: i32) -> i32 {
    -MATE + ply
}

#[inline(always)]
pub fn is_mate_score(s: i32) -> bool {
    s.abs() >= MATE_BOUND
}

/// Moves to mate, positive if the side to move wins. `None` for an ordinary score.
pub fn mate_distance(score: i32) -> Option<i32> {
    if score >= MATE_BOUND {
        Some((MATE - score + 1) / 2)
    } else if score <= -MATE_BOUND {
        Some(-((MATE + score + 1) / 2))
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // The scale invariants are checked at compile time: they are properties of
    // the constants, so a violation should fail the build rather than a test run.
    const _: () = {
        assert!(MATE < INF, "a mate must never outrank the alpha/beta sentinel");
        assert!(MATE_BOUND < MATE);
        assert!(INF <= i16::MAX as i32, "scores must fit a 16-bit TT entry");
        assert!(-INF >= i16::MIN as i32);
    };

    #[test]
    fn a_static_evaluation_can_never_reach_a_mate_score() {
        // A generous upper bound on the evaluation: every piece, every positional
        // bonus at once. If this ever approached MATE_BOUND, an ordinary position
        // could be mistaken for a forced win.
        let max_eval = 3600 + 8 * (80 + 48 + 20 + 120 + 25 + 75 + 60) + 200;
        assert!(max_eval < MATE_BOUND, "eval scale collides with mate scores");
    }

    #[test]
    fn a_mate_is_recognised_at_every_reachable_ply() {
        for ply in 0..MAX_PLY as i32 {
            assert!(is_mate_score(mate_in(ply)), "mate_in({ply})");
            assert!(is_mate_score(mated_in(ply)), "mated_in({ply})");
        }
        assert!(!is_mate_score(0));
        assert!(!is_mate_score(5000));
    }
}
