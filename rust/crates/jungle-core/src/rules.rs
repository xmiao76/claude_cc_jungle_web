//! Capture legality and effective rank.
//!
//! The order of the checks in [`can_capture`] is load-bearing and is the one
//! thing here that must not be "simplified". The project's one recorded rules
//! defect was the Elephant-cannot-take-Rat rejection sitting *before* the trap
//! override, which made a Rat in its own trap untouchable by an Elephant that it
//! could take back. Water boundary, then trapped defender, then rank.

use crate::bitboard::{bb, is_river, weakening_traps};
use crate::types::{animal, Piece, Square};

/// A piece standing in the *enemy's* home traps fights at rank 0.
///
/// This applies to the **defender only**. An attacker standing in enemy traps
/// keeps its real rank: a trapped piece is vulnerable, not disarmed.
#[inline(always)]
pub fn effective_rank(piece: Piece, s: Square) -> u8 {
    if weakening_traps(piece.color().index()) & bb(s) != 0 {
        0
    } else {
        piece.rank()
    }
}

/// Can `attacker` on `atk_sq` take `defender` on `def_sq`?
#[inline]
pub fn can_capture(attacker: Piece, defender: Piece, atk_sq: Square, def_sq: Square) -> bool {
    // (1) Must be enemies.
    if attacker.color() == defender.color() {
        return false;
    }

    // (2) No capture across the water/land boundary, in either direction. This
    //     is what makes a Rat in the river invulnerable to land pieces, and what
    //     stops a river Rat from taking an Elephant on the bank.
    if is_river(atk_sq) != is_river(def_sq) {
        return false;
    }

    // (3) A defender in the attacker's home traps has rank 0 and falls to
    //     anything — including an Elephant taking a Rat. Traps are never river
    //     squares, so this cannot bypass (2).
    if effective_rank(defender, def_sq) == 0 {
        return true;
    }

    // (4) Rank comparison, with the Rat/Elephant exception. The attacker always
    //     uses its real rank.
    let a = attacker.rank();
    let d = defender.rank();
    if a == animal::RAT && d == animal::ELEPHANT {
        return true;
    }
    if a == animal::ELEPHANT && d == animal::RAT {
        return false;
    }
    a >= d
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bitboard::{DEN_BLACK, TRAPS_BLACK};
    use crate::types::{sq, Color};

    fn blue(rank: u8) -> Piece {
        Piece::new(Color::Blue, rank)
    }
    fn black(rank: u8) -> Piece {
        Piece::new(Color::Black, rank)
    }

    #[test]
    fn higher_or_equal_rank_captures() {
        let a = sq(0, 4);
        let d = sq(0, 5);
        assert!(can_capture(blue(5), black(4), a, d));
        assert!(can_capture(blue(4), black(4), a, d), "equal ranks trade");
        assert!(!can_capture(blue(3), black(4), a, d));
    }

    #[test]
    fn rat_beats_elephant_but_not_the_reverse() {
        let a = sq(0, 4);
        let d = sq(0, 5);
        assert!(can_capture(blue(animal::RAT), black(animal::ELEPHANT), a, d));
        assert!(!can_capture(blue(animal::ELEPHANT), black(animal::RAT), a, d));
    }

    #[test]
    fn captures_never_cross_the_water_boundary() {
        let water = sq(1, 4);
        let land = sq(0, 4);
        assert!(!can_capture(blue(animal::RAT), black(animal::ELEPHANT), water, land));
        assert!(!can_capture(blue(animal::ELEPHANT), black(animal::RAT), land, water));
        // Rat vs Rat on the same terrain is fine.
        assert!(can_capture(blue(animal::RAT), black(animal::RAT), water, sq(2, 4)));
    }

    #[test]
    fn a_trapped_defender_falls_to_anything() {
        // A Blue piece standing in Black's home traps is the weakened one.
        for trap in [sq(2, 0), sq(4, 0), sq(3, 1)] {
            assert!(TRAPS_BLACK & bb(trap) != 0);
            for victim in 1..=8u8 {
                for attacker in 1..=8u8 {
                    assert!(
                        can_capture(black(attacker), blue(victim), DEN_BLACK, trap),
                        "black {attacker} should take trapped blue {victim}"
                    );
                }
            }
        }
    }

    #[test]
    fn a_trapped_attacker_keeps_its_rank() {
        // Blue Rat inside Black's trap still cannot be taken by an Elephant that
        // is itself standing in Blue's trap -- the trap weakens defence only.
        let blue_trap = sq(3, 7);
        let adjacent = sq(3, 6);
        assert!(!can_capture(
            black(animal::ELEPHANT),
            blue(animal::RAT),
            blue_trap,
            adjacent
        ));
        // ... and it still strikes at full power.
        assert!(can_capture(
            black(animal::ELEPHANT),
            blue(animal::LION),
            blue_trap,
            adjacent
        ));
    }

    #[test]
    fn own_colour_never_captures() {
        assert!(!can_capture(blue(8), blue(1), sq(0, 4), sq(0, 5)));
    }
}
