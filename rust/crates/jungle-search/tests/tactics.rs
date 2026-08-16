//! Behavioural tests for the search.
//!
//! A search port cannot be verified the way the rules were -- move generation has
//! one right answer and the corpus holds it, but a search has only "did it find
//! the move". So these check the things that must never regress: it wins when a
//! win is there, it does not throw material away, it prefers a faster win, it
//! always returns a legal move, and it stops when told to.

use std::sync::atomic::Ordering;
use std::thread;
use std::time::{Duration, Instant};

use jungle_core::position::Position;
use jungle_core::types::{animal, sq, Color, Move, Piece};
use jungle_core::{bitboard, generate};
use jungle_search::score::{is_mate_score, MATE_BOUND};
use jungle_search::{Limits, Searcher};

fn build(specs: &[(usize, usize, Color, u8)], stm: Color) -> Position {
    let mut p = Position::empty();
    for &(c, r, color, rank) in specs {
        p.place(sq(c, r), Piece::new(color, rank));
    }
    p.set_side_to_move(stm);
    p
}

fn best(pos: &mut Position, depth: i32) -> (Move, i32) {
    let mut s = Searcher::new(16);
    let r = s.think(pos, &Limits::depth(depth));
    (r.best_move.expect("search returned no move"), r.score)
}

#[test]
fn finds_an_immediate_den_entry() {
    let mut pos = build(
        &[
            (3, 1, Color::Blue, animal::WOLF),
            (0, 0, Color::Black, animal::CAT),
            (6, 6, Color::Black, animal::RAT),
        ],
        Color::Blue,
    );
    let (mv, score) = best(&mut pos, 6);
    assert_eq!(mv.to(), bitboard::DEN_BLACK, "should walk into the den");
    assert!(is_mate_score(score) && score > 0, "score was {score}");
}

#[test]
fn finds_a_forced_den_entry_in_two() {
    // The Wolf walks (3,2) -> (3,1) -> den. Black's Cat is too far to interfere,
    // and (3,1) is a Black trap -- which weakens the Wolf's *defence*, not its
    // ability to keep walking.
    let mut pos = build(
        &[
            (3, 2, Color::Blue, animal::WOLF),
            (0, 0, Color::Black, animal::CAT),
        ],
        Color::Blue,
    );
    let (mv, score) = best(&mut pos, 8);
    assert_eq!(mv, Move::new(sq(3, 2), sq(3, 1)));
    assert!(is_mate_score(score) && score > 0, "score was {score}");
}

#[test]
fn takes_free_material() {
    // Blue Leopard (rank 5) takes an undefended Black Wolf (rank 4).
    let mut pos = build(
        &[
            (3, 4, Color::Blue, animal::LEOPARD),
            (3, 3, Color::Black, animal::WOLF),
            (0, 0, Color::Black, animal::CAT),
            (6, 8, Color::Blue, animal::RAT),
        ],
        Color::Blue,
    );
    let (mv, _) = best(&mut pos, 6);
    assert_eq!(mv, Move::new(sq(3, 4), sq(3, 3)), "should take the Wolf");
}

#[test]
fn declines_a_capture_that_loses_material() {
    // Blue Lion can take a Black Cat, but a Black Elephant recaptures. Taking is
    // -700 + 200; almost anything else is better.
    let mut pos = build(
        &[
            (3, 4, Color::Blue, animal::LION),
            (3, 3, Color::Black, animal::CAT),
            (3, 2, Color::Black, animal::ELEPHANT),
            (0, 8, Color::Blue, animal::RAT),
        ],
        Color::Blue,
    );
    let (mv, _) = best(&mut pos, 7);
    assert_ne!(
        mv,
        Move::new(sq(3, 4), sq(3, 3)),
        "walked the Lion into an Elephant"
    );
}

#[test]
fn prefers_the_faster_win() {
    // One Wolf is one step from the den, another is three away. Both win; the
    // near one wins sooner, and mate-distance scoring must say so.
    let mut pos = build(
        &[
            (3, 1, Color::Blue, animal::WOLF),
            (0, 4, Color::Blue, animal::LEOPARD),
            (0, 0, Color::Black, animal::CAT),
        ],
        Color::Blue,
    );
    let (mv, score) = best(&mut pos, 8);
    assert_eq!(mv.to(), bitboard::DEN_BLACK);
    // A win at ply 1 scores MATE - 1, the highest reachable score.
    assert!(score > MATE_BOUND, "score was {score}");
}

#[test]
fn sees_that_it_is_losing_when_it_is() {
    // Black Wolf is one step from Blue's den and it is Black to move; Blue has
    // nothing nearby. From Blue's side this is lost.
    let mut pos = build(
        &[
            (3, 7, Color::Black, animal::WOLF),
            (0, 0, Color::Blue, animal::CAT),
        ],
        Color::Black,
    );
    let (mv, score) = best(&mut pos, 6);
    assert_eq!(mv.to(), bitboard::DEN_BLUE);
    assert!(is_mate_score(score) && score > 0);
}

#[test]
fn never_returns_an_illegal_move() {
    struct Rng(u64);
    impl Rng {
        fn next(&mut self) -> u64 {
            self.0 ^= self.0 >> 12;
            self.0 ^= self.0 << 25;
            self.0 ^= self.0 >> 27;
            self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
        }
    }

    let mut rng = Rng(0xFEED_FACE_CAFE_0001);
    let mut searcher = Searcher::new(16);

    for _ in 0..40 {
        let mut pos = Position::startpos();
        for _ in 0..40 {
            if pos.result().is_some() {
                break;
            }
            let legal = generate(&pos);
            if legal.is_empty() {
                break;
            }
            let r = searcher.think(&mut pos, &Limits::nodes(3_000));
            let mv = r.best_move.expect("no move returned in a live position");
            assert!(
                legal.as_slice().contains(&mv),
                "illegal move {mv} in {}",
                pos.to_board_string()
            );
            pos.make(legal[(rng.next() % legal.len() as u64) as usize]);
        }
    }
}

#[test]
fn respects_a_node_limit() {
    let mut pos = Position::startpos();
    let mut s = Searcher::new(16);
    for budget in [1_000u64, 10_000, 100_000] {
        let r = s.think(&mut pos, &Limits::nodes(budget));
        assert!(r.best_move.is_some());
        // The clock is only checked every 2048 nodes, so allow one interval of
        // overshoot rather than pretending the bound is exact.
        assert!(
            r.nodes <= budget + 2048,
            "budget {budget}, searched {}",
            r.nodes
        );
    }
}

#[test]
fn respects_a_time_limit() {
    let mut pos = Position::startpos();
    let mut s = Searcher::new(16);
    let t0 = Instant::now();
    let r = s.think(&mut pos, &Limits::movetime(300));
    let elapsed = t0.elapsed();
    assert!(r.best_move.is_some());
    assert!(
        elapsed < Duration::from_millis(1500),
        "300ms budget took {elapsed:?}"
    );
}

#[test]
fn can_be_stopped_from_another_thread() {
    // The GUI aborts a search on Escape, undo and new game. A stop must produce a
    // legal move promptly rather than None -- the controller applies what it gets.
    let mut pos = Position::startpos();
    let mut s = Searcher::new(16);
    let handle = s.stop_handle();

    let stopper = thread::spawn(move || {
        thread::sleep(Duration::from_millis(80));
        handle.store(true, Ordering::Relaxed);
    });

    let t0 = Instant::now();
    let r = s.think(&mut pos, &Limits::movetime(30_000));
    let elapsed = t0.elapsed();
    stopper.join().unwrap();

    assert!(elapsed < Duration::from_millis(3_000), "took {elapsed:?}");
    let mv = r.best_move.expect("a stopped search must still return a move");
    assert!(generate(&pos).as_slice().contains(&mv));
}

#[test]
fn a_deeper_search_does_not_lose_a_won_position() {
    // Iterative deepening must not talk itself out of a forced win as it goes
    // deeper -- the classic symptom of mate scores stored without ply adjustment.
    let mut pos = build(
        &[
            (3, 2, Color::Blue, animal::WOLF),
            (0, 0, Color::Black, animal::CAT),
        ],
        Color::Blue,
    );
    let mut s = Searcher::new(16);
    for depth in 4..=12 {
        let r = s.think(&mut pos, &Limits::depth(depth));
        assert!(
            is_mate_score(r.score) && r.score > 0,
            "depth {depth} scored {}",
            r.score
        );
    }
}
