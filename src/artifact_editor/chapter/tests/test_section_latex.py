"""Source-line regression checks, runnable without the Flask application."""

import importlib.util
from pathlib import Path
import sys
import unittest


spec = importlib.util.spec_from_file_location(
    "chapter_section_latex", Path(__file__).parents[1] / "section_latex.py"
)
renderer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = renderer
spec.loader.exec_module(renderer)


class SectionLatexTests(unittest.TestCase):
    def test_text_and_rainbow_crop_at_same_alpha_bottom_plus_1080(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from PIL import Image

        for height, content_bottom, expected in [(4000, 500, 1580), (1200, 500, 1200),
                                                  (4000, None, 1080)]:
            with self.subTest(height=height, content_bottom=content_bottom), TemporaryDirectory() as temp:
                directory = Path(temp)
                rainbow = Image.new('RGBA', (20, height), (99, 55, 33, 0))
                if content_bottom:
                    rainbow.putpixel((7, content_bottom - 1), (0, 0, 10, 1))
                rainbow.save(directory / 'chapter.rainbow-1.png')
                text = Image.new('RGBA', (20, height), (0, 0, 0, 0))
                text.putpixel((4, 100), (20, 30, 40, 255))
                text.save(directory / 'chapter-1.png')
                renderer.crop_chapter_text_images(directory)
                for name in ['chapter-1.png', 'chapter.rainbow-1.png']:
                    with Image.open(directory / name) as result:
                        self.assertEqual(result.size, (20, expected))
                with Image.open(directory / 'chapter-1.png') as result:
                    self.assertEqual(result.getpixel((4, 100)), (20, 30, 40, 255))
                renderer.crop_chapter_text_images(directory)
                with Image.open(directory / 'chapter-1.png') as result:
                    self.assertEqual(result.size, (20, expected))

    def test_video_page_matches_native_panel_width(self):
        import re
        for width in (840, 1080, 960):
            geometry = renderer.video_page_geometry(width)
            inches = float(re.search(r"paperwidth=([0-9.]+)in", geometry)[1])
            self.assertAlmostEqual(inches * renderer.RASTER_DPI, width, places=6)
            self.assertIn("left=0.1in,right=0.1in", geometry)

    def test_xml_typesetting_preserves_assets_and_phrase_ids(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("""<chapter><paragraph tags="has-text=false"><phrase index="0">Title</phrase></paragraph>
        <paragraph><image index="2" animation_prompt="keep"/><phrase index="7" src="audio.wav" duration="2" delta='[{"insert":"Hello "}]'>Hello </phrase><phrase index="12" delta='[{"insert":"world&amp;\\n"}]'>world&amp;
</phrase></paragraph></chapter>""", "xml")
        before = str(soup)
        plain, rainbow = renderer.render_chapter_xml(soup, 840)
        self.assertEqual(str(soup), before)
        self.assertNotIn("Title", plain)
        self.assertIn(r"\highLight[B11]", rainbow)
        self.assertIn(r"\highLight[B16]", rainbow)
        self.assertIn("Hello", plain)
        self.assertIn("paperwidth=2.8000000000in", plain)

    def test_pretty_printed_xml_uses_delta_line_breaks(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup("""<chapter><paragraph><phrase index="0" delta='[{"insert":"One\\n\\n"}]'>
   One
  </phrase><phrase index="5" delta='[{"insert":"Two\\n"}]'>
   Two
  </phrase></paragraph></chapter>""", "xml")
        before = str(soup)
        plain, rainbow = renderer.render_chapter_xml(soup, 840)
        self.assertEqual(str(soup), before)
        self.assertEqual(plain.count(r"\mbox{}"), 1)
        self.assertIn(r"\highLight[BA]", rainbow)
        self.assertIn(r"\highLight[BF]", rainbow)

    def test_verse_centering_is_applied_inside_a_completed_paragraph(self):
        sections = [{"typesetting_method": "verse", "ops": [{
            "insert": "Centered title\n", "attributes": {"align": "center"}
        }]}]
        for highlights in (None, [{"index": 7, "spoken": "Centered title"}]):
            output = renderer.render_sections(sections, highlights)
            self.assertIn(r"\parbox[t]{\dimexpr\linewidth+\vindent\relax}{\leftskip=0pt plus 1fil", output)
            self.assertIn(r"\par}", output)
            self.assertEqual(output.count(r"\\}"), 1)

    def test_verse_preserves_hanging_indents(self):
        output = renderer.render_sections([{
            "typesetting_method": "verse", "ops": [{"insert": "A long verse line\n"}]
        }])
        self.assertIn(r"\begin{verse}", output)
        self.assertNotIn(r"\setlength{\vindent}{0pt}", output)
        self.assertNotIn(r"\parbox", output)

    def test_failed_render_keeps_published_files(self):
        from bs4 import BeautifulSoup
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        import subprocess
        soup = BeautifulSoup('<chapter><paragraph><phrase index="4">Hello</phrase></paragraph></chapter>', "xml")
        with TemporaryDirectory() as directory:
            output = Path(directory) / "chapter.pdf"
            output.write_bytes(b"existing PDF")
            animation = Path(directory) / "animation.mp4"
            animation.write_bytes(b"existing animation")
            with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "lualatex")):
                with self.assertRaises(subprocess.CalledProcessError):
                    renderer.typeset_chapter_xml(soup, directory, 840)
            self.assertEqual(output.read_bytes(), b"existing PDF")
            self.assertEqual(animation.read_bytes(), b"existing animation")
            self.assertEqual(len(list(Path(directory).iterdir())), 2)

    def test_terminal_newline_is_not_a_blank_line(self):
        lines = list(renderer.quill_lines([{"insert": "first\nsecond\n"}]))
        self.assertEqual(len(lines), 2)

    def test_only_explicit_empty_lines_produce_blank_lines(self):
        output = renderer.render_sections([{
            "typesetting_method": "verse",
            "ops": [{"insert": "first\n\nsecond.\n\n\n"}],
        }])
        self.assertEqual(output.count(r"\mbox{}"), 3)
        self.assertEqual(output.count(r"\\}"), 5)
        self.assertNotIn("\n\n", output)

    def test_punctuation_and_phrase_breaks_do_not_change_source_lines(self):
        sections = [{"typesetting_method": "verse", "ops": [{
            "insert": "first without punctuation\nsecond.\nthird,\n"
        }]}]
        phrases = [
            {"spoken": "first without punctuation\nsecond.\nthird,"},
        ]
        plain = renderer.render_sections(sections)
        rainbow = renderer.render_sections(sections, phrases)
        self.assertEqual(plain.count(r"\\}"), 3)
        self.assertEqual(rainbow.count(r"\\}"), 3)
        self.assertNotIn("\n\n", rainbow)

    def test_alignment_and_inline_formatting_survive_operation_boundaries(self):
        output = renderer.render_sections([{
            "typesetting_method": "plain",
            "ops": [
                {"insert": "A & ", "attributes": {"size": "24px"}},
                {"insert": "B", "attributes": {"bold": True}},
                {"insert": "\n", "attributes": {"align": "center"}},
            ],
        }])
        self.assertIn(r"\textbf{B}", output)
        self.assertIn(r"A \&", output)
        self.assertIn(r"\fontsize{18bp}{25.56bp}", output)
        self.assertIn(r"\leftskip=0pt plus 1fil", output)
        self.assertNotIn(r"\begin{center}", output)
        self.assertNotIn(r"\begin{verse}", output)
        self.assertNotIn(r"\vspace", output)

    def test_highlighting_can_cross_a_source_line_without_adding_gaps(self):
        sections = [{"typesetting_method": "verse", "ops": [{
            "insert": "one two\nthree four\n"
        }]}]
        output = renderer.render_sections(sections, [
            {"spoken": "one "}, {"spoken": "two\nthree "}, {"spoken": "four\n"}
        ])
        self.assertEqual(output.count(r"\\}"), 2)
        self.assertEqual(output.count(r"\highLight[BB]"), 2)
        self.assertIn(" two}", output)
        self.assertIn("three}", output)

    def test_mixed_sizes_and_fonts_do_not_leak_between_runs(self):
        output = renderer.render_sections([{
            "typesetting_method": "plain",
            "ops": [
                {"insert": "Large", "attributes": {"font": "Roboto Condensed", "size": "24px"}},
                {"insert": "Default"},
                {"insert": "Small", "attributes": {"font": "Times New Roman", "size": "10px", "bold": True}},
                {"insert": "\n"},
            ],
        }])
        self.assertIn(r"\quillRobotoCondensed\fontsize{18bp}{25.56bp}", output)
        self.assertIn(r"\quillHelvetica\fontsize{12bp}{17.04bp}\selectfont Default", output)
        self.assertIn(r"\quillTimes\fontsize{7.5bp}{10.65bp}\selectfont \textbf{Small}", output)

    def test_font_declarations_include_exact_names_and_fallbacks(self):
        preamble = renderer.font_preamble()
        for family in ("Roboto Condensed", "Times New Roman", "Calibri", "Calibri Light"):
            self.assertIn(r"\IfFontExistsTF{" + family + "}", preamble)
        for fallback in ("Liberation Sans", "Liberation Serif", "Carlito"):
            self.assertIn(fallback, preamble)
        self.assertEqual(preamble.count(r"\IfFontExistsTF{Helvetica}"), 1)

    def test_consecutive_sections_keep_their_own_methods(self):
        output = renderer.render_sections([
            {"typesetting_method": "plain", "ops": [{"insert": "Heading\n"}]},
            {"typesetting_method": "verse", "ops": [{"insert": "poem\n"}]},
            {"typesetting_method": "plain", "ops": [{"insert": "Afterword\n"}]},
        ])
        self.assertLess(output.index("Heading"), output.index(r"\begin{verse}"))
        self.assertLess(output.index(r"\end{verse}"), output.index("Afterword"))
        self.assertNotIn(r"\mbox{}", output)


if __name__ == "__main__":
    unittest.main()
