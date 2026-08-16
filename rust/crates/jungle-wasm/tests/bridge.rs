//! The bridge protocol, asserted against the Rust implementation.
//!
//! A port of `tests/python/test_web_api.py`, test for test. That file is the
//! written-down contract the UI, the coordinate helpers and the Playwright suite
//! are all built on; reproducing its assertions here is what makes "the engine
//! changed but the protocol did not" a checked claim rather than a hope.
//!
//! Where a test's Python form asserted something Python-specific, the Rust form
//! says so at the point of divergence. There is exactly one such place.

use jungle_wasm::session::Session;
use serde_json::Value;

fn call(s: String) -> Value {
    serde_json::from_str(&s).expect("bridge replies must be valid JSON")
}

/// Every test starts from a clean Easy game, as the Python fixture did.
fn fresh() -> Session {
    let mut s = Session::new();
    s.new_game(0);
    s
}

/// `pieces` is `[(col, row, is_blue, rank)]`; everything else is empty.
fn custom_state(s: &mut Session, pieces: &[(usize, usize, bool, u8)]) {
    let mut board = vec![b'.'; 63];
    for &(col, row, is_blue, rank) in pieces {
        let base = if is_blue { b'A' } else { b'a' };
        board[row * 7 + col] = base + rank - 1;
    }
    let text = String::from_utf8(board).unwrap();
    s.set_position(&text, 0, 0)
        .expect("hand-built position must be valid");
}

fn state(v: &Value) -> &Value {
    &v["data"]["state"]
}

// ---------------------------------------------------------------------------
// new_game / get_state
// ---------------------------------------------------------------------------

#[test]
fn new_game_returns_starting_state() {
    let mut s = Session::new();
    let res = call(s.new_game(1));
    assert_eq!(res["ok"], true);
    let st = state(&res);

    let board = st["board"].as_array().unwrap();
    assert_eq!(board.len(), 9);
    assert!(board.iter().all(|row| row.as_array().unwrap().len() == 7));
    let pieces = board
        .iter()
        .flat_map(|row| row.as_array().unwrap())
        .filter(|pid| pid.as_i64() != Some(0))
        .count();
    assert_eq!(pieces, 16);

    assert_eq!(st["turn"], 0); // Blue
    assert_eq!(st["terminal"], false);
    assert_eq!(st["winner"], Value::Null);
    assert_eq!(st["plyCount"], 0);
    assert!(!st["legalMoves"].as_array().unwrap().is_empty());
    assert_eq!(st["captured"]["blue"].as_array().unwrap().len(), 0);
    assert_eq!(st["captured"]["black"].as_array().unwrap().len(), 0);
}

#[test]
fn new_game_rejects_bad_difficulty() {
    let mut s = Session::new();
    let res = call(s.new_game(5));
    assert_eq!(res["ok"], false);
    assert!(res["error"].as_str().unwrap().contains("difficulty"));
}

#[test]
fn get_state_matches_new_game() {
    let mut s = Session::new();
    let first = call(s.new_game(0))["data"]["state"].clone();
    let again = call(s.get_state())["data"]["state"].clone();
    assert_eq!(first, again);
}

#[test]
fn engine_info_reports_version_and_backend() {
    let s = Session::new();
    let res = call(s.engine_info());
    assert_eq!(res["ok"], true);
    assert!(!res["data"]["engineVersion"].as_str().unwrap().is_empty());
    // The Python bridge reported the interpreter version here. There is no
    // interpreter now, so the field names the engine behind the protocol.
    assert_eq!(res["data"]["backend"], "rust-wasm");
}

// ---------------------------------------------------------------------------
// apply_move
// ---------------------------------------------------------------------------

#[test]
fn apply_legal_move_advances_turn() {
    let mut s = fresh();
    // Blue Rat at (6,6) stepping north to (6,5) is legal from the start.
    let res = call(s.apply_move(6, 6, 6, 5));
    assert_eq!(res["ok"], true);
    let st = state(&res);
    assert_eq!(st["turn"], 1); // Black
    assert_eq!(st["plyCount"], 1);
    assert_eq!(
        st["lastMove"],
        serde_json::json!({"fc": 6, "fr": 6, "tc": 6, "tr": 5, "captured": 0})
    );
    assert_ne!(st["board"][5][6], 0);
    assert_eq!(st["board"][6][6], 0);
    assert_eq!(st["history"].as_array().unwrap().len(), 1);
    assert_eq!(st["history"][0], "1. B Rat G7->G6");
}

#[test]
fn apply_illegal_move_is_rejected_and_state_unchanged() {
    let mut s = fresh();
    let before = call(s.get_state())["data"]["state"].clone();
    let res = call(s.apply_move(0, 8, 3, 3));
    assert_eq!(res["ok"], false);
    assert!(res["error"].as_str().unwrap().contains("illegal"));
    let after = call(s.get_state())["data"]["state"].clone();
    assert_eq!(before, after);
}

#[test]
fn out_of_bounds_coordinates_are_rejected_not_panicked() {
    // Not in the Python suite: there, out-of-range indices raised inside the
    // engine. Here they would index a fixed-size table, so the boundary check
    // has to be explicit and is worth pinning.
    let mut s = fresh();
    for (fc, fr, tc, tr) in [(-1, 0, 0, 0), (0, 0, 99, 0), (0, -5, 0, 0), (7, 9, 0, 0)] {
        let res = call(s.apply_move(fc, fr, tc, tr));
        assert_eq!(
            res["ok"], false,
            "({fc},{fr})->({tc},{tr}) should be rejected"
        );
    }
}

// ---------------------------------------------------------------------------
// ai_move
// ---------------------------------------------------------------------------

#[test]
fn ai_move_plays_a_legal_move_and_flips_turn() {
    let mut s = fresh();
    let res = call(s.ai_move(None));
    assert_eq!(res["ok"], true);
    assert!(!res["data"]["move"].is_null());
    let st = state(&res);
    assert_eq!(st["turn"], 1);
    assert_eq!(st["plyCount"], 1);
}

#[test]
fn ai_move_respects_an_explicit_budget() {
    let mut s = fresh();
    assert_eq!(call(s.ai_move(Some(200)))["ok"], true);
}

#[test]
fn ai_move_alternates_sides_and_stays_legal() {
    // The searchers are per-colour and long-lived; walking a few plies proves
    // both are wired and that neither corrupts the position it shares.
    let mut s = fresh();
    for ply in 0..12 {
        let res = call(s.ai_move(Some(50)));
        assert_eq!(res["ok"], true, "ply {ply}: {res}");
        assert_eq!(state(&res)["turn"], (ply + 1) % 2);
    }
    assert_eq!(call(s.get_state())["data"]["state"]["plyCount"], 12);
}

// ---------------------------------------------------------------------------
// undo
// ---------------------------------------------------------------------------

#[test]
fn undo_pops_human_and_ai_plies() {
    let mut s = fresh();
    s.apply_move(6, 6, 6, 5); // human (Blue)
    s.ai_move(Some(100)); // AI (Black)
    let res = call(s.undo_for_human(0));
    assert_eq!(res["ok"], true);
    let st = state(&res);
    assert_eq!(st["plyCount"], 0);
    assert_eq!(st["turn"], 0);
}

#[test]
fn undo_with_no_history_errors() {
    let mut s = fresh();
    assert_eq!(call(s.undo_for_human(0))["ok"], false);
}

// ---------------------------------------------------------------------------
// terminal positions
// ---------------------------------------------------------------------------

#[test]
fn den_entry_wins_the_game() {
    let mut s = fresh();
    custom_state(
        &mut s,
        &[(3, 1, true, 7), (0, 5, false, 1), (6, 5, false, 2)],
    );
    let res = call(s.apply_move(3, 1, 3, 0)); // into Black's den
    assert_eq!(res["ok"], true);
    let st = state(&res);
    assert_eq!(st["terminal"], true);
    assert_eq!(
        st["winner"],
        serde_json::json!({"color": 0, "reason": "den"})
    );
    assert_eq!(st["legalMoves"].as_array().unwrap().len(), 0);
}

#[test]
fn capturing_the_last_piece_wins_by_elimination() {
    let mut s = fresh();
    custom_state(&mut s, &[(2, 2, true, 7), (2, 1, false, 2)]);
    let res = call(s.apply_move(2, 2, 2, 1));
    assert_eq!(res["ok"], true);
    let st = state(&res);
    assert_eq!(
        st["winner"],
        serde_json::json!({"color": 0, "reason": "elimination"})
    );
    assert_eq!(st["captured"]["black"], serde_json::json!(["Cat"]));
    assert_eq!(st["history"][0], "1. B Lion C3->C2 xCat");
}

#[test]
fn moves_are_rejected_after_game_over() {
    let mut s = fresh();
    custom_state(&mut s, &[(3, 1, true, 7), (0, 5, false, 1)]);
    s.apply_move(3, 1, 3, 0);
    let res = call(s.apply_move(0, 5, 0, 4));
    assert_eq!(res["ok"], false);
    assert!(res["error"].as_str().unwrap().contains("over"));
    assert_eq!(call(s.ai_move(None))["ok"], false);
}

// ---------------------------------------------------------------------------
// replay (E2E support)
// ---------------------------------------------------------------------------

#[test]
fn replay_applies_a_move_sequence() {
    let mut s = fresh();
    let res = call(s.replay_moves("[[6,6,6,5],[0,2,0,3]]"));
    assert_eq!(res["ok"], true);
    assert_eq!(res["data"]["applied"], 2);
    assert_eq!(res["data"]["state"]["plyCount"], 2);
}

#[test]
fn replay_rejects_an_illegal_sequence() {
    let mut s = fresh();
    let res = call(s.replay_moves("[[6,6,6,5],[6,6,6,5]]"));
    assert_eq!(res["ok"], false);
    assert!(res["error"].as_str().unwrap().contains("ply 1"));
}

#[test]
fn replay_rejects_a_malformed_payload() {
    let mut s = fresh();
    assert_eq!(call(s.replay_moves("not json"))["ok"], false);
}
