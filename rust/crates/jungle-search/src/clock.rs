//! A millisecond clock that exists on every target this engine builds for.
//!
//! `std::time::Instant::now()` compiles on `wasm32-unknown-unknown` and then
//! panics — "time not implemented on this platform" — because that target has no
//! WASI and no clock syscall. The search called it unconditionally, in the
//! constructor and again at the top of every `think()`, so the panic did not wait
//! for a `movetime` limit: a node-limited or depth-limited search trapped just as
//! fast. That was the one thing standing between this engine and a browser.
//!
//! Time is a `f64` of milliseconds rather than an `Instant` so that both
//! implementations return the same type and the search does plain arithmetic. The
//! search never needs sub-millisecond resolution: it consults the clock once every
//! 2048 nodes and the shortest budget it is ever given is measured in hundreds of
//! milliseconds.

/// Milliseconds since a fixed origin. Only differences are meaningful.
#[cfg(not(target_arch = "wasm32"))]
pub fn now_ms() -> f64 {
    use std::sync::OnceLock;
    use std::time::Instant;

    static ORIGIN: OnceLock<Instant> = OnceLock::new();
    ORIGIN.get_or_init(Instant::now).elapsed().as_secs_f64() * 1000.0
}

/// Milliseconds since the Unix epoch, via `Date.now()`.
///
/// `performance.now()` would be monotonic where this is not, but reaching it
/// needs `web-sys` and a different global in a Worker than on the main thread,
/// and the failure it protects against is the wall clock being stepped during the
/// two-and-a-half seconds of a search. The search already tolerates a clock that
/// jumps forward: it keeps the best move from the interrupted iteration.
#[cfg(target_arch = "wasm32")]
pub fn now_ms() -> f64 {
    js_sys::Date::now()
}

#[cfg(test)]
mod tests {
    use super::now_ms;

    #[test]
    fn the_clock_advances_and_does_not_run_backwards() {
        let t0 = now_ms();
        let mut spin = 0u64;
        while now_ms() - t0 < 2.0 {
            spin = spin.wrapping_add(1);
            assert!(spin < 1_000_000_000, "clock never advanced");
        }
        assert!(now_ms() >= t0);
    }
}
