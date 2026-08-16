//! The single game session behind the browser bridge.
//!
//! A port of `engine-python/web_api.py`, shape for shape. The UI, the coordinate
//! helpers and the Playwright suite are all written against that protocol, so
//! reproducing it exactly is what lets the engine underneath be replaced without
//! touching any of them — and what makes the swap checkable, since the same
//! assertions can be run against either backend.
//!
//! Deliberately host-agnostic: no `wasm_bindgen`, no JS types, everything in and
//! out as `String`. That is not tidiness, it is testability — `cargo test` then
//! covers the whole protocol on the host, where a browser harness would only ever
//! be run by hand.

use jungle_core::movegen::generate;
use jungle_core::position::Position;
use jungle_core::types::{col_of, row_of, sq, Color, Move, Piece, COLS, ROWS};
use jungle_search::{Limits, Searcher};

use crate::json;

/// Per-difficulty AI budgets in milliseconds.
///
/// Easy and Medium are depth-limited — the desktop engine measured the Python
/// and Rust searches statistically indistinguishable at equal depth, so reusing
/// the old depths reproduces the old difficulty exactly rather than approximately
/// at whatever node budget happened to match. These milliseconds are the
/// wall-clock *ceiling* on top of that depth, and unlike the Python bridge (where
/// Easy and Medium set a time limit of 999,999 seconds and were bounded by
/// nothing at all) they are now real.
pub const AI_TIME_BUDGET_MS: [u64; 3] = [1000, 2000, 2500];

/// Fixed search depths for Easy and Medium. Hard runs on the clock.
pub const DEPTH_LIMIT: [Option<i32>; 3] = [Some(3), Some(5), None];

/// Transposition table per side. `TranspositionTable::new` rounds down to a power
/// of two and then halves it, so this is 32 MiB each, 64 MiB in total.
const TT_MEGABYTES: usize = 64;

const ANIMAL_NAMES: [&str; 9] = [
    "", "Rat", "Cat", "Dog", "Wolf", "Leopard", "Tiger", "Lion", "Elephant",
];

const WIN_REASON_DEN: &str = "den";
const WIN_REASON_ELIMINATION: &str = "elimination";
const WIN_REASON_STALEMATE: &str = "stalemate";
const DRAW_REASON_FIFTY: &str = "fifty_move";

/// The engine version reported to the UI. Bump on an engine change that ships.
///
/// 2.0 — the Rust/wasm port replacing the Python engine.
/// 2.1 — `use_improving` (+26 Elo) and `use_tt_eval` (+4.6% nps). See
///       `rust/STRENGTH.md` for what was measured and rejected alongside them.
pub const ENGINE_VERSION: &str = "2.1";

/// What the Rust `Move` deliberately does not carry.
///
/// `Move` is a packed from/to pair with no captured field, because the Python
/// engine's equivalent carried one and `make_move` trusted it unvalidated, so a
/// stale field silently corrupted the hash. The bridge still has to report
/// captures and render a history line, so it records them here instead — a
/// journal beside the position, exactly as the Python session's `history` and
/// `mover_history` were.
#[derive(Clone, Copy)]
struct MoveRecord {
    mv: Move,
    /// Signed piece id of the victim, 0 for a quiet move.
    captured: i32,
    /// Signed piece id of the piece that moved.
    mover: i32,
}

pub struct Session {
    pos: Position,
    difficulty: usize,
    /// One searcher per colour, long-lived. They must outlive a single move: the
    /// transposition table and the history heuristics are worth carrying across
    /// the moves of one game, and rebuilding them per move throws that away.
    searchers: [Searcher; 2],
    history: Vec<MoveRecord>,
}

impl Default for Session {
    fn default() -> Session {
        Session::new()
    }
}

impl Session {
    pub fn new() -> Session {
        Session {
            pos: Position::startpos(),
            difficulty: 1,
            searchers: [Searcher::new(TT_MEGABYTES), Searcher::new(TT_MEGABYTES)],
            history: Vec::new(),
        }
    }

    // -- the seven bridge entry points ---------------------------------

    pub fn engine_info(&self) -> String {
        let mut data = String::from("{\"engineVersion\":");
        json::push_string(&mut data, ENGINE_VERSION);
        data.push_str(",\"backend\":");
        json::push_string(&mut data, "rust-wasm");
        data.push('}');
        json::ok(&data)
    }

    pub fn new_game(&mut self, difficulty: i32) -> String {
        if !(0..=2).contains(&difficulty) {
            return json::err(&format!("invalid difficulty: {difficulty}"));
        }
        self.difficulty = difficulty as usize;
        self.pos = Position::startpos();
        self.history.clear();
        // A new game is an unrelated position: what the tables learned about the
        // last one is worth nothing here and its deep entries would hold slots.
        for s in self.searchers.iter_mut() {
            s.reset();
        }
        self.ok_state()
    }

    pub fn get_state(&self) -> String {
        self.ok_state()
    }

    /// Install a hand-built position from its 63-character board string.
    ///
    /// Test and harness support, and deliberately **not** exported through
    /// `wasm_bindgen`: the browser's copy of the protocol is exactly the seven
    /// functions the UI calls, and a "put any position on the board" entry point
    /// in a shipped game is a liability, not a feature.
    pub fn set_position(&mut self, board: &str, stm: i32, halfmove: u16) -> Result<(), String> {
        let mut pos = Position::from_board_string(board)?;
        pos.set_side_to_move(Color::from_index(stm.clamp(0, 1) as usize));
        pos.set_halfmove_clock(halfmove);
        self.pos = pos;
        self.history.clear();
        Ok(())
    }

    pub fn apply_move(&mut self, fc: i32, fr: i32, tc: i32, tr: i32) -> String {
        if self.winner_json().is_some() {
            return json::err("game is over");
        }
        let Some(mv) = self.find_legal(fc, fr, tc, tr) else {
            return json::err(&format!("illegal move: ({fc},{fr}) -> ({tc},{tr})"));
        };
        let mover = self.make_recorded(mv);
        self.ok_move(mv, mover)
    }

    pub fn ai_move(&mut self, time_budget_ms: Option<u64>) -> String {
        if self.winner_json().is_some() {
            return json::err("game is over");
        }
        let side = self.pos.side_to_move();
        let budget = time_budget_ms.unwrap_or(AI_TIME_BUDGET_MS[self.difficulty]);
        let limits = Limits {
            depth: DEPTH_LIMIT[self.difficulty],
            nodes: None,
            movetime: Some(core::time::Duration::from_millis(budget)),
        };

        // Search a copy. `think` needs `&mut Position` and the searcher needs
        // `&mut self`; both live in this struct, and the clone is a hundred bytes.
        let mut scratch = self.pos.clone();
        let result = self.searchers[side.index()].think(&mut scratch, &limits);

        let Some(mv) = result.best_move else {
            return json::err("AI found no legal move");
        };
        // The searcher is verified, but a move that is not in the current legal
        // list would corrupt the position rather than merely lose the game, so it
        // is checked at the boundary the same way the desktop controller does.
        if !self.legal_moves().contains(&mv) {
            return json::err("AI returned an illegal move");
        }
        let mover = self.make_recorded(mv);

        // Search telemetry, additive to the reply the Python bridge sent. It is
        // what lets a benchmark read the depth the browser actually reaches
        // rather than the depth a comment claims, and it costs one small object.
        let search = format!(
            "{{\"depth\":{},\"seldepth\":{},\"nodes\":{},\"timeMs\":{},\"score\":{},\"mate\":{}}}",
            result.depth,
            result.seldepth,
            result.nodes,
            result.elapsed.as_millis(),
            result.score,
            match jungle_search::mate_distance(result.score) {
                Some(n) => n.to_string(),
                None => "null".to_string(),
            },
        );
        self.ok_move_with(mv, mover, Some(&search))
    }

    pub fn undo_for_human(&mut self, human_color: i32) -> String {
        if self.history.is_empty() {
            return json::err("nothing to undo");
        }
        if !(0..=1).contains(&human_color) {
            return json::err(&format!("invalid color: {human_color}"));
        }
        let human = Color::from_index(human_color as usize);

        if self.pos.side_to_move() == human && self.history.len() >= 2 {
            self.unmake_recorded();
            self.unmake_recorded();
        } else {
            self.unmake_recorded();
            if !self.history.is_empty() && self.pos.side_to_move() != human {
                self.unmake_recorded();
            }
        }
        self.ok_state()
    }

    pub fn replay_moves(&mut self, moves_json: &str) -> String {
        let moves = match json::parse_move_array(moves_json) {
            Ok(m) => m,
            Err(e) => return json::err(&format!("bad moves payload: {e}")),
        };
        let mut applied = 0usize;
        for entry in moves {
            if self.winner_json().is_some() {
                break;
            }
            let [fc, fr, tc, tr] = entry;
            let Some(mv) = self.find_legal(fc, fr, tc, tr) else {
                return json::err(&format!(
                    "illegal move at ply {applied}: ({fc},{fr}) -> ({tc},{tr})"
                ));
            };
            self.make_recorded(mv);
            applied += 1;
        }
        let mut data = format!("{{\"applied\":{applied},\"state\":");
        self.push_state(&mut data);
        data.push('}');
        json::ok(&data)
    }

    // -- position plumbing ---------------------------------------------

    fn legal_moves(&self) -> Vec<Move> {
        generate(&self.pos).as_slice().to_vec()
    }

    fn find_legal(&self, fc: i32, fr: i32, tc: i32, tr: i32) -> Option<Move> {
        if !in_bounds(fc, fr) || !in_bounds(tc, tr) {
            return None;
        }
        let from = sq(fc as usize, fr as usize);
        let to = sq(tc as usize, tr as usize);
        self.legal_moves()
            .into_iter()
            .find(|m| m.from() == from && m.to() == to)
    }

    /// Apply `mv` and journal what the protocol needs but `Move` does not hold.
    fn make_recorded(&mut self, mv: Move) -> i32 {
        let mover = signed_pid(self.pos.piece_at(mv.from()));
        let captured = signed_pid(self.pos.piece_at(mv.to()));
        self.pos.make(mv);
        self.history.push(MoveRecord {
            mv,
            captured,
            mover,
        });
        mover
    }

    fn unmake_recorded(&mut self) {
        self.pos.unmake();
        self.history.pop();
    }

    // -- serialisation -------------------------------------------------

    fn ok_state(&self) -> String {
        let mut data = String::from("{\"state\":");
        self.push_state(&mut data);
        data.push('}');
        json::ok(&data)
    }

    fn ok_move(&self, mv: Move, mover: i32) -> String {
        self.ok_move_with(mv, mover, None)
    }

    /// The `apply_move` / `ai_move` reply. `search` is present only for `ai_move`.
    fn ok_move_with(&self, mv: Move, mover: i32, search: Option<&str>) -> String {
        let captured = self.history.last().map_or(0, |r| r.captured);
        let mut data = String::from("{\"move\":");
        push_move(&mut data, mv, captured);
        let _ = write_i32(&mut data, ",\"moverPid\":", mover);
        if let Some(s) = search {
            data.push_str(",\"search\":");
            data.push_str(s);
        }
        data.push_str(",\"state\":");
        self.push_state(&mut data);
        data.push('}');
        json::ok(&data)
    }

    /// `None` while the game continues; otherwise the `winner` object's body.
    ///
    /// Checked in the same order as the Python bridge: a decided position first,
    /// then the fifty-move draw, then stalemate — which in Jungle is a loss for
    /// the side with nothing to play, not a draw.
    fn winner_json(&self) -> Option<String> {
        if let Some(winner) = self.pos.result() {
            let loser = winner.flip();
            let reason = if self.pos.alive_count(loser) == 0 {
                WIN_REASON_ELIMINATION
            } else {
                WIN_REASON_DEN
            };
            return Some(format!(
                "{{\"color\":{},\"reason\":\"{reason}\"}}",
                winner.index()
            ));
        }
        if self.pos.is_fifty_move_draw() {
            return Some(format!(
                "{{\"color\":null,\"reason\":\"{DRAW_REASON_FIFTY}\"}}"
            ));
        }
        if !self.pos.has_legal_moves() {
            let winner = self.pos.side_to_move().flip();
            return Some(format!(
                "{{\"color\":{},\"reason\":\"{WIN_REASON_STALEMATE}\"}}",
                winner.index()
            ));
        }
        None
    }

    fn push_state(&self, out: &mut String) {
        let winner = self.winner_json();

        out.push_str("{\"board\":[");
        for r in 0..ROWS {
            if r > 0 {
                out.push(',');
            }
            out.push('[');
            for c in 0..COLS {
                if c > 0 {
                    out.push(',');
                }
                let _ = write_i32(out, "", signed_pid(self.pos.piece_at(sq(c, r))));
            }
            out.push(']');
        }
        out.push(']');

        let _ = write_i32(out, ",\"turn\":", self.pos.side_to_move().index() as i32);
        let _ = write_i32(out, ",\"plyCount\":", self.history.len() as i32);

        // An over game offers no moves: the UI reads an empty list as "nothing to
        // click", which is what keeps a finished board inert.
        out.push_str(",\"legalMoves\":[");
        if winner.is_none() {
            for (i, mv) in self.legal_moves().into_iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                push_move(out, mv, signed_pid(self.pos.piece_at(mv.to())));
            }
        }
        out.push(']');

        out.push_str(",\"history\":[");
        for (i, rec) in self.history.iter().enumerate() {
            if i > 0 {
                out.push(',');
            }
            json::push_string(out, &format_move(rec, i + 1));
        }
        out.push(']');

        out.push_str(",\"lastMove\":");
        match self.history.last() {
            Some(rec) => push_move(out, rec.mv, rec.captured),
            None => out.push_str("null"),
        }

        out.push_str(",\"winner\":");
        match &winner {
            Some(w) => out.push_str(w),
            None => out.push_str("null"),
        }
        out.push_str(",\"terminal\":");
        out.push_str(if winner.is_some() { "true" } else { "false" });

        // Animals each side has *lost*, in capture order, for the side panel.
        out.push_str(",\"captured\":{\"blue\":[");
        self.push_losses(out, Color::Blue);
        out.push_str("],\"black\":[");
        self.push_losses(out, Color::Black);
        out.push_str("]}}");
    }

    fn push_losses(&self, out: &mut String, side: Color) {
        let mut first = true;
        for rec in &self.history {
            if rec.captured == 0 {
                continue;
            }
            let victim_is_blue = rec.captured > 0;
            if victim_is_blue != (side == Color::Blue) {
                continue;
            }
            if !first {
                out.push(',');
            }
            first = false;
            json::push_string(out, ANIMAL_NAMES[rec.captured.unsigned_abs() as usize]);
        }
    }
}

// -- free helpers -------------------------------------------------------

fn in_bounds(c: i32, r: i32) -> bool {
    (0..COLS as i32).contains(&c) && (0..ROWS as i32).contains(&r)
}

/// The protocol's piece encoding: `+rank` Blue, `-rank` Black, 0 empty.
fn signed_pid(piece: Option<Piece>) -> i32 {
    match piece {
        None => 0,
        Some(p) => {
            let rank = p.rank() as i32;
            if p.color() == Color::Blue {
                rank
            } else {
                -rank
            }
        }
    }
}

fn write_i32(out: &mut String, prefix: &str, v: i32) -> core::fmt::Result {
    use core::fmt::Write;
    write!(out, "{prefix}{v}")
}

fn push_move(out: &mut String, mv: Move, captured: i32) {
    use core::fmt::Write;
    let _ = write!(
        out,
        "{{\"fc\":{},\"fr\":{},\"tc\":{},\"tr\":{},\"captured\":{}}}",
        col_of(mv.from()),
        row_of(mv.from()),
        col_of(mv.to()),
        row_of(mv.to()),
        captured
    );
}

/// `"12. B Lion A1->A2 xDog"`, the history-panel format.
fn format_move(rec: &MoveRecord, number: usize) -> String {
    let color_tag = if rec.mover > 0 { 'B' } else { 'K' };
    let animal = ANIMAL_NAMES[rec.mover.unsigned_abs() as usize];
    let src = square_name(rec.mv.from());
    let dst = square_name(rec.mv.to());
    let mut s = format!("{number}. {color_tag} {animal} {src}->{dst}");
    if rec.captured != 0 {
        s.push_str(" x");
        s.push_str(ANIMAL_NAMES[rec.captured.unsigned_abs() as usize]);
    }
    s
}

/// `(3, 0) -> "D1"`.
fn square_name(s: jungle_core::types::Square) -> String {
    format!("{}{}", (b'A' + col_of(s) as u8) as char, row_of(s) + 1)
}
