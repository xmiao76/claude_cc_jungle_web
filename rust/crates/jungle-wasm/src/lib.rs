//! The browser face of the Jungle engine.
//!
//! Everything here is a two-line forward to [`session::Session`], which holds the
//! actual protocol and is written without a single JS type so that `cargo test`
//! can exercise it on the host. Keep it that way: logic that drifts into this
//! file becomes logic only a browser can test.
//!
//! One worker owns one game, so the session is a module-level singleton, exactly
//! as `web_api.py`'s `_session` was.

pub mod json;
pub mod session;

use std::cell::RefCell;

use wasm_bindgen::prelude::*;

use session::Session;

thread_local! {
    static SESSION: RefCell<Session> = RefCell::new(Session::new());
}

/// Route Rust panics to `console.error` instead of an opaque `unreachable`.
///
/// Called once by the worker before anything else. Without it a panic in a
/// release build surfaces as `RuntimeError: unreachable executed` with no stack,
/// which is indistinguishable from a dozen other failures.
#[wasm_bindgen(js_name = start)]
pub fn start() {
    console_error_panic_hook::set_once();
}

#[wasm_bindgen(js_name = engineInfo)]
pub fn engine_info() -> String {
    SESSION.with(|s| s.borrow().engine_info())
}

#[wasm_bindgen(js_name = newGame)]
pub fn new_game(difficulty: i32) -> String {
    SESSION.with(|s| s.borrow_mut().new_game(difficulty))
}

#[wasm_bindgen(js_name = getState)]
pub fn get_state() -> String {
    SESSION.with(|s| s.borrow().get_state())
}

#[wasm_bindgen(js_name = applyMove)]
pub fn apply_move(fc: i32, fr: i32, tc: i32, tr: i32) -> String {
    SESSION.with(|s| s.borrow_mut().apply_move(fc, fr, tc, tr))
}

/// Search and apply a move for the side to move.
///
/// `budget_ms` of `None` means "use the difficulty's own budget", which is how
/// the UI always calls it; the tests pass a small explicit one to stay quick.
///
/// `u32`, not `u64`, on purpose: wasm-bindgen marshals a `u64` as a `BigInt`, so
/// the natural `aiMove(300)` from JS would throw rather than search. Forty-nine
/// days of milliseconds is enough headroom for a move.
#[wasm_bindgen(js_name = aiMove)]
pub fn ai_move(budget_ms: Option<u32>) -> String {
    SESSION.with(|s| s.borrow_mut().ai_move(budget_ms.map(u64::from)))
}

#[wasm_bindgen(js_name = undoForHuman)]
pub fn undo_for_human(human_color: i32) -> String {
    SESSION.with(|s| s.borrow_mut().undo_for_human(human_color))
}

#[wasm_bindgen(js_name = replayMoves)]
pub fn replay_moves(moves_json: &str) -> String {
    SESSION.with(|s| s.borrow_mut().replay_moves(moves_json))
}
