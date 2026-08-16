# Strength log

Every search or evaluation change that was measured, whether or not it shipped.
Kept because the failures are the useful half: this project's history is full of
plausible ideas that lost strength, and a change with no entry here is a change
nobody has actually tested.

## How to measure

```bash
cd rust
cargo run --release -p jungle-cli --bin jungle -- match \
    --a use_iir=true --nodes 25000 --games 1000 --sprt 0:5:0.05:0.05 --threads 8
```

`--a` applies parameter overrides to engine A; with no `--b`, engine B is
`SearchParams::default()` — the shipped tuning. Openings are 6 random plies,
paired and colour-swapped so each is played twice with the sides reversed.

Evaluation weights go through `--a-eval` / `--b-eval` instead, with the same
shape: `--a-eval pv1=250,den_threat=60`.

**Pick the budget to match the claim.** `--nodes` is deterministic and immune to
what else the machine is doing, and is right for anything that changes *which*
moves get searched. It is blind to anything that only changes *how fast* they get
searched — a pure speedup measures as exactly zero. Price those with
`bench --params` and compare nodes per second:

```bash
cargo run --release -p jungle-cli --bin jungle -- bench 3000 --params use_tt_eval=false
cargo run --release -p jungle-cli --bin jungle -- bench 3000 --params use_tt_eval=true
```

`--movetime` on a match works too but is slower and distorted by whatever else is
running, including the match's own other threads.

**Read the interval, not the percentage, and use enough games.** At 200 games one
standard error is about 25 Elo. `use_see_prune` below read +15 at 600 games and +6
[−4, +16] at 2000 — nothing changed but the sample. Two thousand games take about
six minutes on eight threads.

## Results

Baseline for every row is `SearchParams::default()` at the time of the run.

| Change | Budget | Games | Score | Elo [95%] | Shipped |
|---|---|---|---|---|---|
| *(sanity)* shipped tuning vs `--b-baseline` | 8k nodes | 400 | 63.1% | **+93 [+64, +124]** | — |
| `use_improving` | 25k nodes | 600 | 52.7% | +19 [+0, +37] | — |
| `use_improving` | 25k nodes | **2000** | 53.7% | **+26 [+15, +36]** | **yes** |
| `use_see_prune` | 25k nodes | 600 | 52.2% | +15 [−4, +34] | — |
| `use_see_prune` | 25k nodes | **2000** | 50.8% | +6 [−4, +16] | no |
| `use_see_prune` + `use_improving` | 25k nodes | 2000 | 53.1% | +22 [+12, +32] | no |
| `use_iir` | 25k nodes | 1000 | 47.9% | **−15 [−30, −0]** | no |
| `use_iir`, `iir_min_depth=8` | 25k nodes | 1000 | 49.2% | −6 [−20, +9] | no |
| `use_lmr_log` | 25k nodes | 600 | 47.2% | **−20 [−39, −1]** | no |
| `use_tt_eval` | 25k nodes | 1000 | 50.0% | −0 [−14, +14] | — |
| `use_tt_eval` | **3 s clock** | bench | — | **+4.6% nps**, identical nodes | **yes** |
| `use_capture_history` | 25k nodes | 2000 | 50.5% | +4 [−7, +14] | no |
| `use_cont_history` | 25k nodes | 2000 | 49.5% | −3 [−13, +7] | no |
| `use_capture_history` + `use_cont_history` | 25k nodes | 2000 | 50.0% | −0 [−10, +10] | no |
| *(replication)* new default vs `use_improving=false` | 25k nodes | 1200 | 52.6% | **+18 [+5, +31]** | — |
| *(A/A control)* default vs default | 25k nodes | 400 | 50.0% | −0 [−23, +23] | — |

### Evaluation

| Change | Budget | Games | Score | Elo [95%] | Shipped |
|---|---|---|---|---|---|
| `pv1=250` (Rat premium) | 25k nodes | 2000 | 48.5% | **−10 [−20, +0]** | no |
| `pv1=400` (Rat premium) | 25k nodes | 2000 | 49.7% | −2 [−13, +9] | no |
| `pv1=300,pv8=700` (Rat up, Elephant down) | 25k nodes | 2000 | 48.7% | **−9 [−20, +2]** | no |

Net: **two of seven ship.** Five are standard, well-proven techniques — and four
of those five are things the Python engine implemented, measured, and shipped —
that come out neutral or negative here.

### Why so little of the Python engine's later work transfers

Worth writing down, because the obvious next move is to keep porting v1.4–v1.6
features and the evidence says not to.

Those features were measured on a search running at 25–30k nodes per second and
reaching depth 8–10. This one runs at 2.4M and reaches 16–17. That is not the
same tree with more of it: at depth 16 the transposition table is dense, move
ordering is already good enough that most nodes fail high on the first move, and
the marginal ordering information a second or third history table can add has
largely been extracted already. Techniques that buy their keep by *improving
ordering* have much less left to buy at this speed.

The one that did transfer, `use_improving`, is not an ordering heuristic — it
changes *how much* gets pruned, and that scales with depth rather than being
consumed by it.

The implication for the remaining headroom: prefer changes that alter what the
search knows (evaluation) or how much of it runs (parallelism) over further
refinements to what it looks at first.

### `use_see_prune` — and why 600 games was not enough

The clearest lesson in the table. At 600 games it read +15 Elo and looked worth
shipping; at 2000 it read +6 [−4, +16], indistinguishable from zero. Nothing
changed but the sample.

The pair test settles it independently: `use_see_prune` *with* `use_improving`
scored +22, **less** than `use_improving` alone at +26. A component contributing
real Elo does not make the total go down. Left off.

Retry worth trying before writing the idea off: quiescence already declines
losing captures, so the main-search version only pays off where a losing capture
survives ordering into the shallow depths — which `see_prune_max_depth=3` may
simply not reach often enough to matter. A larger `see_prune_max_depth` is the
obvious variant.

### `use_tt_eval` — reuse the transposition table's stored static evaluation

Exactly neutral at fixed nodes, which is the expected and correct result: it
changes no decision, only the work done to reach it. A node-limited match cannot
see a speedup by construction.

Priced instead with `bench --params`, which is what that option exists for:

```
use_tt_eval=false   20,240,183 nodes in 7.14s = 2,835,863 nps
use_tt_eval=true    20,240,183 nodes in 6.82s = 2,966,410 nps
```

**+4.6%, and the node counts are byte-identical** — which is the whole argument.
Identical nodes at an identical time budget means the same tree was searched to
the same depth; only the work per node fell. There is no strength risk to weigh
against the gain, so this ships without a match.

Worth noting regardless of the outcome: the `eval` slot was being *written* and
never read, and it was being written as `0` at principal-variation nodes, which
never compute a static evaluation. Reading that back would have been
indistinguishable from a genuine dead-level position. The slot now stores
`EVAL_NONE` where no evaluation exists.

### `use_iir` — internal iterative reduction

Negative, and the interval excludes parity. This is a standard, well-proven
technique in chess engines, which is exactly why it is worth recording that it
does not work here as implemented: Jungle's branching factor is low and the
transposition table is large relative to the tree, so "no stored move" is weaker
evidence of an uninteresting node than it is in chess.

The current form reduces at every node type once `depth >= iir_min_depth`, PV
nodes included. Before concluding the idea is wrong rather than the tuning,
retry with a higher `iir_min_depth` and with PV nodes excluded.

### `use_lmr_log` — the log-based reduction matrix

Negative, and the interval excludes parity. The Python engine adopted this in
v1.5 and it is the usual shape in chess engines, so the interesting question is
why the integer-step `1 + depth/6 + idx/6` it replaces does better here.

Most likely because the log table reduces *harder* early: at depth 8, move 5 it
gives 2 plies where the step formula gives 1. With Jungle's low branching factor
the move list is short, so "late move" arrives sooner in absolute terms than the
table's shape assumes. A scaled variant (`ln(d)*ln(i)/2.5`, or a larger
`lmr_moves_before` alongside it) is the obvious retry.

### `use_improving` — static-evaluation trend against two plies ago

Implementing this surfaced a bug worth recording, because the first version of it
would have measured something meaningless. Only nodes that compute a static
evaluation wrote to the per-ply stack, so a principal-variation node left the
slot holding a value from an unrelated earlier line, and a node two plies below
compared itself against a different game. The stack is now cleared to `EVAL_NONE`
on entry at every node, and `improving` is false whenever the comparison point is
unknown.

### Piece values — the linear table survives its own disclaimer

`params.rs` says of `PIECE_VALUES = [0, 100, ..., 800]`:

> This misprices Jungle — rank order is not value order, since the Rat kills the
> Elephant, swims, and blocks leaps — but a Rat-premium alternative was tried and
> measured, inconclusively, worse.

That earlier attempt was inconclusive because it was run at forty games, where the
interval is about ±100 Elo. Re-run properly at 2000 games across three variants,
it is not inconclusive: **every Rat premium tested is neutral to negative.**

The likely reason is double-counting. What makes the Rat special is positional,
and the evaluation already pays for it where it actually applies:
`rat_adjacent_to_enemy_elephant` (+60) for the threat, and
`rat_in_water + rat_blocks_river` (+75) for swimming and blocking leaps. Raising
its *material* value adds that premium a second time, everywhere — including the
many positions where the Rat is doing none of those things — and it feeds static
exchange evaluation and delta pruning too, making the search reluctant to trade a
Rat it should happily trade.

The disclaimer in `params.rs` is still true as a statement about Jungle. It is not
a defect in this table, because the mispricing is already corrected elsewhere.

## Where the remaining headroom is not

Fifteen properly-powered matches now say the same thing: this engine's *parameters*
are at or near a local optimum, and single-flag or single-weight changes are not
finding anything. Seven search techniques, three material tables, and the two
strongest results in the whole table are `use_improving` (+26) and a 4.6% speedup.

That is a result, not a failure to find one. It means the remaining gains are
structural rather than parametric:

- **Parallelism.** Nothing here is threaded. Lazy SMP is worth roughly +60–100 Elo
  at four threads, and unlike everything in the table above it is not a guess —
  more search is more strength. It needs a lock-free transposition table and, for
  the browser, a nightly toolchain and cross-origin isolation.
- **A learned evaluation.** All sixteen pieces are unique, so an NNUE input of
  16 × 63 = 1008 features has a trivially incremental accumulator over
  make/unmake. This is the only change on the table that could plausibly beat
  everything else combined, and the only one that is a project rather than a
  patch.
- **An opening book.** Narrow — it only affects the first dozen plies — but the
  engine can now search a position for a minute where it gets 2.5 seconds in play.
