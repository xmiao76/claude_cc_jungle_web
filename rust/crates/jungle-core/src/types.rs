//! Core value types: colours, animals, pieces, squares and moves.
//!
//! Piece encoding differs deliberately from the Python engine's signed scheme
//! (`+rank` Blue, `-rank` Black, `0` empty). Signed ids force a branch and an
//! `abs` on every access; here a piece is a dense index `colour * 8 + rank - 1`,
//! so it indexes tables directly. [`Piece::to_signed`] converts back at the
//! boundary, which is the only place the signed form is still needed (the
//! golden corpus and the Python facade speak it).

/// Board width: columns `0..7`, left to right.
pub const COLS: usize = 7;
/// Board height: rows `0..9`, top to bottom. Row 0 is Black's back rank.
pub const ROWS: usize = 9;
/// Total squares. 63 fits in a `u64` with a bit to spare, which is what makes
/// every mask, attack set and jump path a single word.
pub const NSQ: usize = COLS * ROWS;

/// A square index, `row * COLS + col`, matching the golden corpus's row-major
/// board string so the two never need reindexing.
pub type Square = u8;

/// Sentinel for "this piece has been captured", stored in `Position::sq_of`.
pub const GONE: Square = 255;

#[inline(always)]
pub const fn sq(col: usize, row: usize) -> Square {
    (row * COLS + col) as Square
}

#[inline(always)]
pub const fn col_of(s: Square) -> usize {
    (s as usize) % COLS
}

#[inline(always)]
pub const fn row_of(s: Square) -> usize {
    (s as usize) / COLS
}

#[derive(Clone, Copy, PartialEq, Eq, Debug, Hash)]
#[repr(u8)]
pub enum Color {
    Blue = 0,
    Black = 1,
}

impl Color {
    #[inline(always)]
    pub const fn flip(self) -> Color {
        match self {
            Color::Blue => Color::Black,
            Color::Black => Color::Blue,
        }
    }

    #[inline(always)]
    pub const fn index(self) -> usize {
        self as usize
    }

    #[inline(always)]
    pub const fn from_index(i: usize) -> Color {
        if i == 0 {
            Color::Blue
        } else {
            Color::Black
        }
    }
}

/// Animal ranks, 1 (Rat) through 8 (Elephant).
pub mod animal {
    pub const RAT: u8 = 1;
    pub const CAT: u8 = 2;
    pub const DOG: u8 = 3;
    pub const WOLF: u8 = 4;
    pub const LEOPARD: u8 = 5;
    pub const TIGER: u8 = 6;
    pub const LION: u8 = 7;
    pub const ELEPHANT: u8 = 8;
}

/// A piece as a dense index: `0..8` Blue Rat..Elephant, `8..16` Black.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Hash)]
pub struct Piece(pub u8);

/// Mailbox value for an empty square.
pub const EMPTY: u8 = 16;

impl Piece {
    #[inline(always)]
    pub const fn new(color: Color, rank: u8) -> Piece {
        Piece((color as u8) * 8 + rank - 1)
    }

    #[inline(always)]
    pub const fn color(self) -> Color {
        if self.0 < 8 {
            Color::Blue
        } else {
            Color::Black
        }
    }

    #[inline(always)]
    pub const fn rank(self) -> u8 {
        (self.0 % 8) + 1
    }

    #[inline(always)]
    pub const fn index(self) -> usize {
        self.0 as usize
    }

    /// The Python engine's signed piece id: `+rank` Blue, `-rank` Black.
    #[inline(always)]
    pub const fn to_signed(self) -> i8 {
        let r = self.rank() as i8;
        if self.0 < 8 {
            r
        } else {
            -r
        }
    }

    /// Inverse of [`Piece::to_signed`]. Panics on `0`, which is not a piece.
    pub const fn from_signed(pid: i8) -> Piece {
        assert!(pid != 0, "0 is the empty square, not a piece");
        if pid > 0 {
            Piece::new(Color::Blue, pid as u8)
        } else {
            Piece::new(Color::Black, (-pid) as u8)
        }
    }
}

/// A move, packed as `from | to << 6`.
///
/// The captured piece is deliberately *not* stored. The Python `Move` carries it
/// and `Board.make_move` trusts it without validation, which makes a stale
/// `captured` field a silent grid-and-hash corruption. Here `make` reads the
/// victim from the mailbox and records it in the undo stack, so the two cannot
/// disagree.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Hash, PartialOrd, Ord)]
pub struct Move(pub u16);

impl Move {
    #[inline(always)]
    pub const fn new(from: Square, to: Square) -> Move {
        Move((from as u16) | ((to as u16) << 6))
    }

    #[inline(always)]
    pub const fn from(self) -> Square {
        (self.0 & 0x3F) as Square
    }

    #[inline(always)]
    pub const fn to(self) -> Square {
        ((self.0 >> 6) & 0x3F) as Square
    }
}

impl core::fmt::Display for Move {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        write!(
            f,
            "{},{}->{},{}",
            col_of(self.from()),
            row_of(self.from()),
            col_of(self.to()),
            row_of(self.to())
        )
    }
}

/// A fixed-capacity move list.
///
/// The theoretical maximum is 8 pieces x (4 steps + 2 jumps) = 48; 64 leaves
/// headroom and keeps the list on the stack, so move generation never allocates.
pub const MAX_MOVES: usize = 64;

#[derive(Clone)]
pub struct MoveList {
    moves: [Move; MAX_MOVES],
    len: usize,
}

impl MoveList {
    #[inline(always)]
    pub fn new() -> MoveList {
        MoveList {
            moves: [Move(0); MAX_MOVES],
            len: 0,
        }
    }

    #[inline(always)]
    pub fn push(&mut self, m: Move) {
        debug_assert!(self.len < MAX_MOVES, "move list overflow");
        self.moves[self.len] = m;
        self.len += 1;
    }

    #[inline(always)]
    pub fn len(&self) -> usize {
        self.len
    }

    #[inline(always)]
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    #[inline(always)]
    pub fn as_slice(&self) -> &[Move] {
        &self.moves[..self.len]
    }

    pub fn sort(&mut self) {
        self.moves[..self.len].sort_unstable();
    }

    #[inline(always)]
    pub fn swap(&mut self, a: usize, b: usize) {
        self.moves.swap(a, b);
    }
}

impl Default for MoveList {
    fn default() -> Self {
        Self::new()
    }
}

impl core::ops::Index<usize> for MoveList {
    type Output = Move;
    #[inline(always)]
    fn index(&self, i: usize) -> &Move {
        &self.moves[i]
    }
}
