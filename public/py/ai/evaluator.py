"""Board evaluation function for Jungle AI."""

from __future__ import annotations

from config import (
    DEN_BLACK, DEN_BLUE, TRAPS_BLACK, TRAPS_BLUE,
    ROWS,
    PIECE_VALUES, EVAL_WEIGHTS, EVAL_WEIGHTS_TUNED,
    NEIGHBORS, IS_RIVER, TRAP_ZEROES,
    ADV_BLUE, ADV_BLACK, PST_BLUE, PST_BLACK,
    DIST_TO_BLACK_DEN, DIST_TO_BLUE_DEN,
)
from engine.pieces import Color
from engine.move_generator import HAS_JUMP
from engine.rules import can_capture_sq

_INF = 10_000_000

_MIDLINE = ROWS // 2  # row 4

# Den-approach squares per color: the traps orthogonally adjacent to that
# color's den (all three own traps qualify on the standard board), as flat
# square indices. An enemy piece standing there is one step from entering.
def _den_approach_squares(den: tuple[int, int],
                          traps: set[tuple[int, int]]) -> tuple[int, ...]:
    den_c, den_r = den
    return tuple(c * ROWS + r for (c, r) in traps
                 if abs(c - den_c) + abs(r - den_r) == 1)


_DEN_APPROACH_BLUE = _den_approach_squares(DEN_BLUE, TRAPS_BLUE)
_DEN_APPROACH_BLACK = _den_approach_squares(DEN_BLACK, TRAPS_BLACK)


def _den_threat_level(board, color: Color) -> int:
    """Danger to *color*'s den from the opponent.

    Counts enemy pieces sitting on a den-approach square (a trap orthogonally
    adjacent to the den — one step from entering). The weight is higher when no
    friendly piece is adjacent to recapture (an enemy on our trap has rank 0, so
    any adjacent friendly piece can take it). This is additive to the pure
    den-proximity gradient: it expresses whether the approach is *defended*.
    """
    sqs = board._sq
    my_is_blue = color == Color.BLUE
    approach = _DEN_APPROACH_BLUE if my_is_blue else _DEN_APPROACH_BLACK
    danger = 0
    for tsq in approach:
        occ = sqs[tsq]
        if occ != 0 and (occ > 0) != my_is_blue:
            defended = False
            for nsq in NEIGHBORS[tsq]:
                p = sqs[nsq]
                if p != 0 and (p > 0) == my_is_blue:
                    defended = True
                    break
            danger += 1 if defended else 3
    return danger


def evaluate(state, color: Color, cfg=None) -> int:
    """Evaluate board from *color*'s perspective. Higher = better for color.

    *cfg* is an optional :class:`ai.search_config.SearchConfig` gating the
    enhancement terms (PST, den-threat). When ``None`` all terms are enabled.
    """
    winner = state.get_winner()
    if winner is not None:
        return _INF if winner == color else -_INF

    # Drawn position: explicit zero.
    if state.is_50_move_draw():
        return 0

    return _evaluate_core(state, color, cfg)


def evaluate_nonterminal(state, color: Color, cfg=None) -> int:
    """Static evaluation without the terminal-position detection.

    ``evaluate`` calls ``state.get_winner()``, which runs a full legal-move
    generation just to detect the (rare) no-moves stalemate — doubling the
    cost of every quiescence leaf. Hot search paths that have already ruled
    out an explicit result (``state.result is None``) use this variant
    instead. A true stalemate leaf then evaluates by material rather than as
    a loss; that inaccuracy is confined to the ``use_fast_movegen`` paths.
    """
    if state.is_50_move_draw():
        return 0
    return _evaluate_core(state, color, cfg)


def _evaluate_core(state, color: Color, cfg) -> int:
    """The static evaluation terms (no terminal checks).

    ANTISYMMETRY INVARIANT: every per-piece term is computed from the piece's
    OWN color (added for own pieces, subtracted for opponent pieces), never
    from the evaluating side's perspective, so that
    ``evaluate(BLUE) == -evaluate(BLACK)`` for any position.
    """
    board = state.board
    my_is_blue = color == Color.BLUE
    opponent = Color.BLACK if my_is_blue else Color.BLUE

    # Local bindings for the hot per-piece loops.
    sqs = board._sq
    neighbors = NEIGHBORS
    is_river = IS_RIVER
    trap_zeroes = TRAP_ZEROES
    has_jump = HAS_JUMP
    piece_values = PIECE_VALUES

    # Per-color geometry tables (own-color advancement keeps antisymmetry).
    adv_my = ADV_BLUE if my_is_blue else ADV_BLACK
    adv_opp = ADV_BLACK if my_is_blue else ADV_BLUE
    pst_my = PST_BLUE if my_is_blue else PST_BLACK
    pst_opp = PST_BLACK if my_is_blue else PST_BLUE
    dist_their_den = DIST_TO_BLACK_DEN if my_is_blue else DIST_TO_BLUE_DEN
    dist_own_den = DIST_TO_BLUE_DEN if my_is_blue else DIST_TO_BLACK_DEN
    # TRAP_ZEROES value that zeroes MY pieces (= the opponent's traps), and
    # the value that zeroes THEIR pieces (= our traps).
    zeroes_mine = 1 if my_is_blue else 2
    zeroes_theirs = 2 if my_is_blue else 1

    # Weight table: the tuned set (Texel fit) when the config asks for it,
    # else the hand-tuned legacy weights (v13/v14/baseline and cfg=None).
    w = (EVAL_WEIGHTS_TUNED if cfg is not None and cfg.use_tuned_weights
         else EVAL_WEIGHTS)
    adv_w = w["advancement_per_row"]
    den_max = w["den_proximity_max_dist"]
    den_step = w["den_proximity_per_step"]
    rat_water = w["rat_in_water"]
    rat_near_ele = w["rat_adjacent_to_enemy_elephant"]
    trap_bonus = w["trap_control"]
    den_def = w["den_defender"]
    jump_ready = w["jump_ready"]
    rat_blocks = w["rat_blocks_river"]
    adv_accel = w["advancement_acceleration"]
    tempo = w["tempo"]
    mob_w = w["mobility"]
    pst_w = w["pst"]
    hanging_w = w["hanging"]

    use_pst = True if cfg is None else cfg.use_pst
    use_den_threat = True if cfg is None else cfg.use_den_threat
    use_hanging = True if cfg is None else cfg.use_hanging_penalty

    enemy_elephant_pid = -8 if my_is_blue else 8
    own_elephant_pid = 8 if my_is_blue else -8

    my_pieces = board.pieces_of(color)
    opp_pieces = board.pieces_of(opponent)

    score = 0
    my_mobility = 0
    opp_mobility = 0

    # 1. Material + positional + mobility + trap control — own pieces
    for pid, sq in my_pieces.items():
        rank = pid if pid > 0 else -pid
        score += piece_values[rank]
        adv = adv_my[sq]
        score += adv * adv_w
        if adv > _MIDLINE:
            score += (adv - _MIDLINE) * adv_accel
        if use_pst:
            score += pst_my[sq] * pst_w

        dist = dist_their_den[sq]
        if dist <= den_max:
            score += (den_max + 1 - dist) * den_step

        # Den defender
        if dist_own_den[sq] <= 2:
            score += den_def

        is_rat = rank == 1
        if is_rat and is_river[sq]:
            score += rat_water + rat_blocks

        # One neighbor scan: mobility (adjacent empty/enemy squares), the
        # rat-hunts-elephant adjacency, and hanging-piece detection.
        threatened = False
        has_friend = False
        for nsq in neighbors[sq]:
            t = sqs[nsq]
            if t == 0:
                my_mobility += 1
            elif (t > 0) != my_is_blue:
                my_mobility += 1
                if (use_hanging and not threatened
                        and can_capture_sq(t, pid, nsq, sq, board)):
                    threatened = True
            else:
                has_friend = True
            if is_rat and t == enemy_elephant_pid:
                score += rat_near_ele
        # Undefended piece attacked by an adjacent enemy: rank-scaled penalty.
        if use_hanging and threatened and not has_friend:
            score -= hanging_w * rank

        if (rank == 7 or rank == 6) and has_jump[sq]:
            score += jump_ready

        if trap_zeroes[sq] == zeroes_mine:
            score -= trap_bonus

    # 2. Mirror — opponent pieces
    for pid, sq in opp_pieces.items():
        rank = pid if pid > 0 else -pid
        score -= piece_values[rank]
        adv = adv_opp[sq]
        score -= adv * adv_w
        if adv > _MIDLINE:
            score -= (adv - _MIDLINE) * adv_accel
        if use_pst:
            score -= pst_opp[sq] * pst_w

        dist = dist_own_den[sq]
        if dist <= den_max:
            score -= (den_max + 1 - dist) * den_step

        if dist_their_den[sq] <= 2:
            score -= den_def

        is_rat = rank == 1
        if is_rat and is_river[sq]:
            score -= rat_water + rat_blocks

        threatened = False
        has_friend = False
        for nsq in neighbors[sq]:
            t = sqs[nsq]
            if t == 0:
                opp_mobility += 1
            elif (t > 0) == my_is_blue:
                opp_mobility += 1
                if (use_hanging and not threatened
                        and can_capture_sq(t, pid, nsq, sq, board)):
                    threatened = True
            else:
                has_friend = True
            if is_rat and t == own_elephant_pid:
                score -= rat_near_ele
        if use_hanging and threatened and not has_friend:
            score += hanging_w * rank

        if (rank == 7 or rank == 6) and has_jump[sq]:
            score -= jump_ready

        if trap_zeroes[sq] == zeroes_theirs:
            score += trap_bonus

    # 3. Mobility difference
    score += (my_mobility - opp_mobility) * mob_w

    # 4. Tempo (small bonus for side to move)
    if state.turn == color:
        score += tempo
    else:
        score -= tempo

    # 5. Den threat / safety: penalize undefended enemy pieces on our den
    #    approaches, reward the mirror. Additive to den-proximity (adds defense
    #    awareness). Computed per-own-color so it stays antisymmetric.
    if use_den_threat:
        dt_w = w["den_threat"]
        score -= dt_w * _den_threat_level(board, color)
        score += dt_w * _den_threat_level(board, opponent)

    return score


def evaluate_features(state, color: Color, cfg=None) -> tuple[int, dict[str, int]]:
    """Raw feature counts for the Texel tuner (NOT a hot path).

    Returns ``(material, counts)`` such that, with the weight table ``w``
    selected by *cfg*::

        _evaluate_core(state, color, cfg) ==
            material
            + counts["rat_in_water"] * (w["rat_in_water"] + w["rat_blocks_river"])
            + sum(counts[k] * w[k] for every other k in counts)

    (The rat-in-water and rat-blocks-river weights always co-fire on the same
    indicator, so they are one feature with a combined coefficient; material
    is frozen and enters the fit as a fixed offset.) The consistency of this
    identity is pinned by a property test.
    """
    board = state.board
    my_is_blue = color == Color.BLUE
    opponent = Color.BLACK if my_is_blue else Color.BLUE

    sqs = board._sq
    neighbors = NEIGHBORS
    den_max = EVAL_WEIGHTS["den_proximity_max_dist"]   # structural, not fitted

    use_pst = True if cfg is None else cfg.use_pst
    use_den_threat = True if cfg is None else cfg.use_den_threat
    use_hanging = True if cfg is None else cfg.use_hanging_penalty

    counts = {
        "advancement_per_row": 0,
        "den_proximity_per_step": 0,
        "den_defender": 0,
        "rat_in_water": 0,
        "rat_adjacent_to_enemy_elephant": 0,
        "trap_control": 0,
        "jump_ready": 0,
        "advancement_acceleration": 0,
        "tempo": 1 if state.turn == color else -1,
        "mobility": 0,
        "pst": 0,
        "den_threat": 0,
        "hanging": 0,
    }
    material = 0

    for sign, side, side_is_blue in ((1, color, my_is_blue),
                                     (-1, opponent, not my_is_blue)):
        adv_t = ADV_BLUE if side_is_blue else ADV_BLACK
        pst_t = PST_BLUE if side_is_blue else PST_BLACK
        dist_enemy_den = DIST_TO_BLACK_DEN if side_is_blue else DIST_TO_BLUE_DEN
        dist_own_den = DIST_TO_BLUE_DEN if side_is_blue else DIST_TO_BLACK_DEN
        zeroed_here = 1 if side_is_blue else 2
        enemy_ele = -8 if side_is_blue else 8

        for pid, sq in board.pieces_of(side).items():
            rank = pid if pid > 0 else -pid
            material += sign * PIECE_VALUES[rank]
            adv = adv_t[sq]
            counts["advancement_per_row"] += sign * adv
            if adv > _MIDLINE:
                counts["advancement_acceleration"] += sign * (adv - _MIDLINE)
            if use_pst:
                counts["pst"] += sign * pst_t[sq]
            dist = dist_enemy_den[sq]
            if dist <= den_max:
                counts["den_proximity_per_step"] += sign * (den_max + 1 - dist)
            if dist_own_den[sq] <= 2:
                counts["den_defender"] += sign
            is_rat = rank == 1
            if is_rat and IS_RIVER[sq]:
                counts["rat_in_water"] += sign
            threatened = False
            has_friend = False
            for nsq in neighbors[sq]:
                t = sqs[nsq]
                if t == 0:
                    counts["mobility"] += sign
                elif (t > 0) != side_is_blue:
                    counts["mobility"] += sign
                    if (use_hanging and not threatened
                            and can_capture_sq(t, pid, nsq, sq, board)):
                        threatened = True
                else:
                    has_friend = True
                if is_rat and t == enemy_ele:
                    counts["rat_adjacent_to_enemy_elephant"] += sign
            if use_hanging and threatened and not has_friend:
                counts["hanging"] -= sign * rank
            if (rank == 7 or rank == 6) and HAS_JUMP[sq]:
                counts["jump_ready"] += sign
            if TRAP_ZEROES[sq] == zeroed_here:
                counts["trap_control"] -= sign

    if use_den_threat:
        counts["den_threat"] = (_den_threat_level(board, opponent)
                                - _den_threat_level(board, color))

    return material, counts
