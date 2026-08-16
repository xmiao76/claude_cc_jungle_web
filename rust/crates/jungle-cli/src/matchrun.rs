//! Self-play A/B matches with a sequential probability ratio test.
//!
//! This is the instrument the rest of the work depends on. The Python harness
//! could run maybe 200 games of a slow engine in an evening, which is why most of
//! the per-flag Elo numbers recorded in `ai/search_config.py` turn out, when put
//! back through that harness's own statistics, to be inconclusive. Thousands of
//! games of a native engine take minutes, so "measure, do not assume" stops being
//! aspirational.
//!
//! Two design choices make the numbers trustworthy:
//!
//! * **Fixed nodes, not fixed time.** A node budget is deterministic, so a match
//!   is reproducible and completely immune to what else the machine is doing.
//!   Timed matches are neither, and a timed A/B on a busy machine measures the
//!   scheduler as much as the engine. The exception is a change that is *purely*
//!   a speedup, which a node-limited match cannot see at all — price those on the
//!   clock instead, and say so.
//! * **Paired colour-swapped openings.** Each opening is played twice with the
//!   sides reversed, which cancels both the first-move advantage and any luck in
//!   the opening itself.

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Mutex;

use jungle_core::position::Position;
use jungle_core::types::Color;
use jungle_search::{EvalParams, Limits, SearchParams, Searcher};

/// xorshift64*, so the harness needs no dependency and every run is reproducible.
pub struct Rng(pub u64);

impl Rng {
    pub fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    pub fn below(&mut self, n: usize) -> usize {
        (self.next() % n as u64) as usize
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct Tally {
    pub a_wins: u32,
    pub b_wins: u32,
    pub draws: u32,
}

impl Tally {
    pub fn games(&self) -> u32 {
        self.a_wins + self.b_wins + self.draws
    }
    pub fn score(&self) -> f64 {
        let n = self.games();
        if n == 0 {
            return 0.5;
        }
        (self.a_wins as f64 + 0.5 * self.draws as f64) / n as f64
    }
}

/// Elo from a score, matching `tools/strength_harness.py:score_to_elo` so the two
/// instruments report the same number for the same result.
pub fn score_to_elo(score: f64) -> f64 {
    if score <= 0.0 {
        return -800.0;
    }
    if score >= 1.0 {
        return 800.0;
    }
    -400.0 * (1.0 / score - 1.0).log10()
}

/// 95% confidence interval on the Elo, from the observed per-game variance.
pub fn elo_interval(t: &Tally) -> (f64, f64, f64) {
    let n = t.games() as f64;
    let s = t.score();
    if n < 2.0 {
        return (score_to_elo(s), -800.0, 800.0);
    }
    let var = (t.a_wins as f64 * (1.0 - s).powi(2)
        + t.draws as f64 * (0.5 - s).powi(2)
        + t.b_wins as f64 * s.powi(2))
        / n;
    let stderr = (var / n).sqrt();
    let lo = (s - 1.96 * stderr).clamp(0.0, 1.0);
    let hi = (s + 1.96 * stderr).clamp(0.0, 1.0);
    (score_to_elo(s), score_to_elo(lo), score_to_elo(hi))
}

/// Log-likelihood ratio for H0: elo <= elo0 against H1: elo >= elo1.
///
/// Uses the observed draw rate to pin the trinomial model, which is the usual
/// practical approach: draws carry no information about which side is better,
/// only about how much information each decisive game carries.
pub fn sprt_llr(t: &Tally, elo0: f64, elo1: f64) -> f64 {
    let n = t.games() as f64;
    if n == 0.0 {
        return 0.0;
    }
    let draw_rate = (t.draws as f64 / n).clamp(0.0, 0.999);
    let model = |elo: f64| {
        let s = 1.0 / (1.0 + 10f64.powf(-elo / 400.0));
        let w = (s - draw_rate / 2.0).clamp(1e-9, 1.0);
        let l = (1.0 - s - draw_rate / 2.0).clamp(1e-9, 1.0);
        (w, l)
    };
    let (w0, l0) = model(elo0);
    let (w1, l1) = model(elo1);
    t.a_wins as f64 * (w1 / w0).ln() + t.b_wins as f64 * (l1 / l0).ln()
}

pub struct Sprt {
    pub elo0: f64,
    pub elo1: f64,
    pub lower: f64,
    pub upper: f64,
}

impl Sprt {
    pub fn new(elo0: f64, elo1: f64, alpha: f64, beta: f64) -> Sprt {
        Sprt {
            elo0,
            elo1,
            lower: (beta / (1.0 - alpha)).ln(),
            upper: ((1.0 - beta) / alpha).ln(),
        }
    }

    /// `Some(true)` accepts H1 (A is better), `Some(false)` accepts H0.
    pub fn verdict(&self, t: &Tally) -> Option<bool> {
        let llr = sprt_llr(t, self.elo0, self.elo1);
        if llr >= self.upper {
            Some(true)
        } else if llr <= self.lower {
            Some(false)
        } else {
            None
        }
    }
}

/// Play one game and return the winner, or None for a draw or a move-cap finish.
#[allow(clippy::too_many_arguments)]
pub fn play_game(
    a: SearchParams,
    b: SearchParams,
    a_eval: EvalParams,
    b_eval: EvalParams,
    a_is_blue: bool,
    opening_seed: u64,
    opening_plies: usize,
    limits: &Limits,
    max_moves: usize,
    tt_megabytes: usize,
) -> Option<Color> {
    let mut pos = Position::startpos();
    let mut rng = Rng(opening_seed | 1);
    for _ in 0..opening_plies {
        let moves = jungle_core::generate(&pos);
        if moves.is_empty() || pos.result().is_some() {
            break;
        }
        pos.make(moves[rng.below(moves.len())]);
    }

    // One searcher per side for the whole game, so each keeps its transposition
    // table and history across moves the way it would in real play.
    let (blue_p, blue_e) = if a_is_blue { (a, a_eval) } else { (b, b_eval) };
    let (black_p, black_e) = if a_is_blue { (b, b_eval) } else { (a, a_eval) };
    let mut blue = Searcher::with_all_params(tt_megabytes, blue_p, blue_e);
    let mut black = Searcher::with_all_params(tt_megabytes, black_p, black_e);

    for _ in 0..max_moves {
        if pos.is_terminal() {
            break;
        }
        let searcher = if pos.side_to_move() == Color::Blue {
            &mut blue
        } else {
            &mut black
        };
        let result = searcher.think(&mut pos, limits);
        match result.best_move {
            Some(m) => pos.make(m),
            None => break,
        }
    }

    pos.winner()
}

pub struct MatchConfig {
    pub a: SearchParams,
    pub b: SearchParams,
    pub a_eval: EvalParams,
    pub b_eval: EvalParams,
    pub games: usize,
    pub opening_plies: usize,
    pub seed: u64,
    pub max_moves: usize,
    pub threads: usize,
    pub tt_megabytes: usize,
    pub limits: Limits,
    pub sprt: Option<Sprt>,
}

/// Run the match, returning the tally from A's point of view.
///
/// Games are handed out from a shared counter rather than split up front, so a
/// slow game does not leave one thread finishing alone. With an SPRT configured
/// the workers stop as soon as a verdict is reached.
pub fn run_match(cfg: &MatchConfig, progress: impl Fn(&Tally) + Sync) -> Tally {
    let pairs = cfg.games.div_ceil(2);
    let total = pairs * 2;
    let next = AtomicUsize::new(0);
    let done = AtomicBool::new(false);
    let tally = Mutex::new(Tally::default());

    std::thread::scope(|scope| {
        for _ in 0..cfg.threads.max(1) {
            scope.spawn(|| {
                loop {
                    if done.load(Ordering::Relaxed) {
                        return;
                    }
                    let i = next.fetch_add(1, Ordering::Relaxed);
                    if i >= total {
                        return;
                    }
                    // Pair i/2 is played twice, once with each side as A.
                    let a_is_blue = i % 2 == 0;
                    let seed = cfg.seed.wrapping_add((i / 2) as u64).wrapping_mul(0x9E37_79B9);

                    let winner = play_game(
                        cfg.a,
                        cfg.b,
                        cfg.a_eval,
                        cfg.b_eval,
                        a_is_blue,
                        seed,
                        cfg.opening_plies,
                        &cfg.limits,
                        cfg.max_moves,
                        cfg.tt_megabytes,
                    );

                    let snapshot = {
                        let mut t = tally.lock().expect("tally mutex poisoned");
                        match winner {
                            None => t.draws += 1,
                            Some(c) => {
                                if (c == Color::Blue) == a_is_blue {
                                    t.a_wins += 1
                                } else {
                                    t.b_wins += 1
                                }
                            }
                        }
                        *t
                    };
                    progress(&snapshot);

                    if let Some(ref s) = cfg.sprt {
                        if s.verdict(&snapshot).is_some() {
                            done.store(true, Ordering::Relaxed);
                            return;
                        }
                    }
                }
            });
        }
    });

    let t = *tally.lock().expect("tally mutex poisoned");
    t
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn elo_matches_the_python_formula() {
        // Spot values from tools/strength_harness.py:score_to_elo.
        assert!((score_to_elo(0.5) - 0.0).abs() < 1e-9);
        assert!((score_to_elo(0.75) - 190.848).abs() < 0.01);
        assert!((score_to_elo(0.25) + 190.848).abs() < 0.01);
        assert_eq!(score_to_elo(0.0), -800.0);
        assert_eq!(score_to_elo(1.0), 800.0);
    }

    #[test]
    fn an_even_match_has_an_interval_spanning_zero() {
        let t = Tally { a_wins: 50, b_wins: 50, draws: 100 };
        let (elo, lo, hi) = elo_interval(&t);
        assert!(elo.abs() < 1e-6);
        assert!(lo < 0.0 && hi > 0.0);
    }

    #[test]
    fn sprt_accepts_h1_for_a_decisive_lead_and_h0_for_a_deficit() {
        let s = Sprt::new(0.0, 5.0, 0.05, 0.05);
        assert_eq!(s.verdict(&Tally::default()), None);

        let winning = Tally { a_wins: 900, b_wins: 100, draws: 200 };
        assert_eq!(s.verdict(&winning), Some(true));

        let losing = Tally { a_wins: 100, b_wins: 900, draws: 200 };
        assert_eq!(s.verdict(&losing), Some(false));
    }

    #[test]
    fn llr_is_zero_before_any_games() {
        assert_eq!(sprt_llr(&Tally::default(), 0.0, 5.0), 0.0);
    }

    #[test]
    fn identical_configurations_play_an_even_match() {
        // An A/A match must not favour either side: if it did, the harness itself
        // would be the source of any result it reported.
        let cfg = MatchConfig {
            a: SearchParams::default(),
            b: SearchParams::default(),
            a_eval: EvalParams::default(),
            b_eval: EvalParams::default(),
            games: 40,
            opening_plies: 6,
            seed: 4242,
            max_moves: 220,
            threads: 4,
            tt_megabytes: 8,
            limits: Limits::nodes(4_000),
            sprt: None,
        };
        let t = run_match(&cfg, |_| {});
        assert_eq!(t.games(), 40);
        let (_, lo, hi) = elo_interval(&t);
        assert!(lo <= 0.0 && hi >= 0.0, "A/A match was not even: {t:?}");
    }
}
