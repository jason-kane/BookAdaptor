from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from styler.tag_consensus import ENGINES, agreed_tags
from styler.image_tags import ANALYSIS_VERSION


class TagConsensusTests(unittest.TestCase):
    def setUp(self):
        self.hashes = {engine:engine+'-image' for engine in ENGINES}
        self.records = {engine:dict(sha256=self.hashes[engine], prompt_sha256='prompt', analysis_version=ANALYSIS_VERSION,
                        tags=['oil painting','muted palette',engine]) for engine in ENGINES}

    def test_only_three_engine_intersection_is_promoted(self):
        self.assertEqual(agreed_tags(self.records,self.hashes,'prompt'), ['muted palette','oil painting'])

    def test_missing_engine_never_promotes_a_tag(self):
        del self.records['qwen']
        self.assertEqual(agreed_tags(self.records,self.hashes,'prompt'), [])

    def test_regenerated_image_and_changed_prompt_invalidate_agreement(self):
        self.hashes['zit']='replacement'
        self.assertEqual(agreed_tags(self.records,self.hashes,'prompt'), [])
        self.hashes['zit']='zit-image'
        self.assertEqual(agreed_tags(self.records,self.hashes,'edited prompt'), [])

    def test_three_engines_can_agree_on_no_tags(self):
        for engine in ENGINES:
            self.records[engine]['tags'] = [engine + str(index) for index in range(3)]
        self.assertEqual(agreed_tags(self.records,self.hashes,'prompt'), [])

    def test_explicit_empty_analysis_allows_agreement_by_other_two(self):
        self.records['qwen']['tags'] = []
        self.assertEqual(agreed_tags(self.records,self.hashes,'prompt'), ['muted palette','oil painting'])

    def test_two_empty_analyses_do_not_allow_one_engine_to_decide(self):
        self.records['qwen']['tags'] = []
        self.records['zit']['tags'] = []
        self.assertEqual(agreed_tags(self.records,self.hashes,'prompt'), [])

    def test_empty_and_missing_analysis_are_different(self):
        self.records['qwen']['tags'] = []
        self.hashes['qwen'] = 'regenerated'
        self.assertEqual(agreed_tags(self.records,self.hashes,'prompt'), [])

    def test_empty_consensus_falls_back_to_two_engine_support(self):
        self.records['zit']['tags'] = ['a','b','c','d']
        self.records['krea2']['tags'] = ['a','b','e','f']
        self.records['qwen']['tags'] = ['c','d','e','f']
        tags = agreed_tags(self.records,self.hashes,'prompt')
        self.assertIn(len(tags), (4,5))
        self.assertTrue(set(tags) <= {'a','b','c','d','e','f'})
        self.assertEqual(tags,agreed_tags(self.records,self.hashes,'prompt'))

    def test_fallback_never_invents_tags_to_fill_the_count(self):
        self.records['zit']['tags'] = ['a','x','y']
        self.records['krea2']['tags'] = ['a','b','c']
        self.records['qwen']['tags'] = ['d','e','f']
        self.assertEqual(agreed_tags(self.records,self.hashes,'prompt'), ['a'])

    def test_pairwise_tags_top_up_nonempty_unanimous_tags(self):
        self.records['zit']['tags']=['shared','a','b','c','unique1']
        self.records['krea2']['tags']=['shared','a','b','d','unique2']
        self.records['qwen']['tags']=['shared','c','d','e','unique3']
        tags=agreed_tags(self.records,self.hashes,'prompt')
        self.assertIn('shared',tags)
        self.assertIn(len(tags),(4,5))
        self.assertTrue(set(tags)<={'shared','a','b','c','d'})

    def test_old_analysis_is_not_used_for_new_tag_target(self):
        self.records['qwen'].pop('analysis_version')
        self.assertEqual(agreed_tags(self.records,self.hashes,'prompt'),[])
