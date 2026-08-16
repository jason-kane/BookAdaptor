# great vowel shift, expressed in code as an ipa -> ipa filter.
import logger
import re

# The problem, and it is a critical problem, is that by using the modern IPA as
# our input, any silent letters have already been removed.  Those letters are
# not necessarily silent when we turn back the clock.

# which means.. we have to train one or more text -> (time period) IPA models.
# that is going to be interesting.

# if we make the "original" text through.. maybe we can use rules to awaken
# lost silent letters?


log = logger.log(__name__)


def as_pronounced_in_the_year_of_our_lord(year: int, ipa_input: str, latitude: float, longitude: float) -> str:
    """
    Applies historical pronunciation changes based on the given year and
    location.  Input is modern english IPA. Output has been altered to the best
    of our ability to reflect the pronunciation that would be typical in that
    time and place.

    Not a thing that one finishes.
    """
    if 1400 <= year < 1700:
        return middle_english(ipa_input)
    return ipa_input


def middle_english(ipa_input: str) -> str:    
    """
    Applies Middle English pronunciation changes to the given IPA input.
    """
    # First apply the Great Vowel Shift
    ipa_output = great_vowel_shift(ipa_input)
    # Additional Middle English pronunciation rules can be added here
    return ipa_output


# I can do chaucer too.  It will be wild.  It's a little stupid, but I want to
# build a UI that lets you modify a sort of database describing historical
# changes to how people sound.  submit as segment of "modern english" IPA, or
# just modern english and it will convert to modern english IPA.  That is the
# easy one. 

# You then pick a target, a year and place like england in 1400.  It will
# convert that modern pronunciation into the target pronunciation.  Think Malory
# in middle english or chaucer.  Dickens.  The goal is to pronounce like the
# author would have pronounced reading their own work.

# I'm going to wrap a UI around alteruphono.  I think I can make it a really
# good framework to explore historical pronunciation.  It looks really nice. I
# have: Text -> modern english IPA modern english IPA -> Audio
#
# The intent is to introduce localized historical pronunciation changes. In
# order to reach back to the middle ages, I will need to implement the great
# vowel shift (one easy example).  Rules are filters layered with time-posts,
# passed through in a specific sequence.

# "Improve" generally means adding more rules.  There is a point at which
# pronunciation rules becomes changing words.  becomes jibberish because the
# linguistic friction between the phrase and how a contemporary would have
# conveyed the same message is too great and it is reduced to ash.
# This is where the art of historical linguistics comes into play.
# The goal is to get as close as possible without losing the original message.
# This is a balancing act.  It has to feel authentic but relatable.

# Prompt. In an authentic but relatable and engaging way, modernize this ancient
# Middle Eastern religious text for a contemporary audience.  It must be clear
# and unambiguous while preserving the original meaning and tone. Total reading
# time must not exceed 6 hours. This must be deeply engaging and fullfilling.
# It must respect the source material and its cultural context.
#
# 

import re


class Accent:
    """
    Base class for accent transformations.
    """
    def apply(self, ipa_string):
        """
        Applies the accent transformation to the given IPA string.
        """
        raise NotImplementedError("Subclasses should implement this method.")
    
    def remove(self, ipa_string):
        """
        Removes the accent transformation from the given IPA string.
        """
        raise NotImplementedError("Subclasses should implement this method.")


class Rhotic(Accent):
    """
    A rhotic accent is a type of English where the "r" sound is pronounced in
    all contexts, including after vowels, such as in "car" or "bird". This is in
    contrast to a non-rhotic accent, where the "r" is often dropped in those
    same positions. Rhotic accents are common in the United States, Canada, and
    parts of Ireland, while many British accents are non-rhotic.
    """    

    # reversable mapping from non-rhotic to rhotic sounds
    _accent_map = {
        'ɑː': 'ɑɹ',      # e.g., "car" /kɑː/ -> /kɑɹ/
        'ɜː': 'ɝ',       # stressed open-mid central -> r-colored stressed, e.g., "bird" /bɜːd/ -> /bɝd/
        'ɪə': 'ɪɹ',      # e.g., "near" /nɪə/ -> /nɪɹ/
        'eə': 'ɛɹ',      # e.g., "square" /skweə/ -> /skwɛɹ/
        'ɔː': 'ɔɹ',      # e.g., "force" /fɔːs/ -> /fɔɹs/
        'ʊə': 'ʊɹ',      # e.g., "cure" /kjʊə/ -> /kjʊɹ/
    }

    def apply(self, ipa_string):
        """
        Non-Rhotic -> Rhotic
        
        Applies a General American English (GAE) rhotic accent to a non-rhotic
        IPA string.  ie: more American.
        """
        output_ipa = ipa_string

        # First, handle the simple vowel replacements
        for non_rhotic, rhotic in self._accent_map.items():
            output_ipa = output_ipa.replace(non_rhotic, rhotic)
       
        return output_ipa
    
    def remove(self, ipa_string):
        """
        Rhotic -> Non-Rhotic

        Removes a General American English (GAE) rhotic accent from an IPA string,
        converting it to a non-rhotic accent.  ie:  more British.
        """
        output_ipa = ipa_string

        # first revert the r-colored schwa /ɚ/ back to schwa /ə/
        output_ipa = re.sub(r'ɚ(\b)', r'ə\1', output_ipa)

        # Then reverse-map for rhotic to non-rhotic sounds
        for non_rhotic, rhotic in self._accent_map.items():
            output_ipa = output_ipa.replace(rhotic, non_rhotic)

        return output_ipa



def syllables(word: str) -> tuple[list, list]:
    """
    return word broken down into syllables.

    Getting all the syllables is just a repeated application of getting the last
    syllable.
    """
    syllable_list = []
    remainder = word
    error_log = []

    while remainder != "":
        error_log.append(f"Remainder: {remainder}")
        new_remainder, syllable, errors = get_last_syllable(remainder)
        if errors:
            error_log.extend(errors)
        if syllable:
            syllable_list.insert(0, syllable)
        if remainder == new_remainder:
            # we are stuck, bail out.
            raise ValueError("Stuck while processing syllables: %s" % remainder)

        remainder = new_remainder

    log.error(error_log)
    return syllable_list


def shatter_word(in_ipa):
    """
    Break an IPA word into its component phonemes.

    This is non-trivial because some phonemes are multiple characters long.
    """
    # known multi-character phonemes
    multi_char_phonemes = [
        'iː', 'eɪ', 'aɪ', 'ɔɪ', 'aʊ', 'əʊ', 'ɪə', 'eə', 'ʊə',
        'uː', 'ɑː', 'ɔː', 'ɜː',
    ]

    shattered = []
    i = 0
    while i < len(in_ipa):
        matched = False
        for phoneme in multi_char_phonemes:
            if in_ipa.startswith(phoneme, i):
                shattered.append(phoneme)
                i += len(phoneme)
                matched = True
                break

        if not matched:
            shattered.append(in_ipa[i])
            i += 1

    return shattered


def get_last_syllable(word: str) -> tuple:
    """
    Get the last syllable of a word.
    """
    error_log = [
        f"Getting last syllable of {word}"
    ]
    # are there syllable boundary markers?
    if re.search('[.-]', word):
        # split on the last one
        as_list = re.split('[.-]', word)
        prior = " ".join(as_list[:-1])

        error_log.append(
            'Splitting on syllable boundary marker at %s' % len(prior)
        )

        w = as_list[-1]
        if re.search(r"[ˈˌ]", w):
            as_list = re.split(r"[ˈˌ]", w)
            error_log.append(f'Found stress marker in syllable boundary split part: {w=} -> {as_list=}')
            
            new_prior = word[:-1 * (1 + len(as_list[-1]))] # +1 for the syllable boundary marker
            boundary_marker = word[len(new_prior)]

            error_log.append(
                f'returning {new_prior=}, {as_list[-1]=}, with boundary marker {boundary_marker}'
            )
            return new_prior, boundary_marker + as_list[-1], error_log
        
        return word[:len(prior)], as_list[-1], error_log

    # okay.. so work backwards from the end of the word
    # we want to find the last vowel or syllabic consonant
    monophthong = "iː e æ ɑː ʌ ɒ ɔː uː ʊ ɪ ɜː ǝ".split()
    diphthong = [
        "eɪ", "aɪ", "ɔɪ", 
        "aʊ", "əʊ", "ɪə", 
        "eə", "ʊə", "oʊ",
    ]
    #vowels = 'aɛiɪouɔʊəɜːæɑːe̞:ɔːu:ɪi:ʊu̯ɑu̯ɛiɔu̯eu̯iu̯'
    vowels = [
        'i', 'i:', 'y', 'ɨ', 'ʉ', 'ɯ', 'u', 'u:',
        'ɪ', 'ʏ', 'ʊ',
        'e', 'ø', 'ɘ', 'ɵ', 'ɤ', 'o',
        'e̞', 'ø̞', 'ə̞', 'ə', 'ɤ̞', 'o̞',
        'ɛ', 'œ', 'ɜ', 'ɜ:', 'ɞ', 'ʌ', 'ɔ', 'ɔ:',
        'æ', 'ɐ'
        'a', 'ɶ', 'ä', 'ɑ', 'ɑ:', 'ɒ'
    ]

    # lər

    # vowels = [
    #     'ɒ', 'ɑ', 

    #     'ɪ', 'ʊ', 'ə', 'e', 'ɒ', 'ʌ', 'æ',
    #     'iː', 'uː', 'ɑː', 'ɔː', 'ɜː',
    #     'ɛ','o','ɑ', 'i', 'ə', 'ɔ'
    # ]
    ## oʊ

    last_vowel_index = -1
    # wrong here buddy, sorry.  you can't just iterate an IPA like a string.
    # you have to consider modifier characters alongside the character they modify.
    
    shattered = shatter_word(word)
    error_log.append(f'Shattered word: {shattered}')
    for i in range(len(shattered)-1, -1, -1):
        if shattered[i] in vowels + diphthong:
            last_vowel_index = i
            break

    error_log.append(f'Last vowel found at index {last_vowel_index}')
    # TODO:
    # or syllabic consonants
    
    # is the "last" vowel the first letter of the word?
    if last_vowel_index == 0:
        # it can only be a single syllable word
        return "", word, error_log
    else:
        # are there adjacent vowel phonemes?
        if shattered[last_vowel_index-1:last_vowel_index+1] in diphthong:
            # our 'last' vowel is a diphthong
            last_vowel_index -= 1

        error_log.append(f'Finding syllable around vowel {shattered[last_vowel_index]} at index {last_vowel_index}')
        error_log.append(f'Somewhere in here: {"".join(shattered[max(0, last_vowel_index - 5):last_vowel_index + 5])}')
        # error_log.append(f"Last vowel index: {last_vowel_index}")
        if (
            last_vowel_index == -1
        ):
            return "", word, error_log
        
        if last_vowel_index: #  and len(word) > last_vowel_index + 2:
            if word[last_vowel_index-1:last_vowel_index+1] in diphthong:
                # last vowel is part of a diphthong
                error_log.append(f"Diphthong shortcut at {last_vowel_index}")
                last_vowel_index -= 1
            elif (word[last_vowel_index-1] in vowels):
                # two adjacent vowel phonemes == two syllables, we want the second one.
                error_log.append(f"Adjacent vowels shortcut at {last_vowel_index}")
                error_log.append(f'returning {word[:last_vowel_index]=}, {word[last_vowel_index:]=}')
                return word[:last_vowel_index], word[last_vowel_index:], error_log
            elif (
                word[last_vowel_index-1] == "h" and last_vowel_index == 1
            ) or (
                last_vowel_index == 2 and word[last_vowel_index-2:last_vowel_index] == "'h"
            ):
                # special case for "h" at the beginning of the word
                error_log.append(f"Special case for 'h' at the beginning of the word")
                return "", word, error_log
            else:
                # last vowel is at the end of the word
                error_log.append(f"{word[last_vowel_index-1:last_vowel_index+1]} is not a diphthong, {word[last_vowel_index-1]} is not a vowel.")
        else:
            error_log.append(f"{last_vowel_index=} is not truthy or {len(word)} < {last_vowel_index + 1}")

        # okay, so we know where the last vowel is.  We need to include the
        # consonants that are part of the onset of the syllable.  According to
        # some random dude on Redit, the valid _onset_ initial consonant clusers
        # are
        # (any single consonant)
        
        # except for 'ʔ' 'ɳ̊', 'ɳ', 'ɲ̊', 'ɲ', 'ŋ̊', 'ŋ', 'ʒ'
        
        # _all_ valid onsets
        single_consonants = [
            'm̥', 'm', 'ɱ̊', 'ɱ', 'm̠', 'n̼', 'n̪̊', 'n̪', 'n̥', 
            'ɴ̥', 'ɴ', 'p', 'b', 'p̪', 'b̪', 't̼', 'd̼', 't̪',
            'd̪', 't', 'd', 'ʈ', 'ɖ', 'c', 'ɟ', 'k', 'ɡ',
            'q', 'ɢ', 'ʡ', 'n', 'n̠̊', 'n̠', 'ð̠', 'ɽ̊', 
            's̪', 'z̪', 's', 'z', 'ʃ', 'ʂ', 'ʐ', 'ɕ', 'ʑ', 
            'ɸ', 'β', 'f', 'v', 'θ̼', 'ð̼', 'θ', 'ð', 'θ̠', 
            'ç', 'ʝ', 'x', 'ɣ', 'χ', 'ʁ', 
            'ħ', 'ʕ', 'h', 'ɦ', 'ʋ', 'ð̞', 'ɹ', 'ɹ̠', 'ɻ', 
            'j', 'ɰ', 'ʁ̞', 'ʔ̞', 'ⱱ̟', 'ⱱ', 'ɾ̼', 'ɾ̥', 'ɾ', 
            'ɽ', 'ɢ̆', 'ʡ̮', 'ʙ̥', 'ʙ', 'r̥', 'r ', 'r̠', '𝼈 ', 
            'ʀ̥', 'ʀ', 'ʜ', 'ʢ', 'l̪', 'l̥', 'ɺ̥', 'ɺ', '𝼈̊', 
            'ɬ̪', 'ɬ', 'ɮ', 'ꞎ', '𝼅', '𝼆', 'ʎ̝', '𝼄', 'ʟ̝', 
            'l', 'l̠', 'ɭ̊', 'ɭ', 'ʎ̥', 'ʎ', 'ʟ̥', 'ʟ', 'ʟ̠', 
            'ʎ̮', 'ʟ̆', 'ɓ', 'ɗ', 'ᶑ', 'ʄ', 'ɠ', 'ʛ', 'ɓ̥', 
            'ʛ̥', 'ʞ', 'ɗ̥', 'ᶑ̊', 'ʄ̊', 'ɠ̊', 'g'            
        ]
        
        two_consonant_clusters = [
            'ɹ̠̊˔', 'ɹ̠˔', 'ɻ̊˔', 'ɻ˔', 'β̞ ', 't͡ʃ', 'd͡ʒ'
            'sp', 'st', 'sk', 'sm', 'sn', 'pl', 'bl', 'kl',
            'gl', 'fl', 'sl', 'pɹ', 'bɹ', 'tɹ', 'dɹ', 'kɹ',
            'gɹ', 'fɹ', 'θɹ', 'sw', 'tw', 'dw', 'kw', 'gw', 
            'ʃw', 't̪s̪', 'd̪z̪', 'ts', 'dz', 't̠ʃ', 'd̠ʒ', 'tʂ', 
            'dʐ', 'tɕ', 'dʑ', 'pɸ', 'bβ', 'p̪f', 'b̪v', 'pl', 
            'pɹ', 'bɹ', 'tɹ', 'dɹ', 'kɹ', 'gɹ', 'bl', 'kl', 
            't̪θ', 'd̪ð', 'tɹ̝̊', 'dɹ̝', 'cç', 'gl', 'bj', 'tʃ',
            'ɟʝ', 'kx', 'ɡɣ', 'qχ', 'ɢʁ', 'ʡʜ', 'ʡʢ', 'ʔh',
            'ɽ̊r̥', 'ɽr', 'tɬ', 'dɮ', 'tꞎ', 'd𝼅', 'c𝼆', 'ɟʎ̝', 
            'ɡʟ̝', 'pʼ', 'tʼ', 'ʈʼ', 'cʼ', 'kʼ', 'qʼ', 'k𝼄', 
            'ɸʼ', 'fʼ', 'θʼ', 'sʼ', 'ʃʼ', 'q𝼊', 'kǂ', 'qǂ', 
            'ʂʼ', 'ɕʼ', 'xʼ', 'χʼ', 'ɬʼ', 'ɢ𝼊', 'ɡǂ', 'ɢǂ', 
            'kʘ', 'qʘ', 'kǀ', 'qǀ', 'kǃ', 'qǃ', 'k𝼊', 'ɴǂ', 
            'ɡʘ', 'ɢʘ', 'ɡǀ', 'ɢǀ', 'ɡǃ', 'ɢǃ', 'ɡ𝼊', 'ŋǂ', 
            'ŋʘ', 'ɴʘ', 'ŋǀ', 'ɴǀ', 'ŋǃ', 'ɴǃ', 'ŋ𝼊', 'ɴ𝼊', 
            'kǁ', 'qǁ', 'ɡǁ', 'ɢǁ', 'ŋǁ', 'ɴǁ', 'gw', 'ɡw',
        ]

        # two_consonant_clusters = [
        #     'sm', 'sn', 'st', 'sw', 'sk', 'sl', 'sp', 'sf', 'θw', 'dw', 
        #     'tw', 'θr', 'dr', 'tr', 'kw', 'kr', 'kl', 'pr', 'fr', 'br', 
        #     'gr', 'pl', 'fl', 'bl', 'gl', 'ʃr', 
        # ]
            
        three_consonant_clusters = [
            'spl', 'spr', 'str', 'sfr', 'skr', 'skw',
            'd̠ɹ̠˔', 't̠ɹ̠̊˔', 'p̪fʼ', 't̪θʼ', 't̠ʃʼ', 'tʂʼ',
            'tɕʼ', 'kxʼ', 'qχʼ','tɬʼ', 'c𝼆ʼ', 'k𝼄ʼ', 
            'q𝼄ʼ', 'tsʼ',
        ]
        knife = 0

        # three letter consonant cluster
        log.info(f"{word=} {len(word)=} {last_vowel_index=} word[{last_vowel_index}-3:{last_vowel_index}]")
        if last_vowel_index > 2 and word[last_vowel_index-3:last_vowel_index] in three_consonant_clusters:
            error_log.append(f'Matching three consonant cluster: {word[last_vowel_index-3:last_vowel_index]}')
            if word[last_vowel_index - 4] in ["ˈ", "ˌ"]:
                # include the stress marker with the syllable
                knife = last_vowel_index - 4
            else:
                knife = last_vowel_index - 3

        # two letter consonant cluster
        elif last_vowel_index > 1 and word[last_vowel_index-2:last_vowel_index] in two_consonant_clusters:
            log.info('MATCH!')
            error_log.append(f'Matching two consonant cluster: {word[last_vowel_index-2:last_vowel_index]}')
            if word[last_vowel_index - 3] in ["ˈ", "ˌ"]:
                # include the stress marker with the syllable
                knife = last_vowel_index - 3
            else:
                knife = last_vowel_index - 2

        # single consonant cluster
        elif last_vowel_index > 0 and word[last_vowel_index-1:last_vowel_index] in single_consonants:
            error_log.append(f'Matching single consonant: {word[last_vowel_index-1:last_vowel_index]}')
            if word[last_vowel_index - 2] in ["ˈ", "ˌ"]:
                # include the stress marker with the syllable
                knife = last_vowel_index - 2
            else:
                knife = last_vowel_index - 1
        else:
            error_log.append(f'No match 3 letter clusters: {word=}[{last_vowel_index-3}:{last_vowel_index}] ==> {word[last_vowel_index-3:last_vowel_index]}')
            error_log.append(f'No match 2 letter clusters: {word=}[{last_vowel_index-2}:{last_vowel_index}] ==> {word[last_vowel_index-2:last_vowel_index]}')
            error_log.append(f'No match 1 letter clusters: {word=}[{last_vowel_index-1}:{last_vowel_index}] ==> {word[last_vowel_index-1:last_vowel_index]}')
            
            log.info(f'word[last_vowel_index-2:last_vowel_index]: {word[last_vowel_index-2:last_vowel_index]}')
            # no consonants to include, just cut at the vowel
            if word[last_vowel_index - 1] in ["ˈ", "ˌ"]:
                # include the stress marker with the syllable
                knife = last_vowel_index - 1
            else:
                knife = last_vowel_index
        
        first_cut = word[knife:]
        error_log.append(f'{first_cut=}')

        if re.search(r"[ˈˌ]", first_cut):
            error_log.append(f'Found stress marker in {first_cut} [{knife=}]')
            # there is a stress marker _inside_ what we thought was the last
            # syllable.
            s = re.split(r"[ˈˌ]", first_cut)
            error_log.append(f'{s=}')
            
            if len(s) == 2 and len(s[0]) == 0:
                # the word starts with an stress marker.
                scalpel = 0
            else:
                scalpel = len(s[-1])
            
            if scalpel:
                error_log.append(f'Applying {knife=} + {scalpel=} - 1 to {word} (stress marker split)')
                return word[:knife + scalpel - 1], word[knife + scalpel - 1:], error_log
            else:
                return word[:knife], word[knife:], error_log
        else:
            error_log.append(f'No stress marker found in {first_cut} [{knife=}]')

        error_log.append(f'Cutting {word=} at {knife=} ({word[knife:]})')
        error_log.append(f'-------------|{"-" * knife}^')
        return word[:knife], word[knife:], error_log

    print(f"Failed: {word}")
    return "", "", error_log



class Schwa(Accent):
    """
    A Schwa accent transformation.
    """
    def apply(self, ipa_string):
        """
        Applies schwa insertion to the given IPA string.
        """
        # Simple example: insert schwa /ə/ after certain consonant clusters It
        
        # occurred in unstressed syllables, particularly in final positions, and
        # was central to the rhythmic structure of the language.
        out_words = []
        for word in ipa_string.split():
            # is the last syllable of 'word' stressed or unstressed?
            # are there any of these characters in the string?
            # "ˈˌ."
            # if re.search(r'[ˈˌ\.]', word):
            #     last_syllable = re.split(r'[ˈˌ\.]', word)[-1]
            # else:
            #     last_syllabel = get_last_syllable(word)
            #     # is it stressed or unstressed?    
            # else:
            out_words.append(word)
            
        return ' '.join(out_words)

    def remove(self, ipa_string):
        """
        Removes schwa insertion from the given IPA string.
        """
        output_ipa = ipa_string.rstrip('ə')
        
        # As a compensation for the loss of the final schwa, the vowel in an
        # open syllable (a syllable ending in a vowel sound) was often
        # lengthened.

        return output_ipa


class HomorganicLengthening(Accent):
    """
    A Homorganic Lengthening accent transformation.
    """
    def apply(self, ipa_string):
        """
        Applies homorganic lengthening to the given IPA string.
        """
        # lengthen vowels before homorganic consonants
        output_ipa = re.sub(r'([aeiou])([ptkbdgmn])\2', r'\1ː\2\2', ipa_string)
        return output_ipa
    
    def remove(self, ipa_string):
        """
        Removes homorganic lengthening from the given IPA string.
        """
        output_ipa = re.sub(r'([aeiou])ː([ptkbdgmn])\2', r'\1\2\2', ipa_string)
        return output_ipa


class GreatVowelShift(Accent):
    """
    A Great Vowel Shift accent transformation.

    - 1150 old english
    1150 - 1350 early middle english
    1350 - 1430 late middle english
    1430 - 1650 early modern english
    1650 - 1700 - modern english
    """

    def __init__(self, year=1400):
        self.year = year
        super().__init__()

    dialects = [
        'Southern',
        'East Midland',
        'West Midland',
        'Northern',
        'Kentish',
    ]

    # https://en.wikipedia.org/wiki/Great_Vowel_Shift
    
    # first round, getting this _right_ cannot be captured in simple mappings.
    
    # {'old' vowels: 'new' vowels} at each time step
    accent_timeline_map = {
        # During the 12th or the 13th century, /i/ was inserted between a front vowel and a following /h/ (pronounced [ç] in this context), and a vowel /u/ was inserted between a back vowel and a following /h/ (pronounced [x] in this context). A short /a/ was treated as a back vowel in the process;  
        1150: {
            # Early Middle English (1150-1350)
            'o:w': 'ow', # long 'ow' to 'ow', know
            'o:ɣ': 'ow', # long 'ow' to 'ow', know
            'ɛi': 'ai',
            'ei': 'i:',
            'eu': 'iu',
            'eih': 'i:h',
            'ouh': 'u:h',
        },
        # short vowels were lengthened in an open syllable (when they followed by a single consonant that in turn was followed by another vowel). In addition, non-low vowels were lowered: /i/ → /eː/, /e/ → /ɛː/, /u/ → /oː/, /o/ → /ɔː/. 
        # did not occur when two or more syllables followed
        # It only occasionally applied to the high vowels /i/ and /u/
        1200: {
            # Orrmish pronunciation
            # The sound [ɣ], which had been a post-vocalic allophone of /ɡ/, became vocalized to [u].
        },
        # Later in Middle English, vowels were shortened before clusters of two consonants, except before /st/ 
        # Double (geminated) consonants were reduced to single ones.
        1300: {
            # chaucerian pronunciation
            # final /ə/ was dropped, first when the following word began with a vowel
        },
        1400: {
            # unstressed /ə/ also dropped in the plural genitive ending -es (spelled -s in Modern English) and the past ending -ed.
        },
        1500: {
            # middle english to early modern english
            'i:': 'ɪi̯',  # long 'i' to 'ee', time (or əi?)
            'e:': 'i:',  # long 'e' to 'ee', see, meet
            'ɛ:': 'e̞:',  # long 'ee' to 'ay', east -- should be e with a little t (lowered)
            'a:': 'æ:',  # long 'a' to 'ah', name
            'æj': 'æ:i',
            'u:': 'ʊu̯',  # long 'oo' to 'oo', moon
            'ɔu̯': 'ou̯',  # long 'ow' to 'oh', know
            'ɑu̯': 'ɑ:ʊ̯', # long 'ow' to 'ah-oo', law
            'eu̯': 'i̯u:',  # long 'eo' to 'you', new
            'iu̯': 'i̯u:',  # long 'eo' to 'you', new
            'ɛu̯': 'e:u̯',  # long 'eou' to 'ay-oo', dew (juː?)

            'ai': 'e:',
            'æi': 'e:',
            'au': 'ɔː',
            'iu': 'juː',
            'ɔu': 'ou',
            'u:': 'əu',
            'aug': 'ɔː',  # long 'aw' to 'aw', law
            'ɛih': 'ei',
            'i:h': 'əi',
            'ɔuh': 'ou',  # or ɔf, or ɔː
            'u:h': 'ʊf'
        },
        # The phoneme /h/, when it occurred in the syllable coda, is believed to
        # have had two allophones: the voiceless palatal fricative [ç],
        # occurring after front vowels, and the voiceless velar fricative [x],
        # occurring after back vowels. The usual spelling in both cases was
        # ⟨gh⟩, which is retained today in words like night and taught.
        #
        # Those sounds were lost during later Middle English and Early Modern
        # English.
        #
        # Loss of the fricatives was accompanied by some compensatory
        # lengthening or diphthongization of preceding vowels. In some cases,
        # the velar fricative [x] developed into /f/;

        # This is not normally considered a part of the Great Vowel Shift, but during the same time period, most pre-existing Middle English diphthongs were monophthongized:
        # /ai̯/ → ENE /ɛː/ → /eː/ → NE /eɪ̯/
        # /au̯/ → ENE /ɔː/
        # /ɔu̯/ → ENE /oː/ → NE /oʊ̯/
        # The remaining diphthongs developed as follows:

        # /ɛu̯/, /iu̯/ → ENE /ɪu̯/ → NE /juː/. /ɪu̯/ is still used in Welsh English.
        # /ɔi̯/, /ui̯/ → NE /ɔɪ̯/        
        1550: {
            'æ:i': 'ɛ:i', # long 'ai' to 'ay', day
            'ɔː': 'o:',   # long 'aw' to 'oh', stone
            'e:u̯': 'iu̯',  # long 'eou' to 'you', dew
        },
        1600: {
            'e̞:': 'e:',  # long 'ay' to 'ay', east
            'a': 'æ',   # short 'a' to 'ah', cat
            'æ:': 'ɛ:',  # long 'ah' to 'ay', name
            'ɛ:i': 'ɛ:', # long 'ay' to 'ay', day
            'ʊu̯': 'əu̯',  # long 'oo' to 'uh-oo', moon
            'ɑ:ʊ̯': 'ɑ:', # long 'ah-oo' to 'ah', law
            'ɔ:ʊ̯': 'ɔ:', # long 'oh-oo' to 'oh', know
            'iu̯': 'i̯u:', # long 'you' to 'yoo', new            
            'o̞': 'ɔ',   # short 'o' to 'aw', dog (or ɒ)
            'ʊ': 'ɣ',   # short 'oo' to 'uh', cut
        },
        1650: {
            'ɪi̯': 'əi̯',  # long 'ee' to 'uh-ee', time
        },
        1700: {
            'i̯u:': 'ju:',  # long 'yoo' to 'you', new
            'e:u': 'ju:',  # long 'eou' to 'you', dew
            'o:': 'o̞:',   # long 'oh' to 'aw', stone
            'ou̯': 'o̞:',   # long 'oh' to 'aw', know
            'e:': 'i:',  # long 'ay' to 'ee', east
        },
        1750: {
            'əi̯': 'ʌi̯',  # long 'uh-ee' to 'eye', time
            'ɛ:': 'e:',  # long 'ay' to 'ee', east
            'əu̯': 'ɑu̯', # long 'uh-oo' to 'ow', moon
            'ɑ:': 'o̞:',  # long 'ah' to 'aw', law
            'ɔ:': 'o̞:',  # long 'oh' to 'aw', know
            'ɣ': 'ʌ̈',    # short 'uh' to 'uh', cut
        }, 
        1800: {
            'o̞:': 'o:ʊ̯',  # long 'aw' to 'oh', stone
        },
        1850: {
            'e:': 'eɪ',  # long 'ee' to 'ay', east
        },
        1900: {
            'o:ʊ̯': 'oʊ̯',  # long 'oh' to 'oh', stone
        },
        1950: {
            # unknown modern year?
            'ʌi̯': 'aɪ',  # long 'eye' to 'eye', time
            'ɑu̯': 'aʊ̯',  # long 'ow' to 'ow', mouth
            'oʊ̯': 'əʊ̯',  # long 'oh' to 'oh', stone
            'o̞:': 'ɔ:',   # long 'aw' to 'aw', law
            'ʌ̈': 'ʌ',    # short 'uh' to 'uh', cut
        },
        1999: {
            # unknown year 
            'au': 'o:',
            'ow': 'ɔw',
            'a:': 'e:',
            'i:': 'aɪ̯',  # or 'ai'?
        }
    }

    # _accent_map = {
    #     'i:': 'aɪ',   # long 'i' to 'eye'
    #     'e:': 'i:',   # long 'e' to 'ee'
    #     'ɛː': 'i:',  # long 'ee' to 'ee'
    #     'aː': 'eɪ',   # long 'a' to 'ay'
    #     'u:': 'aʊ',   # long 'oo' to 'ow'
    #     'o:': 'u:',   # long 'o' to 'oo'
    #     'ɔː': 'oʊ',   # long 'aw' to 'oh'
    #     'ɪ': 'i:',    # short 'i' to long 'ee'
    #     'ʊ': 'u:',    # short 'u' to long 'oo'
    #     'æɪ': 'eɪ',   # short 'a' to 'ay'
    #     'ɔɪ': 'ɔɪ',   # 'oy' stays 'oy'
    #     'ʊɪ': 'ɔɪ',   # 'ui' to 'oy'
    #     'aʊ': 'ɔ:',   # 'ow' to long 'aw'
    #     'eʊ': 'ju:',   # 'eo' to 'you'
    #     'ɛʊ': 'ju:',   # 'eou' to 'you'
    #     'ɔʊ': 'oʊ',   # 'oh' to 'oh'
    # }

    def apply(self, ipa_string):
        """
        Applies the Great Vowel Shift to the given IPA string.
        The IPA string expected to be an appropriate pronunication from <year>.
        """
        output_ipa = ipa_string

        if 2000 > self.year:
            # we are going back in time, our input is from <year> and we are
            # trying to pronounce it the way it would have been in self.year
            # instead.
            for shift_year in sorted(self.accent_timeline_map.keys(), reverse=True):
                if shift_year < self.year:
                    # we are done
                    break
                else:
                    for prev_gvs_ipa, post_gvs_ipa in self.accent_timeline_map[shift_year].items():
                        before = output_ipa
                        output_ipa = output_ipa.replace(post_gvs_ipa, prev_gvs_ipa)
                        if before != output_ipa:
                            log.info(f'[{shift_year}: {post_gvs_ipa} -> {prev_gvs_ipa}] {output_ipa}')
            return output_ipa

        else:
            # since we don't have a source of IPA formatted text from anything
            # other than modern english there isn't much point in going forward yet.
            raise NotImplementedError("GreatVowelShift.apply for forward time not implemented yet.")       


class MiddleEnglish(Accent):
    """
    A Middle English accent transformation.
    """
    def apply(self, ipa_string, year=1400):
        """
        Applies Middle English pronunciation changes to the given IPA string.
        """
        # remove the rhotic accent
        log.info(f'       Initial Value: {ipa_string}')

        ipa_string = Rhotic().remove(ipa_string)
        log.info(f'      Removed Rhotic: {ipa_string}')

        # re-pronounce this ipa_string which is from 2000, as if the speaker were
        # in 1400.
        ipa_string = GreatVowelShift(year=year).apply(ipa_string)
        log.info(f'Reversed Vowel Shift: {ipa_string}')

        # add schwa where appropriate
        ipa_string = Schwa().apply(ipa_string)
        log.info(f'     Restoring Schwa: {ipa_string}')

        # final /n/ dropped when part of an inflectional syllable
        # remained when part of the root like seven or in derivational endings like written

        return ipa_string


class OldEnglish(Accent):
    """
    An Old English accent transformation.
    """

    early_to_late = {
        'i:u̯': 'e:o̯',
        'iu̯': 'eo̯',
        'iy̯': 'y',
        'i:y̯': 'y:',

        'æɑ̯': 'æ',
        'æ:ɑ̯': 'æ:',
        'æ': 'a',
        'ɑ': 'a',
        'æ:': 'ɛː',
        'ɑ:': 'ɔː',

        'eo̯': 'ø',
        'eːo̯': 'ø:',
    }

    def apply(self, ipa_string, year=1000):
        """
        Applies Old English pronunciation changes to the given IPA string.
        """
        # remove the rhotic accent
        ipa_string = Rhotic().remove(ipa_string)

        # reverse the effects of the great vowel shift
        ipa_string = GreatVowelShift(year=year).apply(ipa_string)

        # add the schwa where appropriate
        ipa_string = Schwa().apply(ipa_string)

        # late old -> early middle:
        # /ø/ and /øː/ were soon respectively backened to /o/ and /oː/ between a
        # palatal consonant and a following syllable
        # and unrounded to /e/ and /eː/ everywhere else

        # /y/ and /yː/ unrounded to /i/ and /iː/ 

        # Late Old English
        if 850 < year < 1100:            
            # remove homorganic lengthening
            ipa_string = HomorganicLengthening().remove(ipa_string)

            # final unstressed /m/ became /n/

        # early old english
        if 450 < year <= 850:
            # Caedmon, Bede, Cynewulf, Aldhelm
            for early_ipa, late_ipa in self.early_to_late.items():
                ipa_string = ipa_string.replace(late_ipa, early_ipa)


        return ipa_string


class LateModernEnglish(Accent):
    """
    A Modern English accent transformation.
    """
    def apply(self, ipa_string):
        """
        Applies a Modern English accent to the given IPA string.
        """
        return ipa_string
    
    def remove(self, ipa_string):
        """
        Removes a Modern English accent from the given IPA string.
        """
        return ipa_string


class ModernEnglish(Accent):
    """
    A Modern English accent transformation.
    """
    def apply(self, ipa_string):
        """
        Applies a Modern English accent to the given IPA string.
        """
        return ipa_string
    
    def remove(self, ipa_string):
        """
        Removes a Modern English accent from the given IPA string.
        """
        return ipa_string


def accentByYear(year: int) -> Accent:
    """
    Returns an Accent transformation appropriate for the given year.
    """
    if year >= 1950:
        return LateModernEnglish()
    elif 1450 <= year < 2000:
        return ModernEnglish()
    elif 1100 <= year < 1450:
        return MiddleEnglish()
    elif 500 <= year < 1100:
        return OldEnglish()
    else:
        return Accent()


def great_vowel_shift(ipa_input: str) -> str:
    # Define the vowel shifts as a list of tuples (pattern, replacement)
    shifts = [
        (r'aː', 'eɪ'),   # The long "a" in mate was pronounced like the "ah" sound in Modern English father.
        (r'ɑː', 'eɪ'),   # long 'a' to 'ay'
        (r'æ', 'eɪ'),    # short 'a' to 'ay'
        (r'eɪ', 'iː'),   # long 'e' to 'ee'
        (r'iː', 'aɪ'),   # long 'i' to 'eye'
        (r'oʊ', 'uː'),   # long 'o' to 'oo'
        (r'o:', 'uː'),   # The long "o" in boot was a mid-back vowel, pronounced like the "o" in Modern English boat.	
        (r'u:', 'aʊ'),   # The long "oo" in bout was a high-back vowel, pronounced like the "oo" in Modern English boot.
        (r'uː', 'aʊ'),   # long 'u' to 'ow'
        (r'ɔː', 'oʊ'),   # long 'aw' to 'oh'
        (r'ɪ', 'iː'),    # short 'i' to long 'ee'
        (r'ʊ', 'uː'),    # short 'u' to long 'oo'
        (r'u:', 'aʊ'),   # The long "oo" in bout was a high-back vowel, pronounced like the "oo" in Modern English boot.
        (r'e:', 'i:'),   # The long "e" in meet was a mid-front vowel, pronounced like the "ey" in the Modern English word they or the vowel in French été.
        (r'ɛː', 'i:'),   # The long "e" in meat was a lower mid-front vowel, like the vowel sound in Modern English air but held longer.
    ]

    # Final -e: The final "-e" that is silent in Modern English was often
    # pronounced as a schwa (/ə/) in Middle English, especially in poetry to
    # maintain meter. 

    # The final /b/ and /g/ sounds in words like lamb and hang were pronounced. Lamb would have been spoken as [lamb], and the ending of hang was a distinct [ŋɡ] sound. 

    # The gh digraph, as in night, represented a voiceless velar fricative sound ([x]), similar to the "ch" in German Bach or Scottish loch. After front vowels, it was pronounced more like [ç]. In many cases, this sound later disappeared entirely or became an /f/ sound, as in rough.

    #  Letters like "k" and "g" in combinations like "kn" and "gn" were spoken, unlike in Modern English.

    # The voiced fricatives /v/, /z/, and /ð/ (the "th" sound in the) became distinct phonemes, meaning they were no longer simply variants of their voiceless counterparts (/f/, /s/, and /θ/).

    # The "w" in the "wr" cluster was pronounced, so write would have sounded like [wriːtə], rather than the Modern English /raɪt/. 

    # The initial /k/ and /g/ sounds in clusters like kn- (in knight) and gn- (in gnaw) were pronounced. For example, knight would have been said as something like /ˈknixt/, not /ˈnaɪt/.

    # Similarly, the initial /w/ in the cluster wr- (in write) was pronounced. So write sounded more like [wriːtə] than the modern /raɪt/.
    
    # Most words beginning with wh- were pronounced with a voiceless "w" sound ([ʍ]). This means that wine and whine were pronounced differently, a distinction that has been lost in many modern English dialects.
    
    # While usually pronounced /ʃ/ ("sh"), sc was sometimes pronounced /sk/. 

    # Apply each shift in order
    for pattern, replacement in shifts:
        ipa_input = re.sub(pattern, replacement, ipa_input)

    return ipa_input