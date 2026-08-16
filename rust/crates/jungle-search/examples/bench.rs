//! Node/depth benchmark on the same three positions the Python harness uses.
//!
//!     cargo run --release -p jungle-search --example bench -- [budget_ms]
//!
//! The positions are the exact board strings `tools/strength_harness.py`
//! benchmarks, so the two numbers can be put side by side without caveats.

use std::time::Instant;

use jungle_core::position::Position;
use jungle_core::types::Color;
use jungle_search::{Limits, Searcher};

const POSITIONS: &[(&str, &str, char)] = &[
    (
        "fixed-mid",
        "g.....f.......a.e.d.h.....................H.D.E.A.......F.....G",
        'B',
    ),
    (
        "midgame-1",
        "g....b..c....fae...dh.....................H.D.E..B....CA..F...G",
        'B',
    ),
    (
        "midgame-2",
        "g....f.c....bh..ed....a...................HD...EA.FB..C......G.",
        'B',
    ),
];

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let budget: u64 = args.get(1).and_then(|s| s.parse().ok()).unwrap_or(2000);

    println!("Bench: budget={budget}ms");
    println!(
        "{:<12} {:>5} {:>7} {:>12} {:>12} {:>7}",
        "position", "depth", "seldep", "nodes", "nps", "time_s"
    );

    let mut total_nodes = 0u64;
    let mut total_time = 0.0f64;

    for &(name, board, stm) in POSITIONS {
        let mut pos = Position::from_board_string(board).expect("bad bench position");
        pos.set_side_to_move(if stm == 'B' { Color::Blue } else { Color::Black });

        let mut searcher = Searcher::new(64);
        let t0 = Instant::now();
        let r = searcher.think(&mut pos, &Limits::movetime(budget));
        let dt = t0.elapsed().as_secs_f64();
        let nps = if dt > 0.0 { r.nodes as f64 / dt } else { 0.0 };

        println!(
            "{name:<12} {:>5} {:>7} {:>12} {:>12.0} {:>7.2}",
            r.depth, r.seldepth, r.nodes, nps, dt
        );
        total_nodes += r.nodes;
        total_time += dt;
    }

    println!(
        "\ntotal: {total_nodes} nodes in {total_time:.2}s = {:.0} nps",
        total_nodes as f64 / total_time
    );
}
