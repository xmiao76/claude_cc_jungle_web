"""Tests for ai/evaluator.py."""

from engine.board import Board
from engine.game_state import GameState
from engine.pieces import Animal, Color, make_piece_id
from ai.evaluator import evaluate, _INF
from config import (
    TRAPS_BLUE,
    PIECE_VALUES, EVAL_WEIGHTS,
)


def make_gs(*piece_specs) -> GameState:
    gs = GameState()
    gs.board = Board()
    for (c, r, color, animal) in piece_specs:
        pid = make_piece_id(color, animal)
        gs.board.place_piece(c, r, pid)
    return gs


def test_hanging_penalty_flags_undefended_attacked_piece():
    """An undefended piece attacked by an adjacent stronger enemy is
    penalized; an adjacent defender lifts the penalty entirely."""
    from dataclasses import replace
    from ai.search_config import strong_config

    gs = make_gs(
        (3, 4, Color.BLUE, Animal.CAT),       # attacked by the Lion below
        (3, 3, Color.BLACK, Animal.LION),
        (0, 8, Color.BLUE, Animal.ELEPHANT),  # far-away fillers
        (6, 0, Color.BLACK, Animal.RAT),
    )
    gs.turn = Color.BLUE
    on = strong_config()
    off = replace(strong_config(), use_hanging_penalty=False)
    assert evaluate(gs, Color.BLUE, on) < evaluate(gs, Color.BLUE, off)

    gs2 = make_gs(
        (3, 4, Color.BLUE, Animal.CAT),
        (3, 3, Color.BLACK, Animal.LION),
        (2, 4, Color.BLUE, Animal.DOG),       # defender next to the Cat
        (0, 8, Color.BLUE, Animal.ELEPHANT),
        (6, 0, Color.BLACK, Animal.RAT),
    )
    gs2.turn = Color.BLUE
    assert evaluate(gs2, Color.BLUE, on) == evaluate(gs2, Color.BLUE, off)


def test_feature_dot_product_matches_eval():
    """evaluate_features must satisfy the documented weight identity against
    the real evaluator, for tuned, frozen, and default configs."""
    import random as _random
    from ai.evaluator import evaluate_features, evaluate_nonterminal
    from ai.search_config import strong_config, v14_strong_config
    from config import EVAL_WEIGHTS, EVAL_WEIGHTS_TUNED

    rng = _random.Random(4242)
    gs = GameState()
    gs.new_game()
    for _ in range(60):
        if gs.is_terminal():
            break
        for cfg, w in ((strong_config(), EVAL_WEIGHTS_TUNED),
                       (v14_strong_config(), EVAL_WEIGHTS),
                       (None, EVAL_WEIGHTS)):
            for color in (Color.BLUE, Color.BLACK):
                material, counts = evaluate_features(gs, color, cfg)
                total = material
                total += counts["rat_in_water"] * (w["rat_in_water"]
                                                   + w["rat_blocks_river"])
                for k, v in counts.items():
                    if k != "rat_in_water":
                        total += v * w[k]
                assert total == evaluate_nonterminal(gs, color, cfg)
        gs.apply_move(rng.choice(gs.legal_moves()))


def test_evaluate_nonterminal_matches_evaluate_on_live_positions():
    """The hot-path eval (skips terminal detection) must agree with evaluate()
    on every non-terminal position, for both enhanced and baseline configs."""
    import random
    from ai.evaluator import evaluate_nonterminal
    from ai.search_config import baseline_config, strong_config

    rng = random.Random(99)
    configs = (strong_config(), baseline_config(), None)
    gs = GameState()
    gs.new_game()
    for _ in range(80):
        if gs.is_terminal():
            break
        for cfg in configs:
            for color in (Color.BLUE, Color.BLACK):
                assert evaluate_nonterminal(gs, color, cfg) \
                    == evaluate(gs, color, cfg)
        gs.apply_move(rng.choice(gs.legal_moves()))


def test_evaluate_starting_position_is_symmetric():
    """Initial position is mirror-symmetric in material; eval from either side
    should be near zero and exact negatives of each other."""
    gs = GameState()
    gs.new_game()
    blue = evaluate(gs, Color.BLUE)
    black = evaluate(gs, Color.BLACK)
    assert blue == -black


def test_evaluate_material_advantage():
    """An extra Elephant should swing the eval by at least its piece value."""
    # Both sides have a Lion; Blue also has an Elephant deep in its half.
    gs = make_gs(
        (0, 8, Color.BLUE, Animal.LION),
        (3, 5, Color.BLUE, Animal.ELEPHANT),
        (0, 0, Color.BLACK, Animal.LION),
    )
    score = evaluate(gs, Color.BLUE)
    assert score >= PIECE_VALUES[int(Animal.ELEPHANT)] - 200  # generous floor


def test_evaluate_trap_control_bonus():
    """Opposing piece in our trap is worth EVAL_WEIGHTS['trap_control'] extra.

    Evaluated with den-threat and PST disabled to isolate the trap-control term:
    every Blue trap is also a den-approach square, so the den-threat term would
    otherwise dominate this contrived position.
    """
    from dataclasses import replace
    from ai.search_config import strong_config
    cfg = replace(strong_config(), use_den_threat=False, use_pst=False)
    trap = next(iter(TRAPS_BLUE))
    base = make_gs(
        (0, 0, Color.BLUE, Animal.RAT),
        (6, 0, Color.BLACK, Animal.WOLF),
    )
    trapped = make_gs(
        (0, 0, Color.BLUE, Animal.RAT),
        (trap[0], trap[1], Color.BLACK, Animal.WOLF),
    )
    delta = evaluate(trapped, Color.BLUE, cfg) - evaluate(base, Color.BLUE, cfg)
    # delta includes trap bonus minus any positional swing for moving the wolf
    assert delta >= EVAL_WEIGHTS["trap_control"] - 200


def test_evaluate_den_proximity_gradient():
    """Closer to the enemy den should score higher for the side moving in."""
    near = make_gs((3, 1, Color.BLUE, Animal.WOLF))   # 1 step from black den (3,0)
    far = make_gs((3, 5, Color.BLUE, Animal.WOLF))    # 5 steps away
    assert evaluate(near, Color.BLUE) > evaluate(far, Color.BLUE)


def test_evaluate_rat_in_water_bonus():
    """Rat sitting in a river square earns the rat-in-water bonus."""
    in_water = make_gs((1, 3, Color.BLUE, Animal.RAT))   # (1,3) is river
    on_land = make_gs((0, 3, Color.BLUE, Animal.RAT))    # (0,3) is land
    assert evaluate(in_water, Color.BLUE) - evaluate(on_land, Color.BLUE) \
        >= EVAL_WEIGHTS["rat_in_water"] - 50


def test_evaluate_symmetry_random_midgame():
    """For any non-terminal position, eval(BLUE) must equal -eval(BLACK)."""
    gs = make_gs(
        (3, 4, Color.BLUE, Animal.TIGER),
        (3, 5, Color.BLACK, Animal.WOLF),
        (3, 6, Color.BLACK, Animal.LION),
        (0, 8, Color.BLUE, Animal.RAT),
        (6, 0, Color.BLACK, Animal.CAT),
        (1, 3, Color.BLUE, Animal.RAT),
    )
    gs.turn = Color.BLUE
    assert evaluate(gs, Color.BLUE) == -evaluate(gs, Color.BLACK)


def test_evaluate_terminal_returns_inf():
    """When the game is decided, eval returns +/- _INF."""
    gs = make_gs((3, 0, Color.BLUE, Animal.WOLF))   # Wolf already in black den
    # Force terminal via the result hook by simulating apply_move semantics:
    from engine.rules import WinResult
    gs.result = WinResult(Color.BLUE)
    assert evaluate(gs, Color.BLUE) == _INF
    assert evaluate(gs, Color.BLACK) == -_INF


# ---------------------------------------------------------------------------
# Piece-square tables (Task 5) — must preserve the symmetry invariant
# ---------------------------------------------------------------------------

def test_pst_table_is_column_symmetric():
    """No left/right bias: PST[adv][c] == PST[adv][COLS-1-c]."""
    from config import PST_TABLE, COLS, ROWS
    for adv in range(ROWS):
        for c in range(COLS):
            assert PST_TABLE[adv][c] == PST_TABLE[adv][COLS - 1 - c]


def test_pst_keeps_start_eval_balanced():
    """Column-symmetric PST contributes exactly 0 at the symmetric start."""
    from dataclasses import replace
    from ai.search_config import strong_config
    gs = GameState()
    gs.new_game()
    cfg_on = strong_config()
    cfg_off = replace(cfg_on, use_pst=False)
    assert evaluate(gs, Color.BLUE, cfg_on) == evaluate(gs, Color.BLUE, cfg_off)


def test_eval_symmetry_holds_under_all_configs():
    """eval(BLUE) == -eval(BLACK) must hold with PST on, off, and default."""
    from ai.search_config import strong_config, baseline_config
    gs = make_gs(
        (3, 4, Color.BLUE, Animal.TIGER),
        (1, 5, Color.BLACK, Animal.WOLF),
        (3, 6, Color.BLACK, Animal.LION),
        (0, 8, Color.BLUE, Animal.RAT),
        (6, 0, Color.BLACK, Animal.CAT),
        (5, 2, Color.BLUE, Animal.LEOPARD),
    )
    gs.turn = Color.BLUE
    for cfg in (strong_config(), baseline_config(), None):
        assert evaluate(gs, Color.BLUE, cfg) == -evaluate(gs, Color.BLACK, cfg)


def test_pst_changes_eval():
    """Enabling the PST changes the score of an off-center vs central piece."""
    from dataclasses import replace
    from ai.search_config import strong_config
    cfg_on = strong_config()
    cfg_off = replace(cfg_on, use_pst=False)
    gs = make_gs((3, 6, Color.BLUE, Animal.WOLF))   # central file
    assert evaluate(gs, Color.BLUE, cfg_on) > evaluate(gs, Color.BLUE, cfg_off)


# ---------------------------------------------------------------------------
# Den threat / safety (Task 6) — must preserve the symmetry invariant
# ---------------------------------------------------------------------------

def test_den_threat_symmetry():
    """eval(BLUE) == -eval(BLACK) with the den-threat term active."""
    from ai.search_config import strong_config
    gs = make_gs(
        (3, 7, Color.BLACK, Animal.WOLF),   # on a blue den-approach trap
        (0, 0, Color.BLUE, Animal.RAT),
    )
    gs.turn = Color.BLUE
    cfg = strong_config()
    assert evaluate(gs, Color.BLUE, cfg) == -evaluate(gs, Color.BLACK, cfg)


def test_den_threat_penalizes_undefended_approach():
    """An undefended enemy on our den approach lowers our eval (vs term off)."""
    from dataclasses import replace
    from ai.search_config import strong_config
    gs = make_gs(
        (3, 7, Color.BLACK, Animal.WOLF),   # blue den-approach (3,8) neighbor, undefended
        (0, 0, Color.BLUE, Animal.RAT),
    )
    gs.turn = Color.BLUE
    cfg_on = strong_config()
    cfg_off = replace(cfg_on, use_den_threat=False)
    assert evaluate(gs, Color.BLUE, cfg_on) < evaluate(gs, Color.BLUE, cfg_off)
