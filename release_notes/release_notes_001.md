# Pronunciation Fixup (Aug 2, 2026)

Issue with pronunciation found while building the walkthrough.  Notes on the resolution and process.

The Pronunciation Guide.  This includes some awesome.
![pronunciation](assets/pronunciation.png)

Word as it appears in the text, override for how to pronounce it.  Stupid simple little webform, I know, right?

Expand one of them.
![alt text](assets/pronounce_machine.png)
<audio controls src="assets/Machine.wav"><audio>

if I type the word into "SoundsLike", I get that word the way the system guesses is correct.

I can drop that pronunciation in to overwrite the old one.  Then it generates the thing I was trying to fix:

![alt text](assets/pronounce_machine_worse.png)
<audio controls src="assets/Machine_Worse.wav"><audio>

SoundsLike is also great for building up a word if you don't know IPA very well.  Ask it for words that have common syllables with the word you want, snip off the IPA for one bit at a time.  It's very intuitive.

There are character set problems between the IPA that comes out of (?) and what works as input for kokuru.  but.. it may be self inflicted as the roundable from a user copy and pasting one unicode string to another is a likely culprit.

That said, it seems to work best if I use characters from:

[https://westonruter.github.io/ipa-chart/keyboard/](https://westonruter.github.io/ipa-chart/keyboard/)

as the authoritative versions, with string substituitions as I have found issues.

It's the `ʃ`, it sounds like shit.

Who is doing my pronouncing.. one second.
Okay, so misaki.en.G2P(trf=False,British=False)('machine')

Now this is interesting.  Threw together a harness and ran it.

```python
from misaki.en import G2P

print(G2P(trf=False)('machine'))
```

Do I get the `məˈʃin` I expect?  No.  I get:

```python
('məʃˈin', [MToken(text='machine', tag='NN', whitespace='', phonemes='məʃˈin', start_ts=None, end_ts=None, _={'is_head': True, 'num_flags': '', 'stress': None, 'prespace': False})])
```

`məʃˈin` sounds much better.  different Misaki versions?

* the "good" one is 0.9.4
* the 'bad' one baked into the comfyui image is 0.7.4

Well shit.  misaki 0.9.4 Requires-Python >=3.8,<3.13

but!!  from inside the comfy container:

```bash
bash-5.2# python3 --version
Python 3.12.13
```

you fucking kidding me right now pip?  3.12.13 _is_ less than 3.13.  I'm going to give you one chance you stupid little bastard.

```bash
# python3 -m pip install --upgrade pip
/usr/bin/python3: No module named pip
```

Now I'm cooked.  System pip inside the container sucks?

```bash
$ pip --version
pip 26.0.1 
```

I've been here before.  I've fixed this before.

```bash
$ docker compose exec -ti comfyui /bin/bash
bash-5.2# pip show misaki
Name: misaki
Version: 0.7.4
```

```bash
pip install git+https://github.com/hexgrad/misaki
```

The python install is all busted.  rebuild..
cooking.

Updating /user-scripts/pre-start.sh

`məˈʃin`

we want `məʃˈin`

ahh.. SoundsLike _doesn't use misaki_.  It uses Eng-to-IPA, which is the fallback pronunciation when misaki gives us the dreaded `?` or I just don't like the way it pronounced something.  But apparantly I didn't leave a way to go back

```python
    p = eng_to_ipa.jonvert(soundslike)
    if "ˈ" in p:
        ph = p.index("ˈ")
        p = p[:ph] + p[ph+1] + p[ph] + p[ph+2:]
```

`məʃˈin`

`ɹˈiᵊl pɹˈOɡɹˌæməɹz ɹˈOtˌin məʃˈin kˈOd.`

`real programmers wrote in machine code`

but `ɹˈOtˌin` should be `ɹˈOtiˌn` (ie: same swap for weak stress)

wth buddy?  is that misaki?

```ipa
ɹˈiᵊl pɹˈOɡɹˌæməɹz ɹˈOtˌɛɹin məʃˈin kˈOd.
ɹˈiᵊl pɹˈOɡɹˌæməɹz ɹˈOt ɪn məʃˈin kˈOd
```

it's the damn linefeeds.

`ɪn ðɪs dˈɛkədᵊnt ˈɛɹə ʌv ˈilˌIt bˈɪɹ,`

```ascii
   in this decadent era of

Lite beer,
```

Ahh, because I'm a moron.

```python
    if "ˈ" in p:
        ph = p.index("ˈ")
        p = p[:ph] + p[ph+1] + p[ph] + p[ph+2:]
```

This will only "fix" the first stress.

Fixed.  Stripping out linefeeds and handling as many stress symbols as there are in the input.

`ɪn ðɪs dˈɛkədᵊnt ˈɛɹə ʌv lˈIt bˈɪɹ,`

How did I fix it?

I'm passing the output of .jonvert() through:

```python
def fix_jonvert_accents(pronunciation_ipa: str) -> str:
    """
    Fix the accent marks in the pronunciation IPA string returned by eng_to_ipa.jonvert().
    """
    # fix common whitespace problems
    pronunciation_ipa = pronunciation_ipa.replace(" , ", ", ")

    out = ""
    swap = None
    for p in pronunciation_ipa:
        if p in ["ˈ", "ˌ"]:
            swap = p
        elif swap:
            out += p + swap
            swap = None
        else:
            out += p

    if swap:
        out += swap

    return out
```

There is an audio caching problem; I think a cachebuster on the .wav is going to be needed.  Otherwise the audio widget seems to be latching on to the old audio, even on page refresh.  Refresh and hold shift seems fine, so browser cache is almost certain the cause.

Not perfect, but _much_ better.  Push to main.
