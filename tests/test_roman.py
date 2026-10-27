"""Characterize the existing Roman numeral helpers before their rewrite."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import roman


def test_validator_accepts_canonical_values_in_the_supported_range():
    for numeral in ('I', 'IV', 'MCMXCIV', 'MMMCMXCIX'):
        assert roman.is_roman_numeral(numeral)


def test_validator_rejects_empty_noncanonical_and_non_string_values():
    for numeral in ('', 'IIII', 'IL', 'iv', 'MMMM', 'ABC', None, 4):
        assert not roman.is_roman_numeral(numeral)


def test_converter_handles_place_values_and_subtractive_notation():
    assert roman.roman_to_int('IV') == 4
    assert roman.roman_to_int('XLII') == 42
    assert roman.roman_to_int('CM') == 900
    assert roman.roman_to_int('MCMXCIV') == 1994
    assert roman.roman_to_int('MMMCMXCIX') == 3999


def test_converter_rejects_every_value_outside_the_canonical_contract():
    for numeral in ('', 'IIII', 'IL', 'A', 'iv', 'MMMM', None):
        with pytest.raises(ValueError, match='canonical uppercase Roman numeral'):
            roman.roman_to_int(numeral)


def test_spoken_conversion_delegates_the_converted_value_to_inflect():
    engine = SimpleNamespace(number_to_words=Mock(return_value='forty two'))
    with patch.object(roman, '_spoken_number_engine', return_value=engine):
        assert roman.numeral_to_spoken('XLII') == 'forty two'
    engine.number_to_words.assert_called_once_with(42)


def test_spoken_conversion_rejects_invalid_values_before_loading_inflect():
    with patch.object(roman, '_spoken_number_engine') as load_engine, pytest.raises(ValueError):
        roman.numeral_to_spoken('IIII')
    load_engine.assert_not_called()


def test_spoken_engine_is_loaded_lazily_and_cached():
    engine = object()
    factory = Mock(return_value=engine)
    with patch.dict('sys.modules', {'inflect': SimpleNamespace(engine=factory)}):
        roman._spoken_number_engine.cache_clear()
        try:
            assert roman._spoken_number_engine() is engine
            assert roman._spoken_number_engine() is engine
            factory.assert_called_once_with()
        finally:
            roman._spoken_number_engine.cache_clear()
