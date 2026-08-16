//! Developer front-end for the Jungle engine.
//!
//! Subcommands:
//!   `protocol`  line-based host, so another process can drive the engine
//!   `bench`     node/depth benchmark
//!   `perft`     move-generation fingerprint and speed
//!   `match`     self-play A/B with Elo, confidence interval and SPRT
//!
//! The protocol exists so the Python strength harness can play this engine
//! against the Python one without either having to import the other. It is
//! deliberately tiny -- four commands -- because its only job is to let two
//! engines share a board.

mod matchrun;

use std::io::{self, BufRead, Write};

use jungle_core::position::Position;
use jungle_core::types::{col_of, row_of, Color, Move};
use jungle_core::{generate, perft, perft_divide};
use jungle_search::{EvalParams, Limits, SearchParams, Searcher};

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("protocol") => protocol(),
        Some("perft") => {
            let depth = args.get(1).and_then(|s| s.parse().ok()).unwrap_or(6);
            run_perft(depth, args.iter().any(|a| a == "--divide"));
        }
        Some("bench") => {
            let ms = args.get(1).and_then(|s| s.parse().ok()).unwrap_or(2000);
            let mut params = SearchParams::default();
            if let Some(i) = args.iter().position(|a| a == "--params") {
                match args.get(i + 1) {
                    Some(spec) => {
                        if let Err(e) = params.apply_overrides(spec) {
                            eprintln!("error: {e}");
                            std::process::exit(2);
                        }
                    }
                    None => {
                        eprintln!("error: --params needs a value");
                        std::process::exit(2);
                    }
                }
            }
            run_bench(ms, &params);
        }
        Some("match") => {
            if let Err(e) = run_match_cmd(&args[1..]) {
                eprintln!("error: {e}");
                std::process::exit(2);
            }
        }
        _ => {
            eprintln!(
                "usage: jungle <protocol | bench [ms] | perft [depth] [--divide] | match [opts]>\n\
                 \n\
                 bench options:\n  \
                   --params <k=v,...>  parameter overrides (for pricing a speedup)\n\
                 \n\
                 match options:\n  \
                   --a <k=v,...>     parameter overrides for engine A (default: shipped tuning)\n  \
                   --b <k=v,...>     parameter overrides for engine B\n  \
                   --b-baseline      engine B is every optional heuristic off\n  \
                   --a-eval <k=v,...>  evaluation overrides for A (pv1..pv8, den_threat, ...)\n  \
                   --b-eval <k=v,...>  evaluation overrides for B\n  \
                   --nodes N         node budget per move (default 25000)\n  \
                   --movetime MS     use a clock instead of nodes (not reproducible)\n  \
                   --games N         maximum games (default 1000)\n  \
                   --threads N       worker threads (default: available parallelism)\n  \
                   --openings N      random opening plies (default 6)\n  \
                   --seed N          opening seed (default 20260815)\n  \
                   --sprt e0:e1:a:b  sequential test, e.g. 0:5:0.05:0.05\n  \
                   --hash MB         transposition table per engine (default 16)"
            );
            std::process::exit(2);
        }
    }
}

fn parse_move(tok: &str) -> Option<Move> {
    let v: Vec<&str> = tok.split(',').collect();
    if v.len() != 4 {
        return None;
    }
    let fc: usize = v[0].parse().ok()?;
    let fr: usize = v[1].parse().ok()?;
    let tc: usize = v[2].parse().ok()?;
    let tr: usize = v[3].parse().ok()?;
    Some(Move::new(
        jungle_core::sq(fc, fr),
        jungle_core::sq(tc, tr),
    ))
}

fn format_move(m: Move) -> String {
    format!(
        "{},{},{},{}",
        col_of(m.from()),
        row_of(m.from()),
        col_of(m.to()),
        row_of(m.to())
    )
}

/// Line protocol.
///
/// ```text
/// newgame                                   forget the table and history
/// position startpos [moves fc,fr,tc,tr ...] replay a game, keeping repetition history
/// position board <63 chars> <B|K> <halfmove>
/// go nodes <n> | go movetime <ms> | go depth <d>
///   -> info depth <d> seldepth <d> score <cp|mate n> nodes <n> time <ms>
///   -> bestmove <fc,fr,tc,tr> | bestmove none
/// quit
/// ```
fn protocol() {
    let stdin = io::stdin();
    let mut out = io::stdout();
    let mut searcher = Searcher::new(128);
    let mut pos = Position::startpos();

    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        let tokens: Vec<&str> = line.split_whitespace().collect();
        let Some(&cmd) = tokens.first() else { continue };

        match cmd {
            "quit" => break,
            "isready" => {
                let _ = writeln!(out, "readyok");
                let _ = out.flush();
            }
            "newgame" => {
                searcher.reset();
                pos = Position::startpos();
            }
            "position" => {
                match tokens.get(1).copied() {
                    Some("startpos") => {
                        pos = Position::startpos();
                        if let Some(i) = tokens.iter().position(|&t| t == "moves") {
                            for tok in &tokens[i + 1..] {
                                match parse_move(tok) {
                                    Some(m) if generate(&pos).as_slice().contains(&m) => pos.make(m),
                                    _ => {
                                        let _ = writeln!(out, "error illegal move {tok}");
                                        let _ = out.flush();
                                        break;
                                    }
                                }
                            }
                        }
                    }
                    Some("board") => {
                        let Some(board) = tokens.get(2) else { continue };
                        match Position::from_board_string(board) {
                            Ok(p) => {
                                pos = p;
                                if tokens.get(3) == Some(&"K") {
                                    pos.set_side_to_move(Color::Black);
                                }
                                if let Some(h) = tokens.get(4).and_then(|s| s.parse().ok()) {
                                    pos.set_halfmove_clock(h);
                                }
                            }
                            Err(e) => {
                                let _ = writeln!(out, "error {e}");
                                let _ = out.flush();
                            }
                        }
                    }
                    _ => {}
                }
            }
            "go" => {
                let limits = match (tokens.get(1).copied(), tokens.get(2)) {
                    (Some("nodes"), Some(n)) => Limits::nodes(n.parse().unwrap_or(10_000)),
                    (Some("movetime"), Some(ms)) => Limits::movetime(ms.parse().unwrap_or(1000)),
                    (Some("depth"), Some(d)) => Limits::depth(d.parse().unwrap_or(6)),
                    _ => Limits::movetime(1000),
                };
                let r = searcher.think(&mut pos, &limits);
                let score = match jungle_search::mate_distance(r.score) {
                    Some(n) => format!("mate {n}"),
                    None => format!("cp {}", r.score),
                };
                let _ = writeln!(
                    out,
                    "info depth {} seldepth {} score {score} nodes {} time {}",
                    r.depth,
                    r.seldepth,
                    r.nodes,
                    r.elapsed.as_millis()
                );
                match r.best_move {
                    Some(m) => {
                        let _ = writeln!(out, "bestmove {}", format_move(m));
                    }
                    None => {
                        let _ = writeln!(out, "bestmove none");
                    }
                }
                let _ = out.flush();
            }
            _ => {}
        }
    }
}

fn run_perft(max_depth: u32, divide: bool) {
    use std::time::Instant;
    println!(
        "{:>5}  {:>16}  {:>8}  {:>14}",
        "depth", "leaves", "time_s", "leaves/s"
    );
    for depth in 1..=max_depth {
        let mut pos = Position::startpos();
        let t0 = Instant::now();
        let leaves = perft(&mut pos, depth);
        let dt = t0.elapsed().as_secs_f64();
        println!(
            "{depth:>5}  {leaves:>16}  {dt:>8.3}  {:>14.0}",
            leaves as f64 / dt.max(1e-9)
        );
    }
    if divide {
        let mut pos = Position::startpos();
        for (m, n) in perft_divide(&mut pos, max_depth) {
            println!("  {m}  {n}");
        }
    }
}

/// `params` are `name=value` overrides, as `match --a` takes.
///
/// A change that only makes the search *faster* measures as exactly zero in a
/// node-limited match, so pricing one needs either a clock match — slow, and
/// distorted by whatever else is running — or this: the same positions, the same
/// budget, with and without the flag, reading nodes per second.
fn run_bench(budget_ms: u64, params: &SearchParams) {
    use std::time::Instant;
    let mut total_nodes = 0u64;
    let mut total_time = 0.0;
    println!(
        "{:<12} {:>5} {:>7} {:>12} {:>12} {:>7}",
        "position", "depth", "seldep", "nodes", "nps", "time_s"
    );
    for (name, board) in [
        (
            "fixed-mid",
            "g.....f.......a.e.d.h.....................H.D.E.A.......F.....G",
        ),
        (
            "midgame-1",
            "g....b..c....fae...dh.....................H.D.E..B....CA..F...G",
        ),
        (
            "midgame-2",
            "g....f.c....bh..ed....a...................HD...EA.FB..C......G.",
        ),
    ] {
        let mut pos = Position::from_board_string(board).expect("bad bench position");
        let mut s = Searcher::with_params(64, *params);
        let t0 = Instant::now();
        let r = s.think(&mut pos, &Limits::movetime(budget_ms));
        let dt = t0.elapsed().as_secs_f64();
        println!(
            "{name:<12} {:>5} {:>7} {:>12} {:>12.0} {:>7.2}",
            r.depth,
            r.seldepth,
            r.nodes,
            r.nodes as f64 / dt.max(1e-9),
            dt
        );
        total_nodes += r.nodes;
        total_time += dt;
    }
    println!(
        "\ntotal: {total_nodes} nodes in {total_time:.2}s = {:.0} nps",
        total_nodes as f64 / total_time.max(1e-9)
    );
}

/// `jungle match` — self-play A/B with Elo, confidence interval and SPRT.
fn run_match_cmd(args: &[String]) -> Result<(), String> {
    use matchrun::{elo_interval, run_match, sprt_llr, MatchConfig, Sprt, Tally};

    let mut a = SearchParams::default();
    let mut b = SearchParams::default();
    let mut a_eval = EvalParams::default();
    let mut b_eval = EvalParams::default();
    let mut nodes = 25_000u64;
    let mut movetime: Option<u64> = None;
    let mut games = 1000usize;
    let mut threads = std::thread::available_parallelism().map_or(4, |n| n.get());
    let mut openings = 6usize;
    let mut seed = 20_260_815u64;
    let mut hash = 16usize;
    let mut sprt = None;

    let value = |i: usize| -> Result<&String, String> {
        args.get(i + 1)
            .ok_or_else(|| format!("{} needs a value", args[i]))
    };

    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--a" => { a.apply_overrides(value(i)?)?; i += 2; }
            "--b" => { b.apply_overrides(value(i)?)?; i += 2; }
            "--b-baseline" => { b = SearchParams::baseline(); i += 1; }
            "--a-eval" => { a_eval.apply_overrides(value(i)?)?; i += 2; }
            "--b-eval" => { b_eval.apply_overrides(value(i)?)?; i += 2; }
            "--nodes" => { nodes = value(i)?.parse().map_err(|_| "bad --nodes".to_string())?; i += 2; }
            "--movetime" => { movetime = Some(value(i)?.parse().map_err(|_| "bad --movetime".to_string())?); i += 2; }
            "--games" => { games = value(i)?.parse().map_err(|_| "bad --games".to_string())?; i += 2; }
            "--threads" => { threads = value(i)?.parse().map_err(|_| "bad --threads".to_string())?; i += 2; }
            "--openings" => { openings = value(i)?.parse().map_err(|_| "bad --openings".to_string())?; i += 2; }
            "--seed" => { seed = value(i)?.parse().map_err(|_| "bad --seed".to_string())?; i += 2; }
            "--hash" => { hash = value(i)?.parse().map_err(|_| "bad --hash".to_string())?; i += 2; }
            "--sprt" => {
                let parts: Vec<f64> = value(i)?.split(':').map(|p| p.parse().unwrap_or(f64::NAN)).collect();
                if parts.len() != 4 || parts.iter().any(|v| v.is_nan()) {
                    return Err("--sprt wants elo0:elo1:alpha:beta".into());
                }
                sprt = Some(Sprt::new(parts[0], parts[1], parts[2], parts[3]));
                i += 2;
            }
            other => return Err(format!("unknown option {other:?}")),
        }
    }

    if a == b {
        eprintln!("note: A and B are identical; this is an A/A run and should come out even.");
    }

    let limits = match movetime {
        Some(ms) => Limits::movetime(ms),
        None => Limits::nodes(nodes),
    };
    let budget = match movetime {
        Some(ms) => format!("{ms}ms/move (not reproducible)"),
        None => format!("{nodes} nodes/move"),
    };

    println!("Match: A vs B | {budget} | games<={games} threads={threads} seed={seed}");
    if let Some(ref s) = sprt {
        println!("SPRT: H0 elo<={} H1 elo>={} bounds [{:.2}, {:.2}]", s.elo0, s.elo1, s.lower, s.upper);
    }
    println!("{}", "-".repeat(70));

    let start = std::time::Instant::now();
    let reported = std::sync::atomic::AtomicUsize::new(0);
    let cfg = MatchConfig {
        a, b, a_eval, b_eval, games, opening_plies: openings, seed,
        max_moves: 250, threads, tt_megabytes: hash, limits, sprt,
    };

    let tally = run_match(&cfg, |t: &Tally| {
        let n = t.games() as usize;
        if n % 50 == 0 && reported.swap(n, std::sync::atomic::Ordering::Relaxed) != n {
            let (elo, lo, hi) = elo_interval(t);
            println!("  {n:>5} games  W{}-L{}-D{}  {:.1}%  Elo {elo:+.0} [{lo:+.0}, {hi:+.0}]",
                     t.a_wins, t.b_wins, t.draws, t.score() * 100.0);
        }
    });

    let (elo, lo, hi) = elo_interval(&tally);
    println!("{}", "-".repeat(70));
    println!("A wins   : {}", tally.a_wins);
    println!("B wins   : {}", tally.b_wins);
    println!("draws    : {}", tally.draws);
    println!("games    : {}", tally.games());
    println!("A score  : {:.1}%", tally.score() * 100.0);
    println!("Elo      : {elo:+.0} [{lo:+.0}, {hi:+.0}]");

    if let Some(ref s) = cfg.sprt {
        let llr = sprt_llr(&tally, s.elo0, s.elo1);
        let verdict = match s.verdict(&tally) {
            Some(true) => "H1 ACCEPTED - A is better",
            Some(false) => "H0 accepted - A is not better",
            None => "inconclusive - neither bound reached",
        };
        println!("LLR      : {llr:+.2}  ->  {verdict}");
    } else if lo > 0.0 {
        println!("verdict  : A is stronger (95% CI excludes parity)");
    } else if hi < 0.0 {
        println!("verdict  : A is WEAKER (95% CI excludes parity)");
    } else {
        println!("verdict  : inconclusive - CI spans parity");
    }
    println!("elapsed  : {:.1}s", start.elapsed().as_secs_f64());
    Ok(())
}
