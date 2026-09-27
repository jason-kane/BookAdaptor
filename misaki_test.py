from misaki.en import G2P

print(G2P(trf=False)('''in this decadent era of Lite beer,'''))

# not kidding, this is what I get for "wrote"
# Pronunciation IPA: ❓ nˈɛvəɹ ɹˈOt tˌImdəlˈA lˈups, ˈiðəɹ, ˈivən wˌɛn ðə bˈɔki ❓ ɹəkwˈIəɹd ɐ dəlˈA bətwˈin ˈWtpˌʊt kˈɛɹəktəɹz tə wˈɜɹk ɹˈIt.

# good (misaki test harness)
# ɪn ðɪs dˈɛkədᵊnt ˈɛɹə ʌv lˈIt bˈɪɹ
# ɪn ðɪs dˈɛkədᵊnt ˈɛɹə ʌv ❓ lˈIt bˈɪɹ,

# bad (misaki via comfy)
# ɪn ðɪs dˈɛkədᵊnt ˈɛɹə ʌv ˈilˌIt bˈɪɹ,
# ɪn ðɪs dˈɛkədᵊnt ˈɛɹə ʌv ˈilˌIt bˈɪɹ,

#i wrote a letter to my darling wife
#I ɹˈOt ɐ lˈɛTəɹ tə mI dˈɑɹlɪŋ wˈIf