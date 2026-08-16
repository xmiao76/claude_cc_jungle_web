//! Jungle (Dou Shou Qi) rules, board representation and move generation.
//!
//! This crate is the rules contract. It is dependency-free, holds no global
//! mutable state, and is verified against two oracles carried over from the
//! Python engine it replaces: the frozen perft counts in `tests/test_perft.py`,
//! and the golden position corpus in `tests/golden/positions.txt.gz`.
//!
//! Board geometry in one line: 7 columns by 9 rows is **63 squares, which fits in
//! a `u64` with a bit to spare**, so every terrain mask, attack set and jump path
//! is a single word.

pub mod bitboard;
pub mod movegen;
pub mod perft;
pub mod position;
pub mod rules;
pub mod types;
pub mod zobrist;

pub use bitboard::{Bitboard, DEN_BLACK, DEN_BLUE, RIVER, TRAPS_BLACK, TRAPS_BLUE};
pub use movegen::{generate, generate_into, generate_noisy};
pub use perft::{perft, perft_divide};
pub use position::Position;
pub use rules::{can_capture, effective_rank};
pub use types::{animal, col_of, row_of, sq, Color, Move, MoveList, Piece, Square, COLS, NSQ, ROWS};
