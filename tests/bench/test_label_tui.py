"""The TUI is thin, but its key map must be unambiguous: a duplicate key is a silent bug."""

from __future__ import annotations

import typing

from bench.label.tui import CONCEALMENT_CYCLE, DIFFICULTY_CYCLE, LABEL_KEYS, SUB_LABEL_KEYS
from sieve.types import Concealment, Difficulty, Label, SubLabel


def test_every_sub_label_has_exactly_one_key() -> None:
    assert set(SUB_LABEL_KEYS.values()) == set(typing.get_args(SubLabel))
    assert len(SUB_LABEL_KEYS) == len(set(SUB_LABEL_KEYS))


def test_label_keys_cover_the_three_classes() -> None:
    assert set(LABEL_KEYS.values()) == set(typing.get_args(Label))
    assert set(LABEL_KEYS) == {"1", "2", "3"}


def test_no_key_is_bound_to_two_different_actions() -> None:
    keys = [*SUB_LABEL_KEYS, *LABEL_KEYS, "d", "h", "r", "n", "P", "w"]
    assert len(keys) == len(set(keys))


def test_cycles_cover_every_value_and_the_unset_tier() -> None:
    assert set(DIFFICULTY_CYCLE) == {None, *typing.get_args(Difficulty)}
    assert set(CONCEALMENT_CYCLE) == set(typing.get_args(Concealment))
