"""Tests for the messy-CSV unit-list parser.

``counter_units`` / ``team_units`` columns are stored as stringified Python
lists, comma joined values, or already-parsed lists depending on where they
came from. ``parse_unit_list`` has to normalize all of those into a clean
``list[str]``.
"""

import numpy as np

from swgoh.make_strategy import parse_unit_list


def test_parses_repr_list():
    assert parse_unit_list("['LUKE', 'HAN']") == ["LUKE", "HAN"]


def test_parses_repr_tuple():
    assert parse_unit_list("('LUKE', 'HAN')") == ["LUKE", "HAN"]


def test_parses_comma_joined_string():
    assert parse_unit_list("LUKE, HAN") == ["LUKE", "HAN"]


def test_passes_through_real_list_and_strips():
    assert parse_unit_list(["  LUKE ", "HAN"]) == ["LUKE", "HAN"]


def test_drops_empty_tokens():
    assert parse_unit_list("['LUKE', '', '  ']") == ["LUKE"]


def test_empty_string_is_empty_list():
    assert parse_unit_list("") == []


def test_nan_is_empty_list():
    assert parse_unit_list(np.nan) == []


def test_single_bare_token():
    assert parse_unit_list("LUKE") == ["LUKE"]
