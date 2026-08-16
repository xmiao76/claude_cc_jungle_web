//! Static position evaluation for Jungle.
//!
//! Currently a hand-crafted evaluation ported verbatim from the Python engine, so
//! the two can be compared score for score. It is deliberately *not* retuned here:
//! a port that changes behaviour cannot be verified as a port.

pub mod hce;
pub mod params;

pub use hce::{evaluate, evaluate_with};
pub use params::EvalParams;
