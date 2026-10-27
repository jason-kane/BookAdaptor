"""# Roman numeral support for book structure

Book parsers use Roman numerals to recognize chapter and book headings. This
module defines the accepted notation once and provides conversion helpers for
other callers that need an integer or spoken form.

## Callers and contracts

- ``src/book.py`` and ``src/artifact_editor/book/views.py`` call
  :func:`is_roman_numeral` when a whole cleaned line may be a heading. They need
  a boolean result and should not have to catch parsing errors.
- :func:`roman_to_int` and :func:`numeral_to_spoken` are module APIs, but have no
  other in-repository callers today. They accept canonical, uppercase Roman
  numerals from ``I`` through ``MMMCMXCIX``. Invalid values raise ``ValueError``.
- Spoken output uses the optional-at-call-time ``inflect`` dependency. Importing
  this module does not load it or initialize application logging/configuration.

## Why this module exists

The book parsers should share one precise rule for recognizing chapter labels.
Keeping validation and conversion together prevents readers from treating an
invalid sequence differently in different parts of the application. Speech
conversion is kept lazy so chapter detection remains lightweight.

## Extension guide

The regular expression encodes the conventional range 1–3999: thousands,
hundreds, tens, and ones in canonical subtractive notation. Update it together
with the conversion tests if the accepted Roman numeral convention changes.
``TODO(MONITOR)`` marks aggregate input distributions still worth learning; no
telemetry is emitted here, and raw book text must never be recorded.
"""

import re
from functools import lru_cache

# Each place-value group permits only the conventional canonical spellings.
# Together these groups cover positive integers through 3999 and reject zero.
_ROMAN_NUMERAL_PATTERN = re.compile(
    r'M{0,3}'
    r'(?:CM|CD|D?C{0,3})'
    r'(?:XC|XL|L?X{0,3})'
    r'(?:IX|IV|V?I{0,3})'
)

# Descending values make the right-to-left conversion rule explicit.
_SYMBOL_VALUES = {
    'I': 1,
    'V': 5,
    'X': 10,
    'L': 50,
    'C': 100,
    'D': 500,
    'M': 1000,
}


def is_roman_numeral(value: object) -> bool:
    """Return whether ``value`` is a canonical uppercase numeral from 1 to 3999."""
    if not isinstance(value, str) or not value:
        return False

    # TODO(MONITOR): aggregate input lengths and valid/invalid counts to check
    # whether the conventional range fits real headings; never record the text.
    return _ROMAN_NUMERAL_PATTERN.fullmatch(value) is not None


def roman_to_int(numeral: str) -> int:
    """Convert a canonical uppercase Roman numeral to its integer value."""
    if not is_roman_numeral(numeral):
        raise ValueError('Expected a canonical uppercase Roman numeral from I to MMMCMXCIX')

    total = 0
    largest_value_to_right = 0
    for symbol in reversed(numeral):
        value = _SYMBOL_VALUES[symbol]
        total += -value if value < largest_value_to_right else value
        largest_value_to_right = value
    return total


@lru_cache(maxsize=1)
def _spoken_number_engine():
    """Load and reuse inflect's number-to-words engine on first speech request."""
    import inflect

    return inflect.engine()


def numeral_to_spoken(numeral: str) -> str:
    """Convert a canonical numeral to the wording returned by ``inflect``."""
    number = roman_to_int(numeral)
    return _spoken_number_engine().number_to_words(number)
