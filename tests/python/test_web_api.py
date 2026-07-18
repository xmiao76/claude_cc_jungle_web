"""Tests for the web_api JSON bridge used by the Pyodide worker."""

import json

import pytest

import web_api
from engine.game_state import GameState
from engine.pieces import Animal, Color, make_piece_id


def _call(fn, *args):
    """Invoke a bridge function and parse its JSON envelope."""
    return json.loads(fn(*args))


@pytest.fixture(autouse=True)
def fresh_session():
    """Each test starts from a clean Easy game."""
    web_api.new_game(0)
    yield


def _custom_state(pieces):
    """Install a hand-built position: pieces = [(col, row, color, animal)]."""
    gs = GameState()
    for col, row, color, animal in pieces:
        gs.board.place_piece(col, row, make_piece_id(color, animal))
    web_api._session.gs = gs
    return gs


# ---------------------------------------------------------------------------
# new_game / get_state
# ---------------------------------------------------------------------------

def test_new_game_returns_starting_state():
    res = _call(web_api.new_game, 1)
    assert res["ok"] is True
    state = res["data"]["state"]
    assert len(state["board"]) == 9
    assert all(len(row) == 7 for row in state["board"])
    piece_count = sum(1 for row in state["board"] for pid in row if pid != 0)
    assert piece_count == 16
    assert state["turn"] == int(Color.BLUE)
    assert state["terminal"] is False
    assert state["winner"] is None
    assert state["plyCount"] == 0
    assert len(state["legalMoves"]) > 0
    assert state["captured"] == {"blue": [], "black": []}


def test_new_game_rejects_bad_difficulty():
    res = _call(web_api.new_game, 5)
    assert res["ok"] is False
    assert "difficulty" in res["error"]


def test_get_state_matches_new_game():
    first = _call(web_api.new_game, 0)["data"]["state"]
    again = _call(web_api.get_state)["data"]["state"]
    assert first == again


def test_engine_info_reports_version():
    res = _call(web_api.engine_info)
    assert res["ok"] is True
    assert res["data"]["engineVersion"]
    assert res["data"]["python"]


# ---------------------------------------------------------------------------
# apply_move
# ---------------------------------------------------------------------------

def test_apply_legal_move_advances_turn():
    # Blue rat at (6,6) stepping north to (6,5) is legal from the start.
    res = _call(web_api.apply_move, 6, 6, 6, 5)
    assert res["ok"] is True
    state = res["data"]["state"]
    assert state["turn"] == int(Color.BLACK)
    assert state["plyCount"] == 1
    assert state["lastMove"] == {"fc": 6, "fr": 6, "tc": 6, "tr": 5,
                                 "captured": 0}
    assert state["board"][5][6] != 0
    assert state["board"][6][6] == 0
    assert len(state["history"]) == 1


def test_apply_illegal_move_is_rejected_and_state_unchanged():
    before = _call(web_api.get_state)["data"]["state"]
    res = _call(web_api.apply_move, 0, 8, 3, 3)
    assert res["ok"] is False
    assert "illegal" in res["error"]
    after = _call(web_api.get_state)["data"]["state"]
    assert before == after


# ---------------------------------------------------------------------------
# ai_move
# ---------------------------------------------------------------------------

def test_ai_move_plays_a_legal_move_and_flips_turn():
    res = _call(web_api.ai_move)
    assert res["ok"] is True
    assert res["data"]["move"] is not None
    state = res["data"]["state"]
    assert state["turn"] == int(Color.BLACK)
    assert state["plyCount"] == 1


def test_ai_move_respects_explicit_budget():
    res = _call(web_api.ai_move, 200)
    assert res["ok"] is True


# ---------------------------------------------------------------------------
# undo
# ---------------------------------------------------------------------------

def test_undo_pops_human_and_ai_plies():
    _call(web_api.apply_move, 6, 6, 6, 5)     # human (Blue)
    _call(web_api.ai_move)                    # AI (Black)
    res = _call(web_api.undo_for_human, int(Color.BLUE))
    assert res["ok"] is True
    state = res["data"]["state"]
    assert state["plyCount"] == 0
    assert state["turn"] == int(Color.BLUE)


def test_undo_with_no_history_errors():
    res = _call(web_api.undo_for_human, int(Color.BLUE))
    assert res["ok"] is False


# ---------------------------------------------------------------------------
# terminal positions
# ---------------------------------------------------------------------------

def test_den_entry_wins_the_game():
    _custom_state([
        (3, 1, Color.BLUE, Animal.LION),
        (0, 5, Color.BLACK, Animal.RAT),
        (6, 5, Color.BLACK, Animal.CAT),
    ])
    res = _call(web_api.apply_move, 3, 1, 3, 0)   # into Black's den
    assert res["ok"] is True
    state = res["data"]["state"]
    assert state["terminal"] is True
    assert state["winner"] == {"color": int(Color.BLUE), "reason": "den"}
    assert state["legalMoves"] == []


def test_capturing_last_piece_wins_by_elimination():
    _custom_state([
        (2, 2, Color.BLUE, Animal.LION),
        (2, 1, Color.BLACK, Animal.CAT),
    ])
    res = _call(web_api.apply_move, 2, 2, 2, 1)
    assert res["ok"] is True
    state = res["data"]["state"]
    assert state["winner"] == {"color": int(Color.BLUE),
                               "reason": "elimination"}
    assert state["captured"]["black"] == ["Cat"]


def test_moves_rejected_after_game_over():
    _custom_state([
        (3, 1, Color.BLUE, Animal.LION),
        (0, 5, Color.BLACK, Animal.RAT),
    ])
    _call(web_api.apply_move, 3, 1, 3, 0)
    res = _call(web_api.apply_move, 0, 5, 0, 4)
    assert res["ok"] is False
    assert "over" in res["error"]


# ---------------------------------------------------------------------------
# replay (E2E support)
# ---------------------------------------------------------------------------

def test_replay_applies_move_sequence():
    moves = json.dumps([[6, 6, 6, 5], [0, 2, 0, 3]])
    res = _call(web_api.replay_moves, moves)
    assert res["ok"] is True
    assert res["data"]["applied"] == 2
    assert res["data"]["state"]["plyCount"] == 2


def test_replay_rejects_illegal_sequence():
    moves = json.dumps([[6, 6, 6, 5], [6, 6, 6, 5]])
    res = _call(web_api.replay_moves, moves)
    assert res["ok"] is False
    assert "ply 1" in res["error"]


def test_replay_rejects_malformed_payload():
    res = _call(web_api.replay_moves, "not json")
    assert res["ok"] is False
