//! Hand-crafted static evaluation, a faithful port of `ai/evaluator.py`.
//!
//! Two invariants govern this file, both inherited and both worth keeping.
//!
//! **Antisymmetry.** `evaluate(pos, Blue) == -evaluate(pos, Black)` for every
//! position. The way to keep it is to compute each per-piece term from the
//! *piece's own* colour and add it with a sign, never from the evaluating side's
//! point of view. Whole-position terms (side to move) are applied once, outside
//! the per-side loop; adding one inside would silently double its weight.
//!
//! **Purely static.** No mate scores, no draw scores, no terminal detection. The
//! search owns those, because only it knows the ply distance needed to score a
//! mate. The Python version once returned `±_INF` for a decided position, which
//! outranked every real mate and defeated mate-distance preference.

use jungle_core::bitboard::{bb, enemy_den, home_traps, iter_squares, own_den, weakening_traps, ADJACENT, JUMPS, RIVER};
use jungle_core::position::Position;
use jungle_core::types::{animal, col_of, row_of, Color, Piece, Square, ROWS};

use crate::params::*;

/// Evaluate from `color`'s point of view, with the shipped weights.
pub fn evaluate(pos: &Position, color: Color) -> i32 {
    evaluate_with(pos, color, &EvalParams::default())
}

/// Evaluate from `color`'s point of view with explicit weights.
///
/// The default weights reproduce `evaluate` exactly, which is what keeps the
/// 10,000-position golden evaluation corpus meaningful after making the
/// evaluation tunable.
pub fn evaluate_with(pos: &Position, color: Color, p: &EvalParams) -> i32 {
    let mut score = side_score(pos, color, p) - side_score(pos, color.flip(), p);

    // Applied once for the position, not per side.
    score += if pos.side_to_move() == color {
        p.tempo
    } else {
        -p.tempo
    };
    score
}

#[inline(always)]
fn advancement(side: Color, s: Square) -> i32 {
    let r = row_of(s) as i32;
    if side == Color::Blue {
        (ROWS as i32) - 1 - r
    } else {
        r
    }
}

#[inline(always)]
fn manhattan(a: Square, b: Square) -> i32 {
    (col_of(a) as i32 - col_of(b) as i32).abs() + (row_of(a) as i32 - row_of(b) as i32).abs()
}

fn side_score(pos: &Position, side: Color, p: &EvalParams) -> i32 {
    let si = side.index();
    let own = pos.occupancy(side);
    let opp_den = enemy_den(si);
    let my_den = own_den(si);
    // The traps that reduce *our* pieces to rank 0 -- the enemy's home traps.
    let weakening = weakening_traps(si);

    // There is exactly one enemy Elephant, so this is a lookup, not a scan.
    let enemy_elephant = pos
        .square_of(Piece::new(side.flip(), animal::ELEPHANT))
        .map(bb)
        .unwrap_or(0);

    let mut total = 0i32;

    for s in iter_squares(own) {
        let piece = pos.piece_at(s).expect("occupancy disagrees with the mailbox");
        let rank = piece.rank();

        total += p.piece_values[rank as usize];

        let adv = advancement(side, s);
        total += adv * p.advancement_per_row;
        if adv > MIDLINE {
            total += (adv - MIDLINE) * p.advancement_acceleration;
        }
        total += PST[adv as usize][col_of(s)] * p.pst_weight;

        // Approach to the enemy den, by Manhattan distance. This walks straight
        // through the river as if it were not there, which misprices the den
        // race; a BFS true-distance replacement was built and measured, and the
        // result was inconclusive rather than better, so the original stands.
        let dist = manhattan(s, opp_den);
        if dist <= p.den_proximity_max_dist {
            total += (p.den_proximity_max_dist + 1 - dist) * p.den_proximity_per_step;
        }

        if manhattan(s, my_den) <= 2 {
            total += p.den_defender;
        }

        if rank == animal::RAT {
            if RIVER & bb(s) != 0 {
                total += p.rat_in_water + p.rat_blocks_river;
            }
            if ADJACENT[s as usize] & enemy_elephant != 0 {
                total += p.rat_adjacent_to_enemy_elephant;
            }
        } else if rank >= animal::TIGER {
            // Pure geometry: it pays for standing on a river bank whether or not
            // the leap is available, and whether or not this animal can make it.
            // Faithful to the shipped Python; the corrected version measured
            // inconclusively worse.
            if JUMPS.n[s as usize] > 0 {
                total += p.jump_ready;
            }
        }

        if weakening & bb(s) != 0 {
            total -= p.trap_penalty;
        }
    }

    // Cheap mobility proxy: adjacent squares not occupied by us. Ignores the
    // river restriction, the own-den ban and capture legality, so it over-counts.
    let mut mobility = 0u32;
    for s in iter_squares(own) {
        mobility += (ADJACENT[s as usize] & !own).count_ones();
    }
    total += mobility as i32 * p.mobility;

    total -= p.den_threat * den_threat_level(pos, side);

    total
}

/// How exposed `side`'s den is to an enemy already standing on an approach square.
///
/// An enemy on one of our home traps has rank 0 and is one step from the den, so
/// it is weighted heavily when nothing of ours is adjacent to take it.
fn den_threat_level(pos: &Position, side: Color) -> i32 {
    let si = side.index();
    let my_den = own_den(si);
    let own = pos.occupancy(side);
    let enemy = pos.occupancy(side.flip());

    let mut danger = 0;
    for trap in iter_squares(home_traps(si)) {
        if manhattan(trap, my_den) != 1 {
            continue; // not an entry approach
        }
        if enemy & bb(trap) != 0 {
            danger += if ADJACENT[trap as usize] & own != 0 { 1 } else { 3 };
        }
    }
    danger
}

#[cfg(test)]
mod tests {
    use super::*;
    use jungle_core::generate;
    use jungle_core::types::sq;

    struct Rng(u64);
    impl Rng {
        fn next(&mut self) -> u64 {
            self.0 ^= self.0 >> 12;
            self.0 ^= self.0 << 25;
            self.0 ^= self.0 >> 27;
            self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
        }
    }

    #[test]
    fn the_start_position_is_balanced_apart_from_tempo() {
        let pos = Position::startpos();
        // Blue is to move, so Blue is ahead by exactly the tempo bonus.
        assert_eq!(evaluate(&pos, Color::Blue), TEMPO);
        assert_eq!(evaluate(&pos, Color::Black), -TEMPO);
    }

    #[test]
    fn evaluation_is_antisymmetric_across_random_play() {
        // The invariant the whole design exists to preserve.
        let mut rng = Rng(0xA5A5_1234_DEAD_0001);
        for _ in 0..400 {
            let mut pos = Position::startpos();
            for _ in 0..80 {
                if pos.result().is_some() {
                    break;
                }
                let moves = generate(&pos);
                if moves.is_empty() {
                    break;
                }
                pos.make(moves[(rng.next() % moves.len() as u64) as usize]);
                assert_eq!(
                    evaluate(&pos, Color::Blue),
                    -evaluate(&pos, Color::Black),
                    "antisymmetry broken at {}",
                    pos.to_board_string()
                );
            }
        }
    }

    #[test]
    fn evaluation_never_returns_a_terminal_score() {
        // Purely static: even a position with a piece in the enemy den, or one
        // side wiped out, gets an ordinary positional number. Terminality is the
        // search's business.
        let mut pos = Position::empty();
        pos.place(sq(3, 0), Piece::new(Color::Blue, animal::WOLF));
        let v = evaluate(&pos, Color::Blue);
        assert!(v.abs() < 100_000, "eval leaked a terminal score: {v}");
    }

    #[test]
    fn material_dominates_positional_terms() {
        let mut a = Position::empty();
        a.place(sq(0, 4), Piece::new(Color::Blue, animal::ELEPHANT));
        a.place(sq(6, 4), Piece::new(Color::Black, animal::RAT));
        assert!(evaluate(&a, Color::Blue) > 0);
    }
}
