"""Piece definitions for Jungle (Dou Shou Qi)."""

from __future__ import annotations

from enum import IntEnum


class Animal(IntEnum):
    RAT = 1
    CAT = 2
    DOG = 3
    WOLF = 4
    LEOPARD = 5
    TIGER = 6
    LION = 7
    ELEPHANT = 8


class Color(IntEnum):
    BLUE = 0    # south / bottom player, moves first
    BLACK = 1   # north / top player


# Piece ID encoding: positive = BLUE, negative = BLACK
# piece_id = color_sign * animal_rank
# 0 = empty square

def make_piece_id(color: Color, animal: Animal) -> int:
    return int(animal) if color == Color.BLUE else -int(animal)


def piece_id_color(pid: int) -> Color:
    return Color.BLUE if pid > 0 else Color.BLACK


def piece_id_animal(pid: int) -> Animal:
    return Animal(abs(pid))


def piece_id_rank(pid: int) -> int:
    return abs(pid)


# Starting positions: the back-row corners (Lion / Tiger) stay in their fixed
# board positions, while rows 1-2 and rows 6-7 are top-to-bottom mirrors so the
# Elephant sits on each player's own left side (col 6 for Black, col 0 for Blue).
STARTING_POSITIONS: list[tuple[int, int, Color, Animal]] = [
    # Black (top, row 0 = top)
    (0, 0, Color.BLACK, Animal.LION),
    (6, 0, Color.BLACK, Animal.TIGER),
    (1, 1, Color.BLACK, Animal.DOG),
    (5, 1, Color.BLACK, Animal.CAT),
    (0, 2, Color.BLACK, Animal.RAT),
    (2, 2, Color.BLACK, Animal.LEOPARD),
    (4, 2, Color.BLACK, Animal.WOLF),
    (6, 2, Color.BLACK, Animal.ELEPHANT),
    # Blue (bottom, row 8 = bottom) — top-to-bottom mirror of Black on rows 6-7
    (0, 6, Color.BLUE, Animal.ELEPHANT),
    (2, 6, Color.BLUE, Animal.WOLF),
    (4, 6, Color.BLUE, Animal.LEOPARD),
    (6, 6, Color.BLUE, Animal.RAT),
    (1, 7, Color.BLUE, Animal.CAT),
    (5, 7, Color.BLUE, Animal.DOG),
    (0, 8, Color.BLUE, Animal.TIGER),
    (6, 8, Color.BLUE, Animal.LION),
]

# Human-readable animal names
ANIMAL_NAMES: dict[Animal, str] = {
    Animal.RAT: "Rat",
    Animal.CAT: "Cat",
    Animal.DOG: "Dog",
    Animal.WOLF: "Wolf",
    Animal.LEOPARD: "Leopard",
    Animal.TIGER: "Tiger",
    Animal.LION: "Lion",
    Animal.ELEPHANT: "Elephant",
}
