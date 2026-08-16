//! Evaluation weights and tables, ported verbatim from `config.py`.
//!
//! These are the *shipped* Python values, warts included, so the port can be
//! checked against the engine it replaces score for score. Tuning them is a later
//! phase and a separate, measured change; changing them here would make the
//! differential test meaningless.

use jungle_core::types::{COLS, ROWS};

/// Material by rank, index 0 unused. The linear `100 * rank` table.
///
/// This misprices Jungle -- rank order is not value order, since the Rat kills
/// the Elephant, swims, and blocks leaps -- but a Rat-premium alternative was
/// tried and measured, inconclusively, worse. Retuning belongs to the tuning
/// phase, with an instrument that can actually resolve the difference.
pub const PIECE_VALUES: [i32; 9] = [0, 100, 200, 300, 400, 500, 600, 700, 800];

pub const ADVANCEMENT_PER_ROW: i32 = 10;
pub const ADVANCEMENT_ACCELERATION: i32 = 6;
pub const DEN_PROXIMITY_MAX_DIST: i32 = 3;
pub const DEN_PROXIMITY_PER_STEP: i32 = 30;
pub const DEN_DEFENDER: i32 = 25;
pub const RAT_IN_WATER: i32 = 40;
pub const RAT_BLOCKS_RIVER: i32 = 35;
pub const RAT_ADJACENT_TO_ENEMY_ELEPHANT: i32 = 60;
/// Named "trap_control" in config.py but applied as a penalty for standing in
/// enemy traps. Kept at the same magnitude and sign as the original.
pub const TRAP_PENALTY: i32 = 80;
pub const JUMP_READY: i32 = 20;
pub const MOBILITY: i32 = 2;
pub const DEN_THREAT: i32 = 45;
pub const TEMPO: i32 = 10;
pub const PST_WEIGHT: i32 = 1;

/// Row index of the midline; advancement past it accelerates.
pub const MIDLINE: i32 = (ROWS / 2) as i32;

const fn build_pst() -> [[i32; COLS]; ROWS] {
    let col_weight = [0i32, 5, 9, 12, 9, 5, 0];
    let mut table = [[0i32; COLS]; ROWS];
    let mut adv = 0usize;
    while adv < ROWS {
        let mut c = 0usize;
        while c < COLS {
            let mut v = col_weight[c];
            if adv > ROWS / 2 {
                v += (adv as i32 - (ROWS / 2) as i32) * (col_weight[c] / 6);
            }
            table[adv][c] = v;
            c += 1;
        }
        adv += 1;
    }
    table
}

/// Indexed `[advancement][col]`, where advancement is measured from the piece's
/// *own* back rank. Column-symmetric, so the symmetric start position evaluates
/// to zero apart from the side-to-move bonus.
pub static PST: [[i32; COLS]; ROWS] = build_pst();

/// Every evaluation weight, as data rather than constants.
///
/// The constants above remain the defaults and the golden corpus still pins
/// them, so nothing changes by adding this. What it buys is that an evaluation
/// idea can now be *measured* — `SearchParams` made every search heuristic
/// switchable years before the evaluation got the same treatment, and the
/// asymmetry is why the search has a table of results and the evaluation has a
/// row of comments saying "measured inconclusively worse" at forty games.
///
/// `piece_values` lives here rather than beside the positional terms for a
/// reason worth keeping: evaluation, static exchange evaluation and quiescence
/// delta pruning must all read the *same* table or they disagree about what a
/// capture is worth. One struct, threaded everywhere, is what enforces that.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct EvalParams {
    pub piece_values: [i32; 9],
    pub advancement_per_row: i32,
    pub advancement_acceleration: i32,
    pub den_proximity_max_dist: i32,
    pub den_proximity_per_step: i32,
    pub den_defender: i32,
    pub rat_in_water: i32,
    pub rat_blocks_river: i32,
    pub rat_adjacent_to_enemy_elephant: i32,
    pub trap_penalty: i32,
    pub jump_ready: i32,
    pub mobility: i32,
    pub den_threat: i32,
    pub tempo: i32,
    pub pst_weight: i32,
}

impl Default for EvalParams {
    fn default() -> EvalParams {
        EvalParams {
            piece_values: PIECE_VALUES,
            advancement_per_row: ADVANCEMENT_PER_ROW,
            advancement_acceleration: ADVANCEMENT_ACCELERATION,
            den_proximity_max_dist: DEN_PROXIMITY_MAX_DIST,
            den_proximity_per_step: DEN_PROXIMITY_PER_STEP,
            den_defender: DEN_DEFENDER,
            rat_in_water: RAT_IN_WATER,
            rat_blocks_river: RAT_BLOCKS_RIVER,
            rat_adjacent_to_enemy_elephant: RAT_ADJACENT_TO_ENEMY_ELEPHANT,
            trap_penalty: TRAP_PENALTY,
            jump_ready: JUMP_READY,
            mobility: MOBILITY,
            den_threat: DEN_THREAT,
            tempo: TEMPO,
            pst_weight: PST_WEIGHT,
        }
    }
}

impl EvalParams {
    /// Parse `name=value` overrides. Piece values are `pv1`..`pv8` by rank.
    pub fn apply_overrides(&mut self, spec: &str) -> Result<(), String> {
        for token in spec.split(',').map(str::trim).filter(|t| !t.is_empty()) {
            let (name, value) = token
                .split_once('=')
                .ok_or_else(|| format!("expected name=value, got {token:?}"))?;
            let bad = || format!("bad value {value:?} for {name}");
            let v: i32 = value.parse().map_err(|_| bad())?;
            match name {
                "pv1" | "pv2" | "pv3" | "pv4" | "pv5" | "pv6" | "pv7" | "pv8" => {
                    let rank = name[2..].parse::<usize>().map_err(|_| bad())?;
                    self.piece_values[rank] = v;
                }
                "advancement_per_row" => self.advancement_per_row = v,
                "advancement_acceleration" => self.advancement_acceleration = v,
                "den_proximity_max_dist" => self.den_proximity_max_dist = v,
                "den_proximity_per_step" => self.den_proximity_per_step = v,
                "den_defender" => self.den_defender = v,
                "rat_in_water" => self.rat_in_water = v,
                "rat_blocks_river" => self.rat_blocks_river = v,
                "rat_adjacent_to_enemy_elephant" => self.rat_adjacent_to_enemy_elephant = v,
                "trap_penalty" => self.trap_penalty = v,
                "jump_ready" => self.jump_ready = v,
                "mobility" => self.mobility = v,
                "den_threat" => self.den_threat = v,
                "tempo" => self.tempo = v,
                "pst_weight" => self.pst_weight = v,
                other => return Err(format!("unknown evaluation parameter {other:?}")),
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pst_matches_the_python_table() {
        // Materialised values from config.py's _build_pst().
        assert_eq!(PST[0], [0, 5, 9, 12, 9, 5, 0]);
        assert_eq!(PST[4], [0, 5, 9, 12, 9, 5, 0]);
        assert_eq!(PST[5], [0, 5, 10, 14, 10, 5, 0]);
        assert_eq!(PST[6], [0, 5, 11, 16, 11, 5, 0]);
        assert_eq!(PST[7], [0, 5, 12, 18, 12, 5, 0]);
        assert_eq!(PST[8], [0, 5, 13, 20, 13, 5, 0]);
    }

    #[test]
    fn pst_is_column_symmetric() {
        // This is what makes the symmetric starting position evaluate to 0.
        for row in PST.iter() {
            for c in 0..COLS {
                assert_eq!(row[c], row[COLS - 1 - c]);
            }
        }
    }
}
