//! Differential test against the golden corpus generated from the Python engine.
//!
//! Perft proves the counts agree. This proves the *decisions* agree: for ten
//! thousand positions spanning openings, sparse endgames, terminals, stalemates
//! and the awkward rules matrices, the exact move list, terminal flag and winner
//! must match move for move. When they do not, the failure names the position and
//! the specific move that was added or missed.

use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;

use flate2::read::GzDecoder;
use jungle_core::position::Position;
use jungle_core::types::{col_of, row_of, Color};

/// The committed corpus, unless `JUNGLE_GOLDEN_CORPUS` points elsewhere.
///
/// The override exists so the crate can be checked against a *freshly generated*
/// corpus with a different seed. Passing only the committed one would prove the
/// rules agree on ten thousand specific positions; passing a fresh one proves
/// they agree in general, which is the actual claim.
fn corpus_path() -> PathBuf {
    match std::env::var("JUNGLE_GOLDEN_CORPUS") {
        Ok(p) => PathBuf::from(p),
        Err(_) => PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../tests/golden/positions.txt.gz"),
    }
}

fn lines() -> Vec<String> {
    let path = corpus_path();
    let file = File::open(&path)
        .unwrap_or_else(|e| panic!("cannot open {}: {e}\nregenerate with `python -m tools.golden`", path.display()));
    BufReader::new(GzDecoder::new(file))
        .lines()
        .map(|l| l.expect("corpus read error"))
        .filter(|l| !l.trim().is_empty() && !l.starts_with('#'))
        .collect()
}

/// Legal moves as the corpus writes them: `fc,fr,tc,tr,captured`, sorted by that
/// tuple, with `captured` in the Python engine's signed piece-id encoding.
fn encoded_moves(pos: &Position) -> Vec<(usize, usize, usize, usize, i8)> {
    let mut out: Vec<_> = jungle_core::generate(pos)
        .as_slice()
        .iter()
        .map(|m| {
            let captured = pos.piece_at(m.to()).map_or(0, |p| p.to_signed());
            (
                col_of(m.from()),
                row_of(m.from()),
                col_of(m.to()),
                row_of(m.to()),
                captured,
            )
        })
        .collect();
    out.sort_unstable();
    out
}

struct Record {
    board: String,
    pos: Position,
    terminal: bool,
    winner: char,
    moves: Vec<(usize, usize, usize, usize, i8)>,
}

fn parse(line: &str) -> Record {
    let f: Vec<&str> = line.split(' ').collect();
    let mut pos = Position::from_board_string(f[0]).expect("bad board string");
    pos.set_side_to_move(if f[1] == "B" { Color::Blue } else { Color::Black });
    pos.set_halfmove_clock(f[2].parse().expect("bad halfmove clock"));
    let n: usize = f[5].parse().expect("bad move count");
    let moves = f[6..6 + n]
        .iter()
        .map(|tok| {
            let v: Vec<&str> = tok.split(',').collect();
            (
                v[0].parse().unwrap(),
                v[1].parse().unwrap(),
                v[2].parse().unwrap(),
                v[3].parse().unwrap(),
                v[4].parse().unwrap(),
            )
        })
        .collect();
    Record {
        board: f[0].to_string(),
        pos,
        terminal: f[3] == "1",
        winner: f[4].chars().next().unwrap(),
        moves,
    }
}

#[test]
fn corpus_is_present_and_populated() {
    assert_eq!(lines().len(), 10_000);
}

#[test]
fn board_strings_round_trip() {
    for (i, line) in lines().iter().enumerate() {
        let r = parse(line);
        assert_eq!(r.pos.to_board_string(), r.board, "line {i} did not round-trip");
        r.pos.assert_consistent();
    }
}

#[test]
fn move_generation_matches_the_python_engine() {
    for (i, line) in lines().iter().enumerate() {
        let r = parse(line);
        let got = encoded_moves(&r.pos);
        if got != r.moves {
            let missing: Vec<_> = r.moves.iter().filter(|m| !got.contains(m)).collect();
            let extra: Vec<_> = got.iter().filter(|m| !r.moves.contains(m)).collect();
            panic!(
                "line {i}: move generation diverged\n  board   {}\n  stm     {:?}\n  missing {missing:?}\n  extra   {extra:?}",
                r.board,
                r.pos.side_to_move()
            );
        }
    }
}

#[test]
fn terminal_status_and_winner_match_the_python_engine() {
    for (i, line) in lines().iter().enumerate() {
        let r = parse(line);
        assert_eq!(
            r.pos.is_terminal(),
            r.terminal,
            "line {i}: terminal flag diverged (board {})",
            r.board
        );
        let want = match r.winner {
            'B' => Some(Color::Blue),
            'K' => Some(Color::Black),
            _ => None,
        };
        assert_eq!(
            r.pos.winner(),
            want,
            "line {i}: winner diverged (board {})",
            r.board
        );
    }
}
