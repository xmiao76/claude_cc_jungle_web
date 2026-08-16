"""Frozen perft counts: the contract for move generation and the rules.

These numbers fingerprint every legality decision the engine makes. They are the
gate for optimising move generation, `Board.make_move`/`unmake_move` and the
rules: those changes must be behaviour-preserving, and a perft mismatch is the
only cheap way to prove it.

The counts were recorded *after* the trap-capture fix (an enemy piece in your
trap has rank 0 and may be taken by anything, Elephant included), so
`trap_standoff` bakes that rule in. If a future change to `rules.can_capture`
alters these totals, that is a rules change and the intent must be explicit.

Use `tools.perft.perft_divide` to localise a mismatch:

    python -c "from tests.python.test_perft import POSITIONS; from tools.perft import perft_divide; \
               print(perft_divide(POSITIONS['start'](), 3))"
"""

from __future__ import annotations

from collections.abc import Callable

from engine.game_state import GameState
from engine.pieces import Animal, Color
from tools.positions import assert_board_consistent, make_gs
from tools.perft import perft, perft_divide

B, K = Color.BLUE, Color.BLACK


def _start() -> GameState:
    gs = GameState()
    gs.new_game()
    return gs


def _spec(*specs, turn: Color = Color.BLUE) -> Callable[[], GameState]:
    return lambda: make_gs(*specs, turn=turn)


POSITIONS: dict[str, Callable[[], GameState]] = {
    "start": _start,
    # The reported defect: a Black Rat inside Blue's trap at (3,7), with a Blue
    # Elephant and Cat adjacent. Both may take it — that is the fix.
    "trap_standoff": _spec(
        (3, 6, B, Animal.ELEPHANT), (3, 7, K, Animal.RAT),
        (2, 7, B, Animal.CAT), (4, 7, K, Animal.DOG),
    ),
    # Rats inside the river, land pieces on the banks: exercises the
    # water/land capture boundary in both directions.
    "river_rats": _spec(
        (1, 4, B, Animal.RAT), (2, 4, K, Animal.RAT),
        (0, 4, B, Animal.ELEPHANT), (3, 4, K, Animal.LION),
    ),
    # Lion and Tiger positioned to leap: Lion both axes, Tiger horizontal only.
    "jumpers": _spec(
        (0, 4, B, Animal.LION), (3, 4, B, Animal.TIGER),
        (6, 4, K, Animal.LION), (3, 2, K, Animal.TIGER),
    ),
    # A Rat sitting in the river blocks jumps across it.
    "jump_blocked": _spec(
        (0, 4, B, Animal.LION), (1, 4, K, Animal.RAT),
        (3, 3, B, Animal.TIGER), (6, 0, K, Animal.ELEPHANT),
    ),
    # Both sides racing for a den: exercises den entry and the own-den ban.
    "den_race": _spec(
        (3, 2, B, Animal.WOLF), (2, 1, K, Animal.CAT),
        (3, 6, K, Animal.LEOPARD), (4, 7, B, Animal.DOG),
    ),
    # The classic endgame. The Elephant cannot take the Rat and cannot swim,
    # so it has exactly one move.
    "ele_vs_rat": _spec(
        (3, 4, B, Animal.ELEPHANT), (3, 5, K, Animal.RAT),
    ),
}

# perft at depths 1, 2, 3, 4.
EXPECTED: dict[str, tuple[int, int, int, int]] = {
    "start": (24, 576, 12240, 260099),
    "trap_standoff": (8, 39, 187, 1057),
    "river_rats": (5, 27, 154, 1016),
    "jumpers": (4, 28, 172, 1103),
    "jump_blocked": (6, 32, 170, 1008),
    "den_race": (8, 63, 419, 2751),
    "ele_vs_rat": (1, 4, 7, 28),
}


def test_every_position_has_expected_counts():
    """Every named position is pinned at all four depths."""
    assert set(POSITIONS) == set(EXPECTED), "POSITIONS and EXPECTED must stay in step"


def test_perft_counts_are_unchanged():
    for name, factory in POSITIONS.items():
        for depth, expected in enumerate(EXPECTED[name], start=1):
            gs = factory()
            actual = perft(gs, depth)
            assert actual == expected, (
                f"perft({depth}) for {name!r}: expected {expected}, got {actual}. "
                f"Move generation or the rules changed — use perft_divide to localise."
            )


def test_perft_restores_the_position():
    """make/unmake must round-trip exactly across a whole perft walk."""
    for name, factory in POSITIONS.items():
        gs = factory()
        before_hash = gs.board.hash
        before_turn = gs.turn
        perft(gs, 3)
        assert gs.board.hash == before_hash, f"{name}: hash not restored after perft"
        assert gs.turn == before_turn, f"{name}: turn not restored after perft"
        assert gs.history == [], f"{name}: history not unwound after perft"
        assert_board_consistent(gs.board)


def test_divide_sums_to_total():
    """perft_divide is a decomposition of perft, so the parts must sum."""
    for name in ("start", "trap_standoff", "den_race"):
        gs = POSITIONS[name]()
        total = perft(POSITIONS[name](), 3)
        assert sum(n for _, n in perft_divide(gs, 3)) == total, name


def test_trapped_rat_capture_is_counted():
    """Guard the fix at the perft level, not just via can_capture.

    In `trap_standoff` the Blue Elephant at (3,6) and the Blue Cat at (2,7) are
    both adjacent to the Black Rat trapped at (3,7). Both captures must appear.
    """
    gs = POSITIONS["trap_standoff"]()
    captures = [m for m in gs.legal_moves() if (m.tc, m.tr) == (3, 7)]
    sources = sorted((m.fc, m.fr) for m in captures)
    assert sources == [(2, 7), (3, 6)], (
        f"both the Cat and the Elephant should be able to take the trapped Rat, got {sources}"
    )
