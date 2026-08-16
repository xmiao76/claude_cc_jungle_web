"""The Python side of the differential rules oracle.

This repository now contains two complete implementations of the Jungle rules:
the Python engine in ``engine-python/engine/`` and the Rust engine in ``rust/``. Only
the Rust one plays; the Python one is kept as the oracle that proves it right.

Two rule sets normally drift apart, and the drift is silent. The arrangement only
holds because the agreement is *continuously measured*, by instruments that must
all stay green:

===========================  =====================================  ==================================
Instrument                   What it pins                           Where
===========================  =====================================  ==================================
Frozen perft counts          Move generation, exhaustively          ``test_perft.py`` / ``perft.rs``
Golden position corpus       Legal moves, terminal, winner on 10k    this file / ``golden.rs``
Golden evaluation corpus     Static evaluation, score for score     ``golden_evals.rs`` (Rust only)
===========================  =====================================  ==================================

The evaluation corpus is deliberately Rust-only. ``jungle-eval`` is a verbatim
port of the desktop repository's v1.3-era evaluator, while this repository's
Python evaluator is the v1.6 lineage and has terms the port was never meant to
have (``use_hanging_penalty`` most visibly). The two are not supposed to agree,
so scoring the corpus here would assert a falsehood. ``golden_evals.rs`` still
pins ``jungle-eval`` against accidental drift, which is that corpus's real job.

If a rule changes, both engines change and the corpora are regenerated together
with ``python -m tools.golden``.
"""

from __future__ import annotations

import pytest

from engine.move_generator import generate_legal_moves
from engine.pieces import Color
from tools import golden


@pytest.fixture(scope="module")
def corpus() -> list[str]:
    lines = golden.read()
    assert len(lines) == 10_000, "the corpus is a fixed 10,000-position contract"
    return lines


def _winner_char(gs) -> str:
    winner = gs.get_winner()
    if winner is None:
        return "-"
    return "B" if winner == Color.BLUE else "K"


def test_move_generation_matches_the_corpus(corpus):
    """Every position's legal-move list, move for move.

    A mismatch names the position and the specific move added or missed, which
    is what makes this more useful than perft: perft says the rules diverged,
    this says where.
    """
    bad: list[str] = []
    for line in corpus:
        gs, _, _, expected = golden.decode_position(line)
        got = sorted(
            (m.fc, m.fr, m.tc, m.tr, m.captured)
            for m in generate_legal_moves(gs.board, gs.turn)
        )
        expected = [tuple(m) for m in expected]
        if got != expected:
            board = line.split(" ", 1)[0]
            extra = [m for m in got if m not in expected]
            missing = [m for m in expected if m not in got]
            bad.append(f"{board}: extra={extra} missing={missing}")

    assert not bad, f"{len(bad)} of {len(corpus)} positions diverge:\n" + "\n".join(bad[:5])


def test_terminal_status_and_winner_match_the_corpus(corpus):
    """Terminality is recorded independently of the move list, so check it so."""
    bad_terminal: list[str] = []
    bad_winner: list[str] = []
    for line in corpus:
        gs, terminal, winner, _ = golden.decode_position(line)
        board = line.split(" ", 1)[0]
        if gs.is_terminal() != terminal:
            bad_terminal.append(board)
        if _winner_char(gs) != winner:
            bad_winner.append(board)

    assert not bad_terminal, f"{len(bad_terminal)} terminal-status mismatches: {bad_terminal[:5]}"
    assert not bad_winner, f"{len(bad_winner)} winner mismatches: {bad_winner[:5]}"


def test_board_strings_round_trip(corpus):
    """Encoding is the wire format the Rust side reads; it has to be exact."""
    for line in corpus:
        board = line.split(" ", 1)[0]
        assert golden.encode_board(golden.decode_board(board)) == board


@pytest.mark.slow
def test_corpus_regenerates_byte_for_byte(corpus, tmp_path):
    """`python -m tools.golden` must reproduce the committed corpus exactly.

    Stronger than the checks above, and worth its runtime: the sampler drives
    itself with ``rng.choice(gs.legal_moves())``, so reproducing the file proves
    the two engines agree on move *order* as well as move *content* — and on
    every position the walk visits, not only the 10,000 it keeps.
    """
    regenerated = golden.collect()
    out = tmp_path / "positions.txt.gz"
    golden.write(regenerated, out)
    assert golden.read(out) == corpus
