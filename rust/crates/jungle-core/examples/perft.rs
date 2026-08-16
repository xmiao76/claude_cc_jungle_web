//! Perft benchmark and fingerprint.
//!
//!     cargo run --release --example perft -- [max_depth]
//!
//! The same measurement `python -m tools.strength_harness perft` makes, so the
//! two are directly comparable.

use std::time::Instant;

use jungle_core::position::Position;
use jungle_core::{perft, perft_divide};

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let max_depth: u32 = args.get(1).and_then(|s| s.parse().ok()).unwrap_or(6);
    let divide = args.iter().any(|a| a == "--divide");

    println!("{:>5}  {:>16}  {:>8}  {:>14}", "depth", "leaves", "time_s", "leaves/s");
    for depth in 1..=max_depth {
        let mut pos = Position::startpos();
        let t0 = Instant::now();
        let leaves = perft(&mut pos, depth);
        let dt = t0.elapsed().as_secs_f64();
        let rate = if dt > 0.0 { leaves as f64 / dt } else { 0.0 };
        println!("{depth:>5}  {leaves:>16}  {dt:>8.3}  {rate:>14.0}");
    }

    if divide {
        let mut pos = Position::startpos();
        println!("\ndivide at depth {max_depth}:");
        for (m, n) in perft_divide(&mut pos, max_depth) {
            println!("  {m}  {n}");
        }
    }
}
