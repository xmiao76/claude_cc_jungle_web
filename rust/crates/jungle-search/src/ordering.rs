//! Move ordering.
//!
//! Alpha-beta lives or dies on this: a perfectly ordered tree costs the square
//! root of an unordered one. The bands, best first:
//!
//! 1. **Den entry** — an immediate, unconditional win. Nothing outranks it.
//! 2. **The transposition move** — the best move found here before.
//! 3. **Winning and equal captures**, by most-valuable-victim then
//!    least-valuable-attacker, filtered by static exchange evaluation.
//! 4. **Killers**, then the counter-move.
//! 5. **Quiet moves**, by history.
//! 6. **Losing captures**, by how much they lose.
//!
//! Two fixes relative to the Python engine. Its counter-move table was keyed by
//! the previous move's squares *only*, so Blue and Black shared entries and each
//! polluted the other's; here the key includes the side. And its history bonus
//! and malus were silently skipped whenever the side-keyed history flag was off,
//! which made that flag do two unrelated things at once.

use jungle_core::position::Position;
use jungle_core::types::{Color, Move, MoveList, Square, NSQ};
use jungle_eval::params::EvalParams;

use crate::see::see_with;

const DEN_ENTRY: i32 = 1 << 30;
const TT_MOVE: i32 = 1 << 29;
const GOOD_CAPTURE: i32 = 1 << 22;
const KILLER_0: i32 = 1 << 21;
const KILLER_1: i32 = KILLER_0 - 1;
const COUNTER: i32 = 1 << 20;
const BAD_CAPTURE: i32 = -(1 << 22);

/// History is capped so a long-running table cannot swamp the killer band.
pub const HISTORY_MAX: i32 = 1 << 19;

/// Capture history is capped well below the gap between `GOOD_CAPTURE` and
/// `TT_MOVE`, so it can reorder captures among themselves but never lift one out
/// of its band.
pub const CAPTURE_HISTORY_MAX: i32 = 1 << 16;

/// Per-side quiet-move history and counter-moves.
pub struct Heuristics {
    /// `[side][from][to]`
    history: Vec<i32>,
    /// `[side][from][to]` -> the reply that refuted it, packed.
    counters: Vec<u16>,
    /// `[side][attacker rank][to][victim rank]`. Jungle has one animal of each
    /// rank per side, so an attacker rank *is* a piece identity — which is also
    /// why this cannot replace MVV-LVA the way it can in chess: there are no
    /// same-class ties for it to break. It blends with MVV-LVA instead.
    capture_history: Vec<i32>,
    /// `[side][previous move's destination][this move's destination]`. Answers
    /// "after they went there, what worked for us?", which plain from/to history
    /// cannot express.
    continuation: Vec<i32>,
    killers: [[u16; 2]; crate::score::MAX_PLY],
}

#[inline(always)]
fn idx(side: Color, from: Square, to: Square) -> usize {
    (side.index() * NSQ + from as usize) * NSQ + to as usize
}

#[inline(always)]
fn capt_idx(side: Color, attacker: u8, to: Square, victim: u8) -> usize {
    (((side.index() * 9) + attacker as usize) * NSQ + to as usize) * 9 + victim as usize
}

impl Heuristics {
    pub fn new() -> Heuristics {
        Heuristics {
            history: vec![0; 2 * NSQ * NSQ],
            counters: vec![0; 2 * NSQ * NSQ],
            capture_history: vec![0; 2 * 9 * NSQ * 9],
            continuation: vec![0; 2 * NSQ * NSQ],
            killers: [[0; 2]; crate::score::MAX_PLY],
        }
    }

    pub fn clear(&mut self) {
        self.history.iter_mut().for_each(|v| *v = 0);
        self.counters.iter_mut().for_each(|v| *v = 0);
        self.capture_history.iter_mut().for_each(|v| *v = 0);
        self.continuation.iter_mut().for_each(|v| *v = 0);
        self.killers = [[0; 2]; crate::score::MAX_PLY];
    }

    /// Halve every history entry. Run once per iteration so recent evidence
    /// outweighs old.
    pub fn age(&mut self) {
        self.history.iter_mut().for_each(|v| *v /= 2);
        self.capture_history.iter_mut().for_each(|v| *v /= 2);
        self.continuation.iter_mut().for_each(|v| *v /= 2);
    }

    #[inline(always)]
    pub fn history_of(&self, side: Color, mv: Move) -> i32 {
        self.history[idx(side, mv.from(), mv.to())]
    }

    #[inline(always)]
    fn capture_history_of(&self, side: Color, attacker: u8, to: Square, victim: u8) -> i32 {
        self.capture_history[capt_idx(side, attacker, to, victim)]
    }

    #[inline(always)]
    fn continuation_of(&self, side: Color, prev: Option<Move>, mv: Move) -> i32 {
        match prev {
            Some(p) => self.continuation[idx(side, p.to(), mv.to())],
            None => 0,
        }
    }

    /// A capture caused a cutoff. Bonus only, no malus for the captures tried
    /// before it: losing captures are rare here (only equal-or-lower ranks are
    /// capturable at all), so the tried-and-failed set this would penalise is
    /// usually empty and the bookkeeping would not pay for itself.
    pub fn record_capture_cutoff(
        &mut self,
        side: Color,
        attacker: u8,
        to: Square,
        victim: u8,
        depth: i32,
    ) {
        let h = &mut self.capture_history[capt_idx(side, attacker, to, victim)];
        *h = (*h + depth * depth).min(CAPTURE_HISTORY_MAX);
    }

    pub fn record_cutoff(
        &mut self,
        side: Color,
        mv: Move,
        depth: i32,
        ply: usize,
        prev: Option<Move>,
        tried_quiets: &[Move],
    ) {
        let bonus = depth * depth;

        let h = &mut self.history[idx(side, mv.from(), mv.to())];
        *h = (*h + bonus).min(HISTORY_MAX);

        // Everything quiet we tried first and that failed to cut off gets a
        // matching penalty, so history measures relative usefulness rather than
        // "how often was this move available".
        for &q in tried_quiets {
            if q == mv {
                continue;
            }
            let h = &mut self.history[idx(side, q.from(), q.to())];
            *h = (*h - bonus).max(-HISTORY_MAX);
        }

        if ply < crate::score::MAX_PLY {
            let k = &mut self.killers[ply];
            if k[0] != mv.0 {
                k[1] = k[0];
                k[0] = mv.0;
            }
        }

        if let Some(p) = prev {
            self.counters[idx(side, p.from(), p.to())] = mv.0;
            let c = &mut self.continuation[idx(side, p.to(), mv.to())];
            *c = (*c + bonus).min(HISTORY_MAX);
            for &q in tried_quiets {
                if q == mv {
                    continue;
                }
                let c = &mut self.continuation[idx(side, p.to(), q.to())];
                *c = (*c - bonus).max(-HISTORY_MAX);
            }
        }
    }

    #[inline(always)]
    fn killer(&self, ply: usize, slot: usize) -> u16 {
        if ply < crate::score::MAX_PLY {
            self.killers[ply][slot]
        } else {
            0
        }
    }

    /// Did this move cause a cutoff at this ply before?
    ///
    /// Used to hold late-move reductions back. A killer is a quiet move already
    /// known to refute something here, so reducing it searches the one quiet move
    /// with evidence behind it at a shallower depth than the ones without.
    #[inline(always)]
    pub fn is_killer(&self, ply: usize, mv: Move) -> bool {
        ply < crate::score::MAX_PLY
            && (self.killers[ply][0] == mv.0 || self.killers[ply][1] == mv.0)
    }

    #[inline(always)]
    fn counter(&self, side: Color, prev: Option<Move>) -> u16 {
        match prev {
            Some(p) => self.counters[idx(side, p.from(), p.to())],
            None => 0,
        }
    }
}

impl Default for Heuristics {
    fn default() -> Self {
        Self::new()
    }
}

/// A move list with an ordering score per move, consumed best-first.
pub struct OrderedMoves {
    moves: MoveList,
    scores: [i32; jungle_core::types::MAX_MOVES],
    next: usize,
}

impl OrderedMoves {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        pos: &Position,
        moves: MoveList,
        tt_move: Option<Move>,
        heur: &Heuristics,
        ply: usize,
        prev: Option<Move>,
        use_capture_history: bool,
        use_cont_history: bool,
        ep: &EvalParams,
    ) -> OrderedMoves {
        let side = pos.side_to_move();
        let enemy_den = jungle_core::bitboard::enemy_den(side.index());
        let tt = tt_move.map_or(0, |m| m.0);
        let k0 = heur.killer(ply, 0);
        let k1 = heur.killer(ply, 1);
        let cm = heur.counter(side, prev);

        let mut scores = [0i32; jungle_core::types::MAX_MOVES];
        for (i, &m) in moves.as_slice().iter().enumerate() {
            scores[i] = if m.to() == enemy_den {
                // Unconditional win. Nothing else can be better, so it is scored
                // above even the transposition move.
                DEN_ENTRY
            } else if m.0 == tt {
                TT_MOVE
            } else if let Some(victim) = pos.piece_at(m.to()) {
                let attacker = pos.piece_at(m.from()).expect("mover missing");
                let mvv_lva = ep.piece_values[victim.rank() as usize] * 16
                    - ep.piece_values[attacker.rank() as usize];
                // Blended, not substituted: with one animal of each rank per
                // side there are no same-value captures for history to break
                // ties between, so on its own it would order nothing.
                let ch = if use_capture_history {
                    heur.capture_history_of(side, attacker.rank(), m.to(), victim.rank())
                } else {
                    0
                };
                let exchange = see_with(pos, m, ep);
                if exchange >= 0 {
                    GOOD_CAPTURE + mvv_lva + ch
                } else {
                    BAD_CAPTURE + exchange
                }
            } else if m.0 == k0 {
                KILLER_0
            } else if m.0 == k1 {
                KILLER_1
            } else if m.0 == cm {
                COUNTER
            } else if use_cont_history {
                // Clamped to the plain-history range so two tables cannot sum
                // their way up into the counter-move band.
                (heur.history_of(side, m) + heur.continuation_of(side, prev, m))
                    .clamp(-HISTORY_MAX, HISTORY_MAX)
            } else {
                heur.history_of(side, m)
            };
        }

        OrderedMoves {
            moves,
            scores,
            next: 0,
        }
    }

    pub fn len(&self) -> usize {
        self.moves.len()
    }

    pub fn is_empty(&self) -> bool {
        self.moves.is_empty()
    }

    /// The next-best move, by selection rather than a full sort: most nodes fail
    /// high on the first move or two, so sorting the tail is wasted work.
    pub fn next_move(&mut self) -> Option<Move> {
        self.next_scored().map(|(m, _)| m)
    }

    /// As [`next_move`], but also yields the ordering score.
    ///
    /// Quiescence uses this to tell a winning capture from a losing one without
    /// running static exchange evaluation a second time -- ordering already paid
    /// for that, and SEE is the most expensive thing either of them does.
    pub fn next_scored(&mut self) -> Option<(Move, i32)> {
        if self.next >= self.moves.len() {
            return None;
        }
        let mut best = self.next;
        for i in self.next + 1..self.moves.len() {
            if self.scores[i] > self.scores[best] {
                best = i;
            }
        }
        self.moves.swap(self.next, best);
        self.scores.swap(self.next, best);
        let m = self.moves[self.next];
        let s = self.scores[self.next];
        self.next += 1;
        Some((m, s))
    }

    /// True if this ordering score is in the losing-capture band.
    #[inline(always)]
    pub fn is_losing_capture(score: i32) -> bool {
        score < 0
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use jungle_core::generate;
    use jungle_core::types::{animal, sq, Piece};

    #[test]
    fn a_den_entry_is_ordered_first() {
        let mut pos = Position::empty();
        pos.place(sq(3, 1), Piece::new(Color::Blue, animal::WOLF));
        pos.place(sq(0, 4), Piece::new(Color::Blue, animal::LION));
        pos.place(sq(6, 4), Piece::new(Color::Black, animal::RAT));
        let heur = Heuristics::new();
        let mut om = OrderedMoves::new(&pos, generate(&pos), None, &heur, 0, None, false, false, &EvalParams::default());
        let first = om.next_move().unwrap();
        assert_eq!(first.to(), jungle_core::bitboard::DEN_BLACK);
    }

    #[test]
    fn winning_captures_precede_quiets_and_losing_captures() {
        let mut pos = Position::empty();
        // Blue Wolf can take an undefended Black Cat, or step to an empty square.
        pos.place(sq(3, 4), Piece::new(Color::Blue, animal::WOLF));
        pos.place(sq(3, 3), Piece::new(Color::Black, animal::CAT));
        pos.place(sq(6, 0), Piece::new(Color::Black, animal::ELEPHANT));
        let heur = Heuristics::new();
        let mut om = OrderedMoves::new(&pos, generate(&pos), None, &heur, 0, None, false, false, &EvalParams::default());
        let first = om.next_move().unwrap();
        assert_eq!(first, Move::new(sq(3, 4), sq(3, 3)));
    }

    #[test]
    fn history_is_capped_and_symmetric_in_sign() {
        let mut h = Heuristics::new();
        let mv = Move::new(sq(0, 0), sq(0, 1));
        for _ in 0..10_000 {
            h.record_cutoff(Color::Blue, mv, 20, 0, None, &[]);
        }
        assert_eq!(h.history_of(Color::Blue, mv), HISTORY_MAX);
        // ...and the other side's table is untouched.
        assert_eq!(h.history_of(Color::Black, mv), 0);
    }

    #[test]
    fn counter_moves_do_not_leak_between_sides() {
        let mut h = Heuristics::new();
        let prev = Move::new(sq(0, 0), sq(0, 1));
        let reply = Move::new(sq(6, 6), sq(6, 5));
        h.record_cutoff(Color::Blue, reply, 3, 0, Some(prev), &[]);
        assert_eq!(h.counter(Color::Blue, Some(prev)), reply.0);
        assert_eq!(h.counter(Color::Black, Some(prev)), 0, "sides share entries");
    }
}
