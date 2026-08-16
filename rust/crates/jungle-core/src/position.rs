//! The board state: mailbox + piece list + occupancy bitboards + incremental hash.
//!
//! Jungle has at most sixteen pieces and every one of them is *unique* — there is
//! exactly one Blue Rat, one Black Lion, and so on. That is unusual and it is
//! worth exploiting: `sq_of` is a complete piece list indexed directly by piece,
//! with no scanning and no duplicate handling, and the whole state is about a
//! hundred bytes, so make/unmake is a handful of word writes.

use crate::bitboard::{bb, iter_squares, Bitboard, DEN_BLACK, DEN_BLUE};
use crate::movegen::generate;
use crate::types::{animal, sq, Color, Move, Piece, Square, COLS, EMPTY, GONE, NSQ, ROWS};
use crate::zobrist::{piece_key, turn_key};

/// Plies without a capture before the game is drawn (50 full moves).
pub const FIFTY_MOVE_PLIES: u16 = 100;

/// Marks a null move in the undo stack. Distinct from every piece index and from
/// `EMPTY`, so `unmake` can assert it is not handed one by mistake.
const NULL_MARKER: u8 = 255;

#[derive(Clone, Copy)]
struct Undo {
    mv: Move,
    captured: u8,
    halfmove: u16,
}

#[derive(Clone)]
pub struct Position {
    mailbox: [u8; NSQ],
    sq_of: [Square; 16],
    occ: [Bitboard; 2],
    /// Rats of both colours. Jump blocking is `path & rats`, and only Rats can
    /// stand in the river, so this single word answers the whole rule.
    rats: Bitboard,
    stm: Color,
    hash: u64,
    halfmove: u16,
    undo: Vec<Undo>,
    /// Keys of the positions already left behind, for repetition detection.
    history: Vec<u64>,
}

/// The starting layout. Corners hold Lion and Tiger; rows 1-2 and 6-7 are
/// top-to-bottom mirrors, so each player's Elephant sits on their own left.
pub const STARTING_POSITION: [(usize, usize, Color, u8); 16] = [
    (0, 0, Color::Black, animal::LION),
    (6, 0, Color::Black, animal::TIGER),
    (1, 1, Color::Black, animal::DOG),
    (5, 1, Color::Black, animal::CAT),
    (0, 2, Color::Black, animal::RAT),
    (2, 2, Color::Black, animal::LEOPARD),
    (4, 2, Color::Black, animal::WOLF),
    (6, 2, Color::Black, animal::ELEPHANT),
    (0, 6, Color::Blue, animal::ELEPHANT),
    (2, 6, Color::Blue, animal::WOLF),
    (4, 6, Color::Blue, animal::LEOPARD),
    (6, 6, Color::Blue, animal::RAT),
    (1, 7, Color::Blue, animal::CAT),
    (5, 7, Color::Blue, animal::DOG),
    (0, 8, Color::Blue, animal::TIGER),
    (6, 8, Color::Blue, animal::LION),
];

impl Position {
    /// An empty board, Blue to move.
    pub fn empty() -> Position {
        Position {
            mailbox: [EMPTY; NSQ],
            sq_of: [GONE; 16],
            occ: [0; 2],
            rats: 0,
            stm: Color::Blue,
            hash: 0,
            halfmove: 0,
            undo: Vec::with_capacity(256),
            history: Vec::with_capacity(256),
        }
    }

    /// The starting position, Blue to move.
    pub fn startpos() -> Position {
        let mut p = Position::empty();
        for &(c, r, color, rank) in STARTING_POSITION.iter() {
            p.place(sq(c, r), Piece::new(color, rank));
        }
        p
    }

    /// Put a piece on an empty square. Panics if the square or the piece is
    /// already in use — the same guard `Board.place` applies, and for the same
    /// reason: a duplicate silently desynchronises the mailbox from the index.
    pub fn place(&mut self, s: Square, piece: Piece) {
        assert!(
            self.mailbox[s as usize] == EMPTY,
            "square {s} is already occupied"
        );
        assert!(
            self.sq_of[piece.index()] == GONE,
            "piece {:?} is already on the board",
            piece
        );
        self.mailbox[s as usize] = piece.0;
        self.sq_of[piece.index()] = s;
        self.occ[piece.color().index()] |= bb(s);
        if piece.rank() == animal::RAT {
            self.rats |= bb(s);
        }
        self.hash ^= piece_key(piece, s);
    }

    #[inline(always)]
    pub fn piece_at(&self, s: Square) -> Option<Piece> {
        let v = self.mailbox[s as usize];
        if v == EMPTY {
            None
        } else {
            Some(Piece(v))
        }
    }

    #[inline(always)]
    pub fn square_of(&self, piece: Piece) -> Option<Square> {
        let s = self.sq_of[piece.index()];
        if s == GONE {
            None
        } else {
            Some(s)
        }
    }

    #[inline(always)]
    pub fn side_to_move(&self) -> Color {
        self.stm
    }

    pub fn set_side_to_move(&mut self, c: Color) {
        self.stm = c;
    }

    #[inline(always)]
    pub fn occupancy(&self, c: Color) -> Bitboard {
        self.occ[c.index()]
    }

    #[inline(always)]
    pub fn all_occupancy(&self) -> Bitboard {
        self.occ[0] | self.occ[1]
    }

    #[inline(always)]
    pub fn rats(&self) -> Bitboard {
        self.rats
    }

    #[inline(always)]
    pub fn alive_count(&self, c: Color) -> u32 {
        self.occ[c.index()].count_ones()
    }

    /// The full position key: pieces, plus side to move.
    #[inline(always)]
    pub fn key(&self) -> u64 {
        if self.stm == Color::Black {
            self.hash ^ turn_key()
        } else {
            self.hash
        }
    }

    #[inline(always)]
    pub fn halfmove_clock(&self) -> u16 {
        self.halfmove
    }

    pub fn set_halfmove_clock(&mut self, v: u16) {
        self.halfmove = v;
    }

    #[inline(always)]
    pub fn ply(&self) -> usize {
        self.undo.len()
    }

    // -----------------------------------------------------------------
    // Make / unmake
    // -----------------------------------------------------------------

    pub fn make(&mut self, mv: Move) {
        let from = mv.from();
        let to = mv.to();
        let mover = Piece(self.mailbox[from as usize]);
        let captured = self.mailbox[to as usize];

        self.history.push(self.key());
        self.undo.push(Undo {
            mv,
            captured,
            halfmove: self.halfmove,
        });

        let mover_side = mover.color().index();
        let from_bb = bb(from);
        let to_bb = bb(to);
        let is_rat = mover.rank() == animal::RAT;

        self.hash ^= piece_key(mover, from);
        self.mailbox[from as usize] = EMPTY;
        self.occ[mover_side] &= !from_bb;

        if captured != EMPTY {
            let victim = Piece(captured);
            self.hash ^= piece_key(victim, to);
            self.occ[victim.color().index()] &= !to_bb;
            if victim.rank() == animal::RAT {
                self.rats &= !to_bb;
            }
            self.sq_of[victim.index()] = GONE;
            self.halfmove = 0;
        } else {
            self.halfmove += 1;
        }

        self.mailbox[to as usize] = mover.0;
        self.hash ^= piece_key(mover, to);
        self.occ[mover_side] |= to_bb;
        self.sq_of[mover.index()] = to;
        if is_rat {
            self.rats = (self.rats & !from_bb) | to_bb;
        }

        self.stm = self.stm.flip();
    }

    /// Pass the turn without moving. Search-only: there is no such move in the
    /// rules, and null-move pruning is the only caller.
    ///
    /// It participates in repetition history and bumps the halfmove clock, both
    /// matching the Python engine, so a null-move line cannot accidentally hide a
    /// repetition from the search above it.
    pub fn make_null(&mut self) {
        self.history.push(self.key());
        self.undo.push(Undo {
            mv: Move(0),
            captured: NULL_MARKER,
            halfmove: self.halfmove,
        });
        self.halfmove += 1;
        self.stm = self.stm.flip();
    }

    pub fn unmake_null(&mut self) {
        let u = self.undo.pop().expect("unmake_null with no null to undo");
        debug_assert_eq!(u.captured, NULL_MARKER, "unmake_null on a real move");
        self.history.pop();
        self.halfmove = u.halfmove;
        self.stm = self.stm.flip();
    }

    pub fn unmake(&mut self) {
        let Undo {
            mv,
            captured,
            halfmove,
        } = self.undo.pop().expect("unmake with no move to undo");
        self.history.pop();

        let from = mv.from();
        let to = mv.to();
        let mover = Piece(self.mailbox[to as usize]);

        let mover_side = mover.color().index();
        let from_bb = bb(from);
        let to_bb = bb(to);

        self.hash ^= piece_key(mover, to);
        self.occ[mover_side] &= !to_bb;
        self.mailbox[from as usize] = mover.0;
        self.hash ^= piece_key(mover, from);
        self.occ[mover_side] |= from_bb;
        self.sq_of[mover.index()] = from;
        if mover.rank() == animal::RAT {
            self.rats = (self.rats & !to_bb) | from_bb;
        }

        if captured != EMPTY {
            let victim = Piece(captured);
            self.mailbox[to as usize] = captured;
            self.hash ^= piece_key(victim, to);
            self.occ[victim.color().index()] |= to_bb;
            if victim.rank() == animal::RAT {
                self.rats |= to_bb;
            }
            self.sq_of[victim.index()] = to;
        } else {
            self.mailbox[to as usize] = EMPTY;
        }

        self.halfmove = halfmove;
        self.stm = self.stm.flip();
    }

    // -----------------------------------------------------------------
    // Terminal conditions
    // -----------------------------------------------------------------

    /// The decided winner, from the position alone.
    ///
    /// Both win conditions are positional, which is why this needs no memory of
    /// how the position arose: nothing but a den entry can put a piece on an
    /// enemy den (and the game stops the instant it happens), and capture-all is
    /// a piece count.
    pub fn result(&self) -> Option<Color> {
        if let Some(p) = self.piece_at(DEN_BLACK) {
            if p.color() == Color::Blue {
                return Some(Color::Blue);
            }
        }
        if let Some(p) = self.piece_at(DEN_BLUE) {
            if p.color() == Color::Black {
                return Some(Color::Black);
            }
        }
        if self.occ[Color::Blue.index()] == 0 {
            return Some(Color::Black);
        }
        if self.occ[Color::Black.index()] == 0 {
            return Some(Color::Blue);
        }
        None
    }

    #[inline(always)]
    pub fn is_fifty_move_draw(&self) -> bool {
        self.halfmove >= FIFTY_MOVE_PLIES
    }

    /// Has this exact position occurred earlier in the game?
    ///
    /// Two-fold, matching the Python engine, and a *search* draw score rather
    /// than a game rule. The scan stops at the last capture: everything before
    /// it had different material, so it cannot repeat the position now.
    pub fn is_repetition(&self) -> bool {
        let key = self.key();
        let back = (self.halfmove as usize).min(self.history.len());
        self.history[self.history.len() - back..].contains(&key)
    }

    pub fn has_legal_moves(&self) -> bool {
        !generate(self).is_empty()
    }

    /// Terminal in the same sense as `GameState.is_terminal`: decided, drawn by
    /// the 50-move rule, or the side to move has no move at all.
    pub fn is_terminal(&self) -> bool {
        self.result().is_some() || self.is_fifty_move_draw() || !self.has_legal_moves()
    }

    /// The winner, with the same precedence as `GameState.get_winner`: an
    /// explicit result beats the 50-move draw, which beats stalemate. Having no
    /// legal move is a **loss** for the side to move.
    pub fn winner(&self) -> Option<Color> {
        if let Some(w) = self.result() {
            return Some(w);
        }
        if self.is_fifty_move_draw() {
            return None;
        }
        if !self.has_legal_moves() {
            return Some(self.stm.flip());
        }
        None
    }

    // -----------------------------------------------------------------
    // Serialization (the golden corpus's board string)
    // -----------------------------------------------------------------

    /// 63 characters, row-major. Blue is `A`..`H` by rank, Black `a`..`h`.
    pub fn to_board_string(&self) -> String {
        let mut out = String::with_capacity(NSQ);
        for r in 0..ROWS {
            for c in 0..COLS {
                match self.piece_at(sq(c, r)) {
                    None => out.push('.'),
                    Some(p) => {
                        let base = if p.color() == Color::Blue { b'A' } else { b'a' };
                        out.push((base + p.rank() - 1) as char);
                    }
                }
            }
        }
        out
    }

    pub fn from_board_string(text: &str) -> Result<Position, String> {
        let bytes = text.as_bytes();
        if bytes.len() != NSQ {
            return Err(format!("expected {NSQ} chars, got {}", bytes.len()));
        }
        let mut p = Position::empty();
        for (i, &ch) in bytes.iter().enumerate() {
            if ch == b'.' {
                continue;
            }
            let (color, rank) = if ch.is_ascii_uppercase() {
                (Color::Blue, ch - b'A' + 1)
            } else if ch.is_ascii_lowercase() {
                (Color::Black, ch - b'a' + 1)
            } else {
                return Err(format!("bad character {:?} at {i}", ch as char));
            };
            if !(1..=8).contains(&rank) {
                return Err(format!("bad rank {rank} at {i}"));
            }
            p.place(i as Square, Piece::new(color, rank));
        }
        Ok(p)
    }

    /// Recompute the hash from scratch; the incremental one must always match.
    pub fn recomputed_hash(&self) -> u64 {
        let mut h = 0u64;
        for s in iter_squares(self.all_occupancy()) {
            h ^= piece_key(self.piece_at(s).unwrap(), s);
        }
        h
    }

    /// Assert the mailbox, the piece list, the occupancy bitboards, the rat
    /// bitboard and the incremental hash all agree. The Rust counterpart of
    /// `tests.helpers.assert_board_consistent`.
    pub fn assert_consistent(&self) {
        assert_eq!(self.hash, self.recomputed_hash(), "incremental hash diverged");

        let mut occ = [0u64; 2];
        let mut rats = 0u64;
        for s in 0..NSQ as Square {
            if let Some(p) = self.piece_at(s) {
                occ[p.color().index()] |= bb(s);
                if p.rank() == animal::RAT {
                    rats |= bb(s);
                }
                assert_eq!(self.sq_of[p.index()], s, "piece list disagrees for {p:?}");
            }
        }
        assert_eq!(occ, self.occ, "occupancy disagrees with the mailbox");
        assert_eq!(rats, self.rats, "rat bitboard disagrees with the mailbox");

        for i in 0..16u8 {
            let s = self.sq_of[i as usize];
            if s != GONE {
                assert_eq!(
                    self.mailbox[s as usize], i,
                    "piece list points at the wrong square for piece {i}"
                );
            }
        }
    }
}

impl core::fmt::Debug for Position {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        writeln!(f, "{} {:?}", self.to_board_string(), self.stm)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::generate;

    /// xorshift64*, so the tests need no `rand` dependency.
    struct Rng(u64);
    impl Rng {
        fn next(&mut self) -> u64 {
            self.0 ^= self.0 >> 12;
            self.0 ^= self.0 << 25;
            self.0 ^= self.0 >> 27;
            self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
        }
        fn below(&mut self, n: usize) -> usize {
            (self.next() % n as u64) as usize
        }
    }

    #[test]
    fn startpos_has_sixteen_pieces_and_a_symmetric_split() {
        let p = Position::startpos();
        assert_eq!(p.alive_count(Color::Blue), 8);
        assert_eq!(p.alive_count(Color::Black), 8);
        assert_eq!(p.side_to_move(), Color::Blue);
        p.assert_consistent();
    }

    #[test]
    fn board_string_round_trips_through_random_play() {
        let mut rng = Rng(0x1234_5678_9ABC_DEF0);
        for _ in 0..200 {
            let mut p = Position::startpos();
            for _ in 0..60 {
                if p.result().is_some() {
                    break;
                }
                let moves = generate(&p);
                if moves.is_empty() {
                    break;
                }
                p.make(moves[rng.below(moves.len())]);
                let text = p.to_board_string();
                let rebuilt = Position::from_board_string(&text).unwrap();
                assert_eq!(rebuilt.to_board_string(), text);
                // The hash depends only on the pieces, so a rebuild must match.
                assert_eq!(rebuilt.recomputed_hash(), p.recomputed_hash());
            }
        }
    }

    #[test]
    fn bounded_repetition_scan_equals_a_full_history_scan() {
        // `is_repetition` only looks back as far as the last capture, on the
        // argument that earlier positions had different material and so cannot
        // repeat the current one. That is a real change to the algorithm the
        // Python engine uses (it scans the whole history), so it is checked
        // against the unbounded scan rather than assumed.
        let mut rng = Rng(0xDEAD_BEEF_0BAD_F00D);
        let mut checked = 0usize;
        let mut repetitions = 0usize;

        for _ in 0..300 {
            let mut p = Position::startpos();
            for _ in 0..120 {
                if p.result().is_some() {
                    break;
                }
                let moves = generate(&p);
                if moves.is_empty() {
                    break;
                }
                // Bias hard toward quiet moves so lines actually repeat.
                let quiet: Vec<_> = moves
                    .as_slice()
                    .iter()
                    .copied()
                    .filter(|m| p.piece_at(m.to()).is_none())
                    .collect();
                let pool = if !quiet.is_empty() && rng.next() % 16 != 0 {
                    quiet
                } else {
                    moves.as_slice().to_vec()
                };
                p.make(pool[rng.below(pool.len())]);

                let key = p.key();
                let full = p.history.contains(&key);
                assert_eq!(p.is_repetition(), full, "bounded scan disagreed");
                checked += 1;
                if full {
                    repetitions += 1;
                }
            }
        }

        // A run that never repeated anything would pass vacuously.
        assert!(checked > 10_000, "not enough positions checked: {checked}");
        assert!(repetitions > 100, "too few repetitions seen: {repetitions}");
    }

    #[test]
    fn fifty_move_clock_resets_only_on_a_capture() {
        let mut p = Position::startpos();
        let moves = generate(&p);
        let quiet = moves
            .as_slice()
            .iter()
            .find(|m| p.piece_at(m.to()).is_none())
            .copied()
            .unwrap();
        p.make(quiet);
        assert_eq!(p.halfmove_clock(), 1);
        p.unmake();
        assert_eq!(p.halfmove_clock(), 0);
    }
}
