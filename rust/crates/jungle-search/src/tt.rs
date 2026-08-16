//! Transposition table: a flat array of four-entry buckets.
//!
//! The Python engine used a `dict` keyed by the full 64-bit hash, with a
//! `TTEntry` object allocated on every hit and an eviction path that, once the
//! table filled with current-generation entries, *sorted a million integers* to
//! find a median depth -- in the middle of a search, contradicting the docstring
//! directly above it that promised it never did. Here the table is a fixed
//! allocation indexed by masking, entries are 16 bytes of plain data, and
//! replacement looks at four slots.
//!
//! Entries store only the upper 32 bits of the key. The lower bits already chose
//! the bucket, so storing them again would be redundant; 32 bits of verification
//! makes a false hit vanishingly rare, and the search validates the TT move
//! against the real move list anyway.

use jungle_core::types::Move;

pub const BOUND_NONE: u8 = 0;
pub const BOUND_EXACT: u8 = 1;
pub const BOUND_LOWER: u8 = 2;
pub const BOUND_UPPER: u8 = 3;

const BUCKET: usize = 4;
const GENERATION_MASK: u8 = 0x3F;

#[derive(Clone, Copy, Default)]
#[repr(C)]
struct Entry {
    key: u32,
    mv: u16,
    score: i16,
    eval: i16,
    depth: i8,
    /// Bound in bits 0-1, generation in bits 2-7.
    meta: u8,
    _pad: [u8; 4],
}

impl Entry {
    #[inline(always)]
    fn bound(&self) -> u8 {
        self.meta & 0x3
    }
    #[inline(always)]
    fn generation(&self) -> u8 {
        self.meta >> 2
    }
}

/// What a probe found.
pub struct Hit {
    pub mv: Option<Move>,
    pub score: i32,
    pub eval: i32,
    pub depth: i8,
    pub bound: u8,
}

pub struct TranspositionTable {
    buckets: Vec<[Entry; BUCKET]>,
    mask: usize,
    generation: u8,
}

impl TranspositionTable {
    /// Allocate a table of roughly `megabytes` MiB, rounded down to a power of
    /// two number of buckets.
    pub fn new(megabytes: usize) -> TranspositionTable {
        let bucket_bytes = core::mem::size_of::<[Entry; BUCKET]>();
        let wanted = (megabytes.max(1) * 1024 * 1024) / bucket_bytes;
        let count = wanted.next_power_of_two() / 2;
        let count = count.max(1024);
        TranspositionTable {
            buckets: vec![[Entry::default(); BUCKET]; count],
            mask: count - 1,
            generation: 0,
        }
    }

    pub fn clear(&mut self) {
        for b in self.buckets.iter_mut() {
            *b = [Entry::default(); BUCKET];
        }
        self.generation = 0;
    }

    /// Start a new search. Entries from older generations become preferred
    /// eviction candidates, which is what stops a deep entry from an unreachable
    /// position holding its slot for the rest of the game.
    pub fn new_generation(&mut self) {
        self.generation = (self.generation + 1) & GENERATION_MASK;
    }

    #[inline(always)]
    fn index(&self, key: u64) -> usize {
        (key as usize) & self.mask
    }

    #[inline(always)]
    fn verify(key: u64) -> u32 {
        (key >> 32) as u32
    }

    pub fn probe(&self, key: u64, ply: i32) -> Option<Hit> {
        let bucket = &self.buckets[self.index(key)];
        let want = Self::verify(key);
        for e in bucket.iter() {
            if e.bound() != BOUND_NONE && e.key == want {
                return Some(Hit {
                    mv: if e.mv == 0 { None } else { Some(Move(e.mv)) },
                    score: score_from_tt(e.score as i32, ply),
                    eval: e.eval as i32,
                    depth: e.depth,
                    bound: e.bound(),
                });
            }
        }
        None
    }

    // Eight parameters is a lot, but every one of them is a distinct fact about
    // the entry and bundling them into a struct would only move the argument list
    // to the call site.
    #[allow(clippy::too_many_arguments)]
    pub fn store(
        &mut self,
        key: u64,
        mv: Option<Move>,
        score: i32,
        eval: i32,
        depth: i8,
        bound: u8,
        ply: i32,
    ) {
        let idx = self.index(key);
        let want = Self::verify(key);
        let generation = self.generation;

        let bucket = &mut self.buckets[idx];

        // Prefer the slot already describing this position; otherwise evict the
        // least valuable one. An entry from an older generation is worth less
        // than any current entry regardless of depth, because the position it
        // describes may no longer be reachable.
        let mut victim = 0usize;
        let mut victim_rank = i32::MAX;
        let mut found = None;
        for (i, e) in bucket.iter().enumerate() {
            if e.bound() != BOUND_NONE && e.key == want {
                found = Some(i);
                break;
            }
            let stale = e.bound() == BOUND_NONE || e.generation() != generation;
            let rank = if stale { -1000 + e.depth as i32 } else { e.depth as i32 };
            if rank < victim_rank {
                victim_rank = rank;
                victim = i;
            }
        }

        let slot = match found {
            Some(i) => {
                // Keep a deeper, still-current entry rather than overwriting it
                // with a shallower one -- unless we have an exact score, which is
                // strictly more useful than a bound.
                let e = &bucket[i];
                if e.generation() == generation
                    && e.depth > depth
                    && bound != BOUND_EXACT
                {
                    return;
                }
                i
            }
            None => victim,
        };

        // Never drop a known best move for a probe that did not produce one.
        let keep_move = match mv {
            Some(m) => m.0,
            None if bucket[slot].key == want => bucket[slot].mv,
            None => 0,
        };

        bucket[slot] = Entry {
            key: want,
            mv: keep_move,
            score: score_to_tt(score, ply).clamp(i16::MIN as i32, i16::MAX as i32) as i16,
            eval: eval.clamp(i16::MIN as i32, i16::MAX as i32) as i16,
            depth,
            meta: (bound & 0x3) | (generation << 2),
            _pad: [0; 4],
        };
    }

    /// Fraction of slots in use, per mille, sampled from the first 1000 buckets.
    pub fn hashfull(&self) -> usize {
        let sample = self.buckets.len().min(250);
        let mut used = 0;
        for b in self.buckets.iter().take(sample) {
            for e in b.iter() {
                if e.bound() != BOUND_NONE && e.generation() == self.generation {
                    used += 1;
                }
            }
        }
        used * 1000 / (sample * BUCKET)
    }
}

use crate::score::{MATE_BOUND, MATE};

/// Mate scores are stored as distance-from-*this node*, not distance-from-root.
///
/// Without this the same entry read at a different ply reports a mate at the
/// wrong distance, and the engine either misses a forced win or hallucinates one.
#[inline(always)]
pub fn score_to_tt(score: i32, ply: i32) -> i32 {
    if score >= MATE_BOUND {
        score + ply
    } else if score <= -MATE_BOUND {
        score - ply
    } else {
        score
    }
}

#[inline(always)]
pub fn score_from_tt(score: i32, ply: i32) -> i32 {
    if score >= MATE_BOUND {
        (score - ply).min(MATE)
    } else if score <= -MATE_BOUND {
        (score + ply).max(-MATE)
    } else {
        score
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn entries_are_sixteen_bytes() {
        assert_eq!(core::mem::size_of::<Entry>(), 16);
        assert_eq!(core::mem::size_of::<[Entry; BUCKET]>(), 64);
    }

    #[test]
    fn store_and_probe_round_trip() {
        let mut tt = TranspositionTable::new(1);
        let mv = Move::new(3, 10);
        tt.store(0xDEAD_BEEF_1234_5678, Some(mv), 42, 7, 5, BOUND_EXACT, 0);
        let hit = tt.probe(0xDEAD_BEEF_1234_5678, 0).expect("should hit");
        assert_eq!(hit.score, 42);
        assert_eq!(hit.depth, 5);
        assert_eq!(hit.bound, BOUND_EXACT);
        assert_eq!(hit.mv, Some(mv));
    }

    #[test]
    fn a_different_key_does_not_hit() {
        let mut tt = TranspositionTable::new(1);
        tt.store(0x1111_2222_3333_4444, None, 1, 0, 3, BOUND_EXACT, 0);
        assert!(tt.probe(0x5555_6666_3333_4444, 0).is_none());
    }

    #[test]
    fn mate_scores_are_adjusted_by_ply() {
        let mut tt = TranspositionTable::new(1);
        let key = 0xABCD_1234_5678_9999;
        // A mate in 3 found at ply 5 is stored as distance-from-node...
        tt.store(key, None, MATE - 8, 0, 4, BOUND_EXACT, 5);
        // ...and read back at ply 5 as the same distance-from-root.
        assert_eq!(tt.probe(key, 5).unwrap().score, MATE - 8);
        // Read at a different ply, it reports the distance from *there*.
        assert_eq!(tt.probe(key, 2).unwrap().score, MATE - 5);
    }

    #[test]
    fn a_deeper_current_entry_is_not_overwritten_by_a_shallow_bound() {
        let mut tt = TranspositionTable::new(1);
        let key = 0x9999_8888_7777_6666;
        tt.store(key, None, 100, 0, 9, BOUND_EXACT, 0);
        tt.store(key, None, -100, 0, 2, BOUND_LOWER, 0);
        assert_eq!(tt.probe(key, 0).unwrap().depth, 9);
    }

    #[test]
    fn a_stale_generation_is_evicted_before_a_current_one() {
        let mut tt = TranspositionTable::new(1);
        // Fill one bucket with deep, old entries.
        let base = 0u64;
        for i in 0..BUCKET as u64 {
            tt.store(base | (i << 32), None, 1, 0, 40, BOUND_EXACT, 0);
        }
        tt.new_generation();
        // A shallow current entry must still find a home.
        let fresh = base | (99u64 << 32);
        tt.store(fresh, None, 7, 0, 1, BOUND_EXACT, 0);
        assert!(tt.probe(fresh, 0).is_some(), "current entry was refused");
    }

    #[test]
    fn a_stored_move_survives_a_moveless_store() {
        let mut tt = TranspositionTable::new(1);
        let key = 0x1234_5678_9ABC_DEF0;
        let mv = Move::new(5, 12);
        tt.store(key, Some(mv), 10, 0, 4, BOUND_EXACT, 0);
        tt.store(key, None, 20, 0, 4, BOUND_LOWER, 0);
        assert_eq!(tt.probe(key, 0).unwrap().mv, Some(mv));
    }
}
