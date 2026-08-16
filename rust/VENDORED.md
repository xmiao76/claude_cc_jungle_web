# Vendored Rust engine — provenance

This workspace is a vendored copy, not original work of this repository.

| | |
|---|---|
| Source | https://github.com/xmiao76/claude_cc_jungle — `rust/` |
| Commit | `0d3475388784fcc38ff99f8d93687fff6b9d0f36` (`0d34753`, 2026-08-16) |
| Subject | *docs: measure against main's v1.5 engine, and correct the perft figures* |

It was vendored rather than submoduled because GitHub Actions cannot reach a
local sibling checkout, and a submodule would complicate the Cloudflare Pages
build for no benefit here.

## What differs from upstream

| Change | Why |
|---|---|
| `crates/jungle-py` **not copied** | The PyO3 extension has no consumer in a browser build, and it drags `pyo3 -> pyo3-ffi -> libc` plus a build-time Python interpreter into CI. Recover it from upstream if a Python↔Rust cross-match is ever wanted. |
| `crates/jungle-wasm` **added** | The `wasm-bindgen` facade this repository ships. Modelled on upstream's `jungle-py/src/lib.rs`, which is the same job for a different host. |
| `jungle-search` time source | `std::time::Instant` panics on `wasm32-unknown-unknown`. Replaced with a platform shim; native behaviour is unchanged and the frozen perft and tactics tests prove it. |

Improvements made here to `jungle-core` / `jungle-eval` / `jungle-search` should
be portable back upstream unchanged. Keep them that way.

## The three instruments

Two complete implementations of the rules now live in this repository — Python in
`engine-python/engine/`, Rust here — and that is normally how rule sets quietly
diverge. It is safe only because the agreement is *measured*, not assumed:

| Instrument | What it pins | Python side | Rust side |
|---|---|---|---|
| Frozen perft counts | Move generation, exhaustively | `tests/python/test_perft.py` | `crates/jungle-core/tests/perft.rs` |
| Golden position corpus | Legal moves, terminal status, winner on 10,000 positions | `tests/python/test_golden.py` | `crates/jungle-core/tests/golden.rs` |
| Golden evaluation corpus | Static evaluation, score for score, on the same 10,000 | *(none — see below)* | `crates/jungle-eval/tests/golden_evals.rs` |

Both corpora live in `tests/golden/` and are regenerated together with
`python -m tools.golden`. Rust additionally reproduces perft(6) = 100,453,636 and
perft(7) = 1,908,199,299.

`test_corpus_regenerates_byte_for_byte` is the strongest of these: the sampler
drives itself with `rng.choice(gs.legal_moves())`, so reproducing the committed
file proves the two engines agree on move *order* as well as move *content*, and
on every position the walk visits rather than only the 10,000 it keeps.

## Two divergences found when vendoring

### 1. The trap rule — fixed in this repository's Python

`golden.rs` passed against the corpus; this repository's Python disagreed with it
on 15 positions in 10,000. Fourteen were one bug: `engine-python/engine/rules.py`
applied the trap's rank-0 effect to the **attacker** as well as the defender, so a
piece standing in an enemy trap could not capture out of it. The correct rule,
which upstream documents and `rules.rs` implements, is that a trapped piece is
*vulnerable, not disarmed* — it is taken by anything, but it still attacks at its
real rank. The fifteenth was the water boundary being gated behind "a Rat is
involved" rather than applied to every piece; that one is only reachable in
constructed positions, since no non-Rat can legally enter the river.

Both were fixed in `rules.py`. The fix cost nothing: perft is unchanged, all five
fixed-depth signature pins in `test_strength.py` are unchanged, all 147 Python
tests still pass, and corpus regeneration became byte-exact.

It does change gameplay. Captures out of an enemy trap are legal now, and were
not before — reachable and relevant near both dens.

### 2. The evaluation corpus is Rust-only, on purpose

`jungle-eval` is a deliberately verbatim port of upstream's v1.3-era evaluator —
upstream's reasoning being that "a port that changes behaviour cannot be verified
as a port". This repository's Python evaluator is the v1.6 lineage and is a
different implementation, not a differently-configured one; no flag combination
makes them agree, and `v13_strong_config()` still differs on 36% of the corpus.

So `evals.txt.gz` stays as upstream generated it and pins `jungle-eval` against
accidental drift, which is that corpus's real job. `tools/golden.py:collect_evals`
scores with `v13_strong_config()` — the nearest thing this repository has to the
ported evaluator — so that regenerating it is at least meaningful, but the Python
side is not asserted against it.

Worth carrying into the evaluation work: the two lineages **contradict each other
on the hanging-piece term.** This repository ships `use_hanging_penalty=True`;
upstream measured it at **-255 Elo [-425, -151]** and ships it off, and that is
the one per-flag result in the whole project whose confidence interval excludes
parity. Neither engine has re-measured it on a fast searcher. It is the first
thing to put through SPRT.
