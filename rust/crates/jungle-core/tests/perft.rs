//! The move-generation contract: the frozen perft counts from
//! `tests/test_perft.py`, reproduced exactly.
//!
//! These seven positions and their leaf counts are the acceptance criterion for
//! this crate's rules layer. If it reproduces them, it is rules-identical to the
//! Python engine it replaces. A mismatch means a rules change, deliberate or
//! not, and `perft_divide` narrows it to one subtree.

use jungle_core::position::Position;
use jungle_core::types::{animal, sq, Color, Piece};
use jungle_core::{perft, perft_divide};

fn build(specs: &[(usize, usize, Color, u8)]) -> Position {
    let mut p = Position::empty();
    for &(c, r, color, rank) in specs {
        p.place(sq(c, r), Piece::new(color, rank));
    }
    p.set_side_to_move(Color::Blue);
    p
}

const B: Color = Color::Blue;
const K: Color = Color::Black;

fn positions() -> Vec<(&'static str, Position, [u64; 4])> {
    vec![
        ("start", Position::startpos(), [24, 576, 12240, 260099]),
        (
            "trap_standoff",
            build(&[
                (3, 6, B, animal::ELEPHANT),
                (3, 7, K, animal::RAT),
                (2, 7, B, animal::CAT),
                (4, 7, K, animal::DOG),
            ]),
            [8, 39, 187, 1057],
        ),
        (
            "river_rats",
            build(&[
                (1, 4, B, animal::RAT),
                (2, 4, K, animal::RAT),
                (0, 4, B, animal::ELEPHANT),
                (3, 4, K, animal::LION),
            ]),
            [5, 27, 154, 1016],
        ),
        (
            "jumpers",
            build(&[
                (0, 4, B, animal::LION),
                (3, 4, B, animal::TIGER),
                (6, 4, K, animal::LION),
                (3, 2, K, animal::TIGER),
            ]),
            [4, 28, 172, 1103],
        ),
        (
            "jump_blocked",
            build(&[
                (0, 4, B, animal::LION),
                (1, 4, K, animal::RAT),
                (3, 3, B, animal::TIGER),
                (6, 0, K, animal::ELEPHANT),
            ]),
            [6, 32, 170, 1008],
        ),
        (
            "den_race",
            build(&[
                (3, 2, B, animal::WOLF),
                (2, 1, K, animal::CAT),
                (3, 6, K, animal::LEOPARD),
                (4, 7, B, animal::DOG),
            ]),
            [8, 63, 419, 2751],
        ),
        (
            "ele_vs_rat",
            build(&[(3, 4, B, animal::ELEPHANT), (3, 5, K, animal::RAT)]),
            [1, 4, 7, 28],
        ),
    ]
}

#[test]
fn frozen_perft_counts_match_the_python_engine() {
    for (name, mut pos, expected) in positions() {
        for (i, &want) in expected.iter().enumerate() {
            let depth = (i + 1) as u32;
            let got = perft(&mut pos, depth);
            assert_eq!(
                got, want,
                "{name} perft({depth}): expected {want}, got {got}"
            );
        }
    }
}

#[test]
fn start_position_perft_5() {
    // Measured against the Python engine, which needed 52 seconds for it.
    let mut pos = Position::startpos();
    assert_eq!(perft(&mut pos, 5), 5_111_620);
}

/// Depths 6 and 7 extend the contract past anything the Python engine could
/// reach in practice: it manages about 98k leaves/s, so depth 6 is a 17-minute
/// run and depth 7 is most of a day.
///
/// Depth 6 was nonetheless cross-checked against the Python engine before being
/// frozen here, because a number that only one implementation has ever computed
/// is not a contract, it is an assumption. Depth 7 is this crate's own and is
/// ignored by default at ~30 seconds.
#[test]
fn start_position_perft_6() {
    let mut pos = Position::startpos();
    assert_eq!(perft(&mut pos, 6), 100_453_636);
}

#[test]
#[ignore = "~30s; run with `cargo test --release -- --ignored`"]
fn start_position_perft_7() {
    let mut pos = Position::startpos();
    assert_eq!(perft(&mut pos, 7), 1_908_199_299);
}

/// Depths 5 through 8 for the six tactical positions, all cross-checked against
/// the Python engine.
///
/// The frozen contract stopped at depth 4, which is only a few hundred to a few
/// thousand leaves for these positions -- shallow enough that a rule that only
/// bites in longer sequences could hide. These extend the same six positions to
/// roughly 8.5 million additional leaves across exactly the awkward regimes:
/// traps, the water boundary, blocked and unblocked leaps, the den race, and the
/// Elephant/Rat standoff. They are cheap because the positions are small.
#[test]
fn deep_perft_on_the_tactical_positions_matches_python() {
    let expected: &[(&str, [u64; 4])] = &[
        ("trap_standoff", [5294, 28118, 136259, 812256]),
        ("river_rats", [5047, 30949, 175804, 1186728]),
        ("jumpers", [5548, 33908, 203826, 1291031]),
        ("jump_blocked", [6319, 38531, 225745, 1407851]),
        ("den_race", [16698, 101830, 622125, 3836049]),
        ("ele_vs_rat", [84, 336, 895, 3343]),
    ];

    for (name, mut pos, _) in positions() {
        let Some((_, want)) = expected.iter().find(|(n, _)| *n == name) else {
            continue; // `start` is covered separately
        };
        for (i, &w) in want.iter().enumerate() {
            let depth = (i + 5) as u32;
            let got = perft(&mut pos, depth);
            assert_eq!(got, w, "{name} perft({depth}): expected {w}, got {got}");
        }
    }
}

#[test]
fn perft_restores_the_position_exactly() {
    for (name, mut pos, _) in positions() {
        let key = pos.key();
        let board = pos.to_board_string();
        let stm = pos.side_to_move();
        perft(&mut pos, 3);
        assert_eq!(pos.key(), key, "{name}: hash not restored");
        assert_eq!(pos.to_board_string(), board, "{name}: board not restored");
        assert_eq!(pos.side_to_move(), stm, "{name}: side to move not restored");
        assert_eq!(pos.ply(), 0, "{name}: undo stack not unwound");
        pos.assert_consistent();
    }
}

#[test]
fn divide_sums_to_the_total() {
    for (name, mut pos, _) in positions() {
        for depth in 1..=3u32 {
            let total = perft(&mut pos, depth);
            let sum: u64 = perft_divide(&mut pos, depth).iter().map(|&(_, n)| n).sum();
            assert_eq!(sum, total, "{name} divide at depth {depth}");
        }
    }
}

#[test]
fn make_unmake_is_consistent_at_every_node() {
    // A cheaper, wider version of the perft restore test: check the full
    // representation invariant at every node of a small tree, not just the root.
    fn walk(pos: &mut Position, depth: u32) {
        pos.assert_consistent();
        if depth == 0 || pos.result().is_some() {
            return;
        }
        for &m in jungle_core::generate(pos).as_slice() {
            pos.make(m);
            walk(pos, depth - 1);
            pos.unmake();
        }
    }
    let mut pos = Position::startpos();
    walk(&mut pos, 3);
}
