//! Zobrist keys, built at compile time from a fixed splitmix64 stream.
//!
//! Sixteen distinct pieces get sixteen distinct key slots per square. The Python
//! engine's `_pid_index` mapped Blue with `pid - 1` and Black with `pid + 8`,
//! landing both in `0..8`, so each Blue piece shared a key with the Black piece
//! of complementary rank and two different positions could hash identically.
//! Indexing by [`Piece::index`] here makes that class of bug unrepresentable.
//!
//! The stream need not match Python's Mersenne Twister: nothing persists a hash
//! across runs, and the opening book is built by replay at startup.

use crate::types::{Piece, Square, NSQ};

const fn splitmix64(seed: u64) -> (u64, u64) {
    let next = seed.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = next;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    (next, z ^ (z >> 31))
}

struct Keys {
    piece: [[u64; NSQ]; 16],
    turn: u64,
}

const fn build_keys() -> Keys {
    let mut k = Keys {
        piece: [[0u64; NSQ]; 16],
        turn: 0,
    };
    let mut state = 0x00C0_FFEE_D0D0_1234u64;
    let mut p = 0;
    while p < 16 {
        let mut s = 0;
        while s < NSQ {
            let (next, value) = splitmix64(state);
            state = next;
            k.piece[p][s] = value;
            s += 1;
        }
        p += 1;
    }
    let (_, value) = splitmix64(state);
    k.turn = value;
    k
}

static KEYS: Keys = build_keys();

/// Key for `piece` standing on `square`.
#[inline(always)]
pub fn piece_key(piece: Piece, s: Square) -> u64 {
    KEYS.piece[piece.index()][s as usize]
}

/// XORed into the hash when it is Black to move.
#[inline(always)]
pub fn turn_key() -> u64 {
    KEYS.turn
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::Color;
    use std::collections::HashSet;

    #[test]
    fn every_piece_square_key_is_distinct() {
        let mut seen = HashSet::new();
        for p in 0..16u8 {
            for s in 0..NSQ as u8 {
                assert!(seen.insert(piece_key(Piece(p), s)), "duplicate key");
            }
        }
        assert!(seen.insert(turn_key()), "turn key collides with a piece key");
        assert_eq!(seen.len(), 16 * NSQ + 1);
    }

    #[test]
    fn complementary_ranks_do_not_share_a_slot() {
        // The exact shape of the Python bug: Blue rank r and Black rank 9-r.
        for rank in 1..=8u8 {
            let b = Piece::new(Color::Blue, rank);
            let k = Piece::new(Color::Black, 9 - rank);
            assert_ne!(b.index(), k.index());
            assert_ne!(piece_key(b, 30), piece_key(k, 30));
        }
    }

    #[test]
    fn no_key_is_zero() {
        for p in 0..16u8 {
            for s in 0..NSQ as u8 {
                assert_ne!(piece_key(Piece(p), s), 0);
            }
        }
    }
}
