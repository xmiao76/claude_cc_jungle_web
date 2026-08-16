//! Differential test of the static evaluation against the Python engine.
//!
//! Move generation agreeing says nothing about whether the two engines *judge* a
//! position the same way, and an evaluation port is easy to get subtly wrong in a
//! way that still looks plausible: a term computed from the evaluating side
//! rather than from the piece's own colour is the classic one, and it survives an
//! antisymmetry check because it is antisymmetric -- just wrong.
//!
//! So every one of the ten thousand corpus positions is scored by both engines
//! and the integers must be equal. Not close: equal.

use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;

use flate2::read::GzDecoder;
use jungle_core::position::Position;
use jungle_core::types::Color;
use jungle_eval::evaluate;

fn evals_path() -> PathBuf {
    match std::env::var("JUNGLE_GOLDEN_EVALS") {
        Ok(p) => PathBuf::from(p),
        Err(_) => {
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../tests/golden/evals.txt.gz")
        }
    }
}

fn records() -> Vec<(String, Color, i32)> {
    let path = evals_path();
    let file = File::open(&path).unwrap_or_else(|e| {
        panic!(
            "cannot open {}: {e}\nregenerate with `python -m tools.golden`",
            path.display()
        )
    });
    BufReader::new(GzDecoder::new(file))
        .lines()
        .map(|l| l.expect("read error"))
        .filter(|l| !l.trim().is_empty() && !l.starts_with('#'))
        .map(|l| {
            let f: Vec<&str> = l.split(' ').collect();
            let stm = if f[1] == "B" { Color::Blue } else { Color::Black };
            (f[0].to_string(), stm, f[2].parse().expect("bad eval"))
        })
        .collect()
}

#[test]
fn evaluation_matches_the_python_engine_exactly() {
    let recs = records();
    assert_eq!(recs.len(), 10_000);

    let mut worst: Option<(String, i32, i32)> = None;
    let mut mismatches = 0usize;

    for (board, stm, want) in recs {
        let mut pos = Position::from_board_string(&board).expect("bad board string");
        pos.set_side_to_move(stm);
        let got = evaluate(&pos, Color::Blue);
        if got != want {
            mismatches += 1;
            if worst.is_none() {
                worst = Some((board, want, got));
            }
        }
    }

    if let Some((board, want, got)) = worst {
        panic!(
            "{mismatches} of 10000 evaluations diverged\n  first: {board}\n  python {want}, rust {got} (delta {})",
            got - want
        );
    }
}

#[test]
fn evaluation_is_antisymmetric_on_every_corpus_position() {
    for (board, stm, _) in records() {
        let mut pos = Position::from_board_string(&board).expect("bad board string");
        pos.set_side_to_move(stm);
        assert_eq!(
            evaluate(&pos, Color::Blue),
            -evaluate(&pos, Color::Black),
            "antisymmetry broken at {board}"
        );
    }
}
