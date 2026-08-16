//! Negamax principal-variation search for Jungle.

pub mod clock;
pub mod ordering;
pub mod params;
pub mod score;
pub mod search;
pub mod see;
pub mod tt;

pub use score::{mate_distance, mate_in, mated_in, is_mate_score, EVAL_NONE, INF, MATE, MAX_PLY};
pub use params::SearchParams;
pub use jungle_eval::EvalParams;
pub use search::{Limits, SearchResult, Searcher};
pub use see::{see, see_with};
pub use tt::TranspositionTable;
