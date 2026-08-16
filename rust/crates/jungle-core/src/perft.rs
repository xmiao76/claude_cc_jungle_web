//! Exhaustive leaf counts — the move-generation contract.
//!
//! Leaf conventions are copied exactly from `tools/perft.py`, because the frozen
//! counts in `tests/test_perft.py` only mean anything if both implementations
//! agree on what a leaf is:
//!
//! * A decided position (den entry or capture-all) is a leaf, counted as 1, and
//!   is not expanded — continuing past a win would count illegal continuations.
//! * A position with no legal move is a leaf, counted as 1.
//! * Otherwise `perft(0) == 1` and `perft(1) == number of legal moves`.

use crate::movegen::generate;
use crate::position::Position;
use crate::types::Move;

pub fn perft(pos: &mut Position, depth: u32) -> u64 {
    if pos.result().is_some() {
        return 1;
    }
    if depth == 0 {
        return 1;
    }
    let moves = generate(pos);
    if moves.is_empty() {
        return 1;
    }
    if depth == 1 {
        return moves.len() as u64;
    }

    let mut total = 0;
    for &m in moves.as_slice() {
        pos.make(m);
        total += perft(pos, depth - 1);
        pos.unmake();
    }
    total
}

/// Per-root-move counts, for localising a mismatch to one subtree.
pub fn perft_divide(pos: &mut Position, depth: u32) -> Vec<(Move, u64)> {
    assert!(depth >= 1, "divide needs depth >= 1");
    let mut out = Vec::new();
    for &m in generate(pos).as_slice() {
        pos.make(m);
        out.push((m, perft(pos, depth - 1)));
        pos.unmake();
    }
    out.sort_by_key(|&(m, _)| m);
    out
}
