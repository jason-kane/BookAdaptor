"""
Roman numeral routines, chapters with roman numerals are pretty common.
"""
import re
import inflect
import logger

log = logger.log(__name__)
log.info('Loading Roman numeral support...')

p = inflect.engine()

def is_roman_numeral(instr):
    # https://dev.to/alexdjulin/a-python-regex-to-validate-roman-numerals-2g99
    pattern = re.compile(r"""   
        ^M{0,3}
        (CM|CD|D?C{0,3})?
        (XC|XL|L?X{0,3})?
        (IX|IV|V?I{0,3})?$
    """, re.VERBOSE)

    if re.match(pattern, instr):
        return True

    return False   


def roman_to_int(s):
    roman = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    result = 0
    prev_value = 0

    for i in s[::-1]:
        value = roman[i]
        if value < prev_value:
            result -= value
        else:
            result += value
        prev_value = value

    return result


def numeral_to_spoken(numeral):
    """
    Convert a roman numeral to spoken text.
    """
    as_int = roman_to_int(numeral)
    return p.number_to_words(as_int)

log.info('Roman numeral support loaded')