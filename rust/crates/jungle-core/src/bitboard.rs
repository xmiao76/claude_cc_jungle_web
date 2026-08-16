//! Board geometry as bitboards, plus the precomputed adjacency and jump tables.
//!
//! Everything here is built by `const fn` from the same `(col, row)` definitions
//! the Python engine uses, rather than transcribed as square indices. A hand-typed
//! mask is a silent rules change waiting to happen; a derived one cannot drift.

use crate::types::{sq, Square, COLS, NSQ, ROWS};

/// A set of squares. Bit `s` is square `s`; bit 63 is always clear.
pub type Bitboard = u64;

pub const EMPTY_BB: Bitboard = 0;

#[inline(always)]
pub const fn bb(s: Square) -> Bitboard {
    1u64 << s
}

// ---------------------------------------------------------------------------
// Terrain, defined exactly as config.py defines it
// ---------------------------------------------------------------------------

/// Black's den, top of the board.
pub const DEN_BLACK: Square = sq(3, 0);
/// Blue's den, bottom of the board.
pub const DEN_BLUE: Square = sq(3, 8);

/// Black's home traps: the three squares around Black's den. A *Blue* piece
/// standing here is the one weakened, hence "the attacker's traps" in the rules.
pub const TRAPS_BLACK: Bitboard = bb(sq(2, 0)) | bb(sq(4, 0)) | bb(sq(3, 1));
/// Blue's home traps.
pub const TRAPS_BLUE: Bitboard = bb(sq(2, 8)) | bb(sq(4, 8)) | bb(sq(3, 7));

const fn build_river() -> Bitboard {
    let mut m = 0u64;
    let mut r = 3;
    while r <= 5 {
        let mut i = 0;
        while i < 4 {
            let c = [1usize, 2, 4, 5][i];
            m |= bb(sq(c, r));
            i += 1;
        }
        r += 1;
    }
    m
}

/// The twelve river squares: two 2-wide by 3-tall blocks, leaving columns
/// 0, 3 and 6 as land bridges — which is what makes the jump geometry work.
pub const RIVER: Bitboard = build_river();

/// Every square on the board.
pub const ALL: Bitboard = (1u64 << NSQ) - 1;

#[inline(always)]
pub const fn is_river(s: Square) -> bool {
    RIVER & bb(s) != 0
}

const fn is_river_cr(c: usize, r: usize) -> bool {
    is_river(sq(c, r))
}

/// The den this colour may not enter (its own).
#[inline(always)]
pub const fn own_den(color_index: usize) -> Square {
    if color_index == 0 {
        DEN_BLUE
    } else {
        DEN_BLACK
    }
}

/// The den this colour wins by entering (the enemy's).
#[inline(always)]
pub const fn enemy_den(color_index: usize) -> Square {
    if color_index == 0 {
        DEN_BLACK
    } else {
        DEN_BLUE
    }
}

/// The traps that reduce this colour's pieces to rank 0 (the enemy's home traps).
#[inline(always)]
pub const fn weakening_traps(color_index: usize) -> Bitboard {
    if color_index == 0 {
        TRAPS_BLACK
    } else {
        TRAPS_BLUE
    }
}

/// The traps around this colour's *own* den — where an enemy piece stands at
/// rank 0, one step from entering.
#[inline(always)]
pub const fn home_traps(color_index: usize) -> Bitboard {
    if color_index == 0 {
        TRAPS_BLUE
    } else {
        TRAPS_BLACK
    }
}

// ---------------------------------------------------------------------------
// Adjacency
// ---------------------------------------------------------------------------

const fn build_adjacent() -> [Bitboard; NSQ] {
    let mut t = [0u64; NSQ];
    let mut r = 0;
    while r < ROWS {
        let mut c = 0;
        while c < COLS {
            let s = sq(c, r);
            let mut m = 0u64;
            if r > 0 {
                m |= bb(sq(c, r - 1));
            }
            if r + 1 < ROWS {
                m |= bb(sq(c, r + 1));
            }
            if c > 0 {
                m |= bb(sq(c - 1, r));
            }
            if c + 1 < COLS {
                m |= bb(sq(c + 1, r));
            }
            t[s as usize] = m;
            c += 1;
        }
        r += 1;
    }
    t
}

/// Orthogonal neighbours of each square.
///
/// Note this cannot be a shift-and-mask like a chess engine's: 7 does not divide
/// 64, so `<< 1` walks off the end of a row into the next one. A table sidesteps
/// the whole class of wraparound bug.
pub static ADJACENT: [Bitboard; NSQ] = build_adjacent();

// ---------------------------------------------------------------------------
// River jumps
// ---------------------------------------------------------------------------

/// The most jumps available from any one square (the land bridges at column 3
/// can leap either way).
pub const MAX_JUMPS: usize = 2;

pub struct JumpTable {
    /// Landing square of each jump.
    pub to: [[Square; MAX_JUMPS]; NSQ],
    /// River squares crossed. A Rat on any of them blocks the leap.
    pub path: [[Bitboard; MAX_JUMPS]; NSQ],
    /// True for a row-axis leap (3 river squares), which only the Lion may make.
    pub vertical: [[bool; MAX_JUMPS]; NSQ],
    /// Number of jumps from each square.
    pub n: [u8; NSQ],
}

const fn build_jumps() -> JumpTable {
    let mut t = JumpTable {
        to: [[0u8; MAX_JUMPS]; NSQ],
        path: [[0u64; MAX_JUMPS]; NSQ],
        vertical: [[false; MAX_JUMPS]; NSQ],
        n: [0u8; NSQ],
    };

    let mut r = 0isize;
    while r < ROWS as isize {
        let mut c = 0isize;
        while c < COLS as isize {
            if is_river_cr(c as usize, r as usize) {
                c += 1;
                continue;
            }
            let s = sq(c as usize, r as usize) as usize;

            let mut d = 0;
            while d < 4 {
                let dc: isize = [0, 0, -1, 1][d];
                let dr: isize = [-1, 1, 0, 0][d];

                let nc = c + dc;
                let nr = r + dr;
                // The leap only exists if we are standing on a river bank.
                if nc >= 0
                    && nc < COLS as isize
                    && nr >= 0
                    && nr < ROWS as isize
                    && is_river_cr(nc as usize, nr as usize)
                {
                    let mut wc = nc;
                    let mut wr = nr;
                    let mut path = 0u64;
                    while wc >= 0
                        && wc < COLS as isize
                        && wr >= 0
                        && wr < ROWS as isize
                        && is_river_cr(wc as usize, wr as usize)
                    {
                        path |= bb(sq(wc as usize, wr as usize));
                        wc += dc;
                        wr += dr;
                    }
                    // Land on the first non-river square, if it is on the board.
                    if wc >= 0 && wc < COLS as isize && wr >= 0 && wr < ROWS as isize {
                        let k = t.n[s] as usize;
                        t.to[s][k] = sq(wc as usize, wr as usize);
                        t.path[s][k] = path;
                        t.vertical[s][k] = dc == 0;
                        t.n[s] = (k + 1) as u8;
                    }
                }
                d += 1;
            }
            c += 1;
        }
        r += 1;
    }
    t
}

pub static JUMPS: JumpTable = build_jumps();

/// Iterate set squares of a bitboard, lowest first.
pub struct BitIter(pub Bitboard);

impl Iterator for BitIter {
    type Item = Square;
    #[inline(always)]
    fn next(&mut self) -> Option<Square> {
        if self.0 == 0 {
            None
        } else {
            let s = self.0.trailing_zeros() as Square;
            self.0 &= self.0 - 1;
            Some(s)
        }
    }
}

#[inline(always)]
pub fn iter_squares(b: Bitboard) -> BitIter {
    BitIter(b)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn river_has_twelve_squares_in_the_right_place() {
        assert_eq!(RIVER.count_ones(), 12);
        for r in 3..=5 {
            for c in [1, 2, 4, 5] {
                assert!(is_river(sq(c, r)), "({c},{r}) should be river");
            }
            for c in [0, 3, 6] {
                assert!(!is_river(sq(c, r)), "({c},{r}) is a land bridge");
            }
        }
    }

    #[test]
    fn dens_and_traps_are_distinct_and_off_the_river() {
        assert_eq!(TRAPS_BLACK.count_ones(), 3);
        assert_eq!(TRAPS_BLUE.count_ones(), 3);
        assert_eq!(TRAPS_BLACK & TRAPS_BLUE, 0);
        // Rule (3) of can_capture relies on this: a trap can never be water, so
        // the trap override cannot smuggle a capture across the water boundary.
        assert_eq!((TRAPS_BLACK | TRAPS_BLUE) & RIVER, 0);
        assert_eq!(bb(DEN_BLUE) & RIVER, 0);
        assert_eq!(bb(DEN_BLACK) & RIVER, 0);
    }

    #[test]
    fn adjacency_never_wraps_across_a_row() {
        for r in 0..ROWS {
            let left = ADJACENT[sq(0, r) as usize];
            let right = ADJACENT[sq(COLS - 1, r) as usize];
            // Column 0 has no western neighbour, column 6 no eastern one.
            assert_eq!(left.count_ones(), if r == 0 || r == ROWS - 1 { 2 } else { 3 });
            assert_eq!(right.count_ones(), if r == 0 || r == ROWS - 1 { 2 } else { 3 });
        }
        assert_eq!(ADJACENT[sq(3, 4) as usize].count_ones(), 4);
    }

    #[test]
    fn jump_table_matches_the_python_engine() {
        // 17 source squares, 20 entries total.
        let sources: usize = JUMPS.n.iter().filter(|&&n| n > 0).count();
        let entries: usize = JUMPS.n.iter().map(|&n| n as usize).sum();
        assert_eq!(sources, 17, "expected 17 jump origins");
        assert_eq!(entries, 20, "expected 20 jump entries");

        // Column-3 bridges leap both ways.
        for r in 3..=5 {
            assert_eq!(JUMPS.n[sq(3, r) as usize], 2);
        }
        // Horizontal leaps cross 2 river squares, vertical ones cross 3.
        for s in 0..NSQ {
            for k in 0..JUMPS.n[s] as usize {
                let crossed = JUMPS.path[s][k].count_ones();
                if JUMPS.vertical[s][k] {
                    assert_eq!(crossed, 3, "vertical leap from {s}");
                } else {
                    assert_eq!(crossed, 2, "horizontal leap from {s}");
                }
                assert_eq!(JUMPS.path[s][k] & !RIVER, 0, "path must be all river");
            }
        }
    }
}
