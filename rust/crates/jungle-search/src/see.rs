//! Static Exchange Evaluation: what a capture is actually worth once both sides
//! have finished trading on the square.
//!
//! Two Jungle-specific wrinkles a chess SEE does not have. Attackers are not just
//! the orthogonal neighbours -- a Lion or Tiger can strike from across the river
//! -- and legality is not a rank comparison: the water boundary, the trap
//! rank-0 rule and the Rat/Elephant exception all decide who may recapture.
//!
//! Attackers are ordered by **value, not rank**. With the linear value table
//! those coincide, but they will not once the values are retuned, and picking the
//! lowest-ranked recapturer instead of the cheapest one silently misprices the
//! exchange.

use jungle_core::bitboard::{bb, iter_squares, Bitboard, JUMPS};
use jungle_core::position::Position;
use jungle_core::rules::can_capture;
use jungle_core::types::{animal, Color, Move, Piece, Square};
use jungle_eval::params::EvalParams;

/// Longest exchange sequence we track. Sixteen pieces means at most sixteen
/// captures on one square; double it and round up for headroom.
const MAX_SWAPS: usize = 34;

/// The cheapest piece of `side` that may legally capture `target` on `sq`,
/// ignoring pieces already spent in this exchange.
fn cheapest_attacker(
    pos: &Position,
    sq: Square,
    side: Color,
    removed: Bitboard,
    target: Piece,
    ep: &EvalParams,
) -> Option<(Square, Piece)> {
    let mut best: Option<(Square, Piece, i32)> = None;
    let consider = |from: Square, piece: Piece, best: &mut Option<(Square, Piece, i32)>| {
        if !can_capture(piece, target, from, sq) {
            return;
        }
        let value = ep.piece_values[piece.rank() as usize];
        if best.is_none() || value < best.unwrap().2 {
            *best = Some((from, piece, value));
        }
    };

    // Orthogonal neighbours.
    let candidates =
        jungle_core::bitboard::ADJACENT[sq as usize] & pos.occupancy(side) & !removed;
    for from in iter_squares(candidates) {
        let piece = pos.piece_at(from).expect("occupancy disagrees with the mailbox");
        consider(from, piece, &mut best);
    }

    // Leaping attackers. Jumps are symmetric -- if A can leap to B then B can leap
    // to A, since both stand on the banks of the same river block -- so the jumps
    // *from* `sq` name exactly the squares that can leap *to* it.
    // A Rat already spent in the exchange no longer blocks.
    let live_rats = pos.rats() & !removed;
    for k in 0..JUMPS.n[sq as usize] as usize {
        let from = JUMPS.to[sq as usize][k];
        if removed & bb(from) != 0 || pos.occupancy(side) & bb(from) == 0 {
            continue;
        }
        if JUMPS.path[sq as usize][k] & live_rats != 0 {
            continue;
        }
        let piece = pos.piece_at(from).expect("occupancy disagrees with the mailbox");
        let rank = piece.rank();
        let can_leap = rank == animal::LION
            || (rank == animal::TIGER && !JUMPS.vertical[sq as usize][k]);
        if can_leap {
            consider(from, piece, &mut best);
        }
    }

    best.map(|(s, p, _)| (s, p))
}

/// Net material from the exchange starting with `mv`, from the mover's point of
/// view. Zero for a non-capture.
pub fn see(pos: &Position, mv: Move) -> i32 {
    see_with(pos, mv, &EvalParams::default())
}

/// Static exchange evaluation with explicit piece values.
///
/// Evaluation, this, and quiescence delta pruning must read the same table or
/// they disagree about what a capture is worth -- so the table is passed in
/// rather than reached for.
pub fn see_with(pos: &Position, mv: Move, ep: &EvalParams) -> i32 {
    let to = mv.to();
    let Some(victim) = pos.piece_at(to) else {
        return 0;
    };
    let attacker = pos
        .piece_at(mv.from())
        .expect("SEE called on a move with no mover");

    let mut gain = [0i32; MAX_SWAPS];
    gain[0] = ep.piece_values[victim.rank() as usize];

    let mut on_square = attacker;
    let mut removed = bb(mv.from());
    let mut side = attacker.color().flip();
    let mut d = 0usize;

    while d + 1 < MAX_SWAPS {
        let Some((from, piece)) = cheapest_attacker(pos, to, side, removed, on_square, ep) else {
            break;
        };
        d += 1;
        gain[d] = ep.piece_values[on_square.rank() as usize];
        removed |= bb(from);
        on_square = piece;
        side = side.flip();
    }

    // Walk back down the sequence: at each level the side to move may decline the
    // recapture, so a losing continuation is worth nothing rather than negative.
    while d > 0 {
        gain[d - 1] -= gain[d].max(0);
        d -= 1;
    }
    gain[0]
}

#[cfg(test)]
mod tests {
    use super::*;
    // Only the tests still name the shipped table directly; the engine reads
    // whatever `EvalParams` it was given.
    use jungle_eval::params::PIECE_VALUES;
    use jungle_core::types::sq;

    fn build(specs: &[(usize, usize, Color, u8)], stm: Color) -> Position {
        let mut p = Position::empty();
        for &(c, r, color, rank) in specs {
            p.place(sq(c, r), Piece::new(color, rank));
        }
        p.set_side_to_move(stm);
        p
    }

    #[test]
    fn a_free_capture_is_worth_the_victim() {
        let p = build(
            &[
                (0, 4, Color::Blue, animal::LEOPARD),
                (0, 5, Color::Black, animal::DOG),
            ],
            Color::Blue,
        );
        assert_eq!(see(&p, Move::new(sq(0, 4), sq(0, 5))), PIECE_VALUES[3]);
    }

    #[test]
    fn a_defended_capture_nets_the_difference() {
        // Blue Leopard (rank 5, 500) takes Black Dog (rank 3, 300); Black Tiger
        // (rank 6) outranks the Leopard and recaptures.
        let p = build(
            &[
                (0, 4, Color::Blue, animal::LEOPARD),
                (0, 5, Color::Black, animal::DOG),
                (0, 6, Color::Black, animal::TIGER),
            ],
            Color::Blue,
        );
        assert_eq!(see(&p, Move::new(sq(0, 4), sq(0, 5))), 300 - 500);
    }

    #[test]
    fn a_lower_ranked_neighbour_is_not_a_defender() {
        // The same position with a Wolf (rank 4) instead of the Tiger. It cannot
        // take a Leopard, so it defends nothing and the capture is free. A SEE
        // that counted adjacency rather than legality would price this at -200.
        let p = build(
            &[
                (0, 4, Color::Blue, animal::LEOPARD),
                (0, 5, Color::Black, animal::DOG),
                (0, 6, Color::Black, animal::WOLF),
            ],
            Color::Blue,
        );
        assert_eq!(see(&p, Move::new(sq(0, 4), sq(0, 5))), 300);
    }

    #[test]
    fn a_non_capture_is_zero() {
        let p = build(&[(0, 4, Color::Blue, animal::LEOPARD)], Color::Blue);
        assert_eq!(see(&p, Move::new(sq(0, 4), sq(0, 3))), 0);
    }

    #[test]
    fn the_recapturer_must_itself_be_legal() {
        // Black's only "defender" is an Elephant, which may not take a Rat, so the
        // Blue Rat's capture is free despite standing next to it.
        let p = build(
            &[
                (0, 4, Color::Blue, animal::RAT),
                (0, 5, Color::Black, animal::CAT),
                (0, 6, Color::Black, animal::ELEPHANT),
            ],
            Color::Blue,
        );
        assert_eq!(see(&p, Move::new(sq(0, 4), sq(0, 5))), PIECE_VALUES[2]);
    }

    #[test]
    fn a_leaping_recapture_is_counted() {
        // Blue Cat takes a Black Dog on (3,4); Black's Lion recaptures from (0,4)
        // across the river. A SEE that only looked at neighbours would call this
        // capture free.
        let p = build(
            &[
                (3, 3, Color::Blue, animal::CAT),
                (3, 4, Color::Black, animal::DOG),
                (0, 4, Color::Black, animal::LION),
            ],
            Color::Blue,
        );
        assert_eq!(see(&p, Move::new(sq(3, 3), sq(3, 4))), 300 - 200);
    }

    #[test]
    fn a_rat_in_the_water_blocks_the_leaping_recapture() {
        let p = build(
            &[
                (3, 3, Color::Blue, animal::CAT),
                (3, 4, Color::Black, animal::DOG),
                (0, 4, Color::Black, animal::LION),
                (1, 4, Color::Blue, animal::RAT),
            ],
            Color::Blue,
        );
        assert_eq!(see(&p, Move::new(sq(3, 3), sq(3, 4))), 300);
    }

    #[test]
    fn jumps_are_symmetric() {
        // The reverse-attacker lookup above depends on this.
        for from in 0..jungle_core::types::NSQ as Square {
            for k in 0..JUMPS.n[from as usize] as usize {
                let to = JUMPS.to[from as usize][k];
                let back = (0..JUMPS.n[to as usize] as usize)
                    .any(|j| JUMPS.to[to as usize][j] == from);
                assert!(back, "jump {from} -> {to} has no reverse");
            }
        }
    }
}
