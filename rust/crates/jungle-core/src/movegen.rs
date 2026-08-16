//! Legal move generation.
//!
//! There is no pseudo-legal stage and no filtering pass: Jungle has no check, so
//! every move produced here is legal as generated.

use crate::bitboard::{bb, iter_squares, own_den, ADJACENT, JUMPS, RIVER};
use crate::position::Position;
use crate::rules::can_capture;
use crate::types::{animal, Move, MoveList};

/// All legal moves for the side to move.
pub fn generate(pos: &Position) -> MoveList {
    let mut list = MoveList::new();
    generate_into(pos, &mut list);
    list
}

pub fn generate_into(pos: &Position, list: &mut MoveList) {
    let us = pos.side_to_move();
    let own = pos.occupancy(us);
    let den = own_den(us.index());
    let den_bb = bb(den);
    let rats = pos.rats();

    for from in iter_squares(own) {
        let piece = pos.piece_at(from).expect("occupancy disagrees with the mailbox");
        let rank = piece.rank();

        // --- single steps -------------------------------------------------
        // Never onto a friendly piece, never into our own den, and only the Rat
        // may set foot in the river.
        let mut targets = ADJACENT[from as usize] & !own & !den_bb;
        if rank != animal::RAT {
            targets &= !RIVER;
        }

        for to in iter_squares(targets) {
            match pos.piece_at(to) {
                None => list.push(Move::new(from, to)),
                Some(victim) => {
                    if can_capture(piece, victim, from, to) {
                        list.push(Move::new(from, to));
                    }
                }
            }
        }

        // --- river jumps (Lion and Tiger only) ----------------------------
        if rank == animal::LION || rank == animal::TIGER {
            let n = JUMPS.n[from as usize] as usize;
            for k in 0..n {
                // The row-axis leap crosses three river squares; only the Lion
                // reaches that far. The Tiger takes the column-axis leap only.
                if JUMPS.vertical[from as usize][k] && rank != animal::LION {
                    continue;
                }
                // Any Rat, either colour, standing in the water on the flight
                // path blocks the leap.
                if JUMPS.path[from as usize][k] & rats != 0 {
                    continue;
                }
                let to = JUMPS.to[from as usize][k];
                if to == den || own & bb(to) != 0 {
                    continue;
                }
                match pos.piece_at(to) {
                    None => list.push(Move::new(from, to)),
                    Some(victim) => {
                        if can_capture(piece, victim, from, to) {
                            list.push(Move::new(from, to));
                        }
                    }
                }
            }
        }
    }
}

/// Captures plus den entries — the moves quiescence must not prune away.
///
/// Jungle has no check; den entry is the tactical analogue, and a search that
/// stops before considering it walks into an instant loss.
pub fn generate_noisy(pos: &Position) -> MoveList {
    let enemy_den = crate::bitboard::enemy_den(pos.side_to_move().index());
    let mut out = MoveList::new();
    for &m in generate(pos).as_slice() {
        if pos.piece_at(m.to()).is_some() || m.to() == enemy_den {
            out.push(m);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{sq, Color, Piece};

    fn pos_with(specs: &[(usize, usize, Color, u8)], stm: Color) -> Position {
        let mut p = Position::empty();
        for &(c, r, color, rank) in specs {
            p.place(sq(c, r), Piece::new(color, rank));
        }
        p.set_side_to_move(stm);
        p
    }

    fn moves_from(p: &Position, from: u8) -> Vec<u8> {
        let mut v: Vec<u8> = generate(p)
            .as_slice()
            .iter()
            .filter(|m| m.from() == from)
            .map(|m| m.to())
            .collect();
        v.sort_unstable();
        v
    }

    #[test]
    fn start_position_has_24_legal_moves() {
        assert_eq!(generate(&Position::startpos()).len(), 24);
    }

    #[test]
    fn only_the_rat_may_enter_the_river() {
        for rank in 1..=8u8 {
            let p = pos_with(&[(0, 4, Color::Blue, rank)], Color::Blue);
            let can_swim = moves_from(&p, sq(0, 4)).contains(&sq(1, 4));
            assert_eq!(
                can_swim,
                rank == animal::RAT,
                "rank {rank} entering the river"
            );
        }
    }

    #[test]
    fn a_piece_may_never_enter_its_own_den() {
        let p = pos_with(&[(3, 7, Color::Blue, animal::WOLF)], Color::Blue);
        assert!(!moves_from(&p, sq(3, 7)).contains(&sq(3, 8)));
        let p = pos_with(&[(3, 1, Color::Black, animal::WOLF)], Color::Black);
        assert!(!moves_from(&p, sq(3, 1)).contains(&sq(3, 0)));
    }

    #[test]
    fn the_enemy_den_is_reachable() {
        let p = pos_with(&[(3, 1, Color::Blue, animal::WOLF)], Color::Blue);
        assert!(moves_from(&p, sq(3, 1)).contains(&sq(3, 0)));
    }

    #[test]
    fn lion_leaps_both_axes_and_tiger_only_the_short_one() {
        // Column-axis leap (2 river squares): both may make it.
        for rank in [animal::LION, animal::TIGER] {
            let p = pos_with(&[(0, 4, Color::Blue, rank)], Color::Blue);
            assert!(
                moves_from(&p, sq(0, 4)).contains(&sq(3, 4)),
                "rank {rank} horizontal leap"
            );
        }
        // Row-axis leap (3 river squares): Lion only.
        for rank in [animal::LION, animal::TIGER] {
            let p = pos_with(&[(1, 2, Color::Blue, rank)], Color::Blue);
            assert_eq!(
                moves_from(&p, sq(1, 2)).contains(&sq(1, 6)),
                rank == animal::LION,
                "rank {rank} vertical leap"
            );
        }
    }

    #[test]
    fn a_rat_in_the_water_blocks_a_leap_whichever_side_owns_it() {
        for color in [Color::Blue, Color::Black] {
            let p = pos_with(
                &[(0, 4, Color::Blue, animal::LION), (1, 4, color, animal::RAT)],
                Color::Blue,
            );
            assert!(
                !moves_from(&p, sq(0, 4)).contains(&sq(3, 4)),
                "{color:?} rat should block"
            );
        }
    }

    #[test]
    fn a_non_rat_in_the_water_does_not_block_a_leap() {
        // Unreachable in play, but it is the case that separates "a Rat blocks"
        // from "any occupant blocks", so it is pinned deliberately.
        let p = pos_with(
            &[
                (0, 4, Color::Blue, animal::LION),
                (1, 4, Color::Black, animal::WOLF),
            ],
            Color::Blue,
        );
        assert!(moves_from(&p, sq(0, 4)).contains(&sq(3, 4)));
    }

    #[test]
    fn noisy_moves_include_a_den_entry_that_captures_nothing() {
        let p = pos_with(&[(3, 1, Color::Blue, animal::WOLF)], Color::Blue);
        let noisy: Vec<u8> = generate_noisy(&p).as_slice().iter().map(|m| m.to()).collect();
        assert!(noisy.contains(&sq(3, 0)));
    }
}
