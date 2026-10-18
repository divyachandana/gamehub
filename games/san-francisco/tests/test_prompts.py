"""Tests for prompt rendering and model-reply parsing."""

import pytest

from san_francisco.errors import InvalidMoveError
from san_francisco.maze import Heading, Move
from san_francisco.models import Observation, OptionView
from san_francisco.prompts import MAX_WHY_CHARS, build_messages, describe_goal, parse_move

ALLOWED = [Move.LEFT, Move.RIGHT]


def test_parses_clean_json() -> None:
    assert parse_move('{"move": "left", "why": "exit is west"}', ALLOWED) == (
        Move.LEFT,
        "exit is west",
    )


def test_parses_json_wrapped_in_prose_and_fences() -> None:
    reply = 'Sure!\n```json\n{"move": "RIGHT", "why": "go east"}\n```'
    assert parse_move(reply, ALLOWED) == (Move.RIGHT, "go east")


def test_falls_back_to_first_legal_move_word() -> None:
    assert parse_move("I would go straight, no wait, right.", ALLOWED) == (Move.RIGHT, "")


def test_rejects_illegal_move() -> None:
    with pytest.raises(InvalidMoveError):
        parse_move('{"move": "straight"}', ALLOWED)


def test_trims_long_reason() -> None:
    _, why = parse_move('{"move": "left", "why": "' + "x" * 200 + '"}', ALLOWED)
    assert len(why) == MAX_WHY_CHARS


def test_describe_goal() -> None:
    assert describe_goal(3, -2) == "3 blocks east, 2 blocks south"
    assert describe_goal(0, 0) == "right here"


def test_prompt_lists_only_available_roads() -> None:
    obs = Observation(
        node=(1, 1),
        heading=Heading.NORTH,
        goal_dx=4,
        goal_dy=5,
        options=[
            OptionView(move=Move.LEFT, compass="west", visits=0),
            OptionView(move=Move.RIGHT, compass="east", visits=2),
        ],
    )
    user = build_messages(obs)[1]["content"]
    assert "- left (goes west, new road)" in user
    assert "- right (goes east, already driven 2x)" in user
    assert "You are driving in circles. Take a new road: left." in user
    assert "straight" not in user
    assert "Pick exactly one of: left, right." in user
