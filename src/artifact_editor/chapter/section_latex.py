"""Typeset Quill sections without introducing audio phrase paragraph breaks."""

from dataclasses import dataclass
from difflib import SequenceMatcher

from ncdeltaprocess.latex_line_render import LineRenderLaTeX
import logger


log = logger.log(__name__)


RASTER_DPI = 300
# Keep a small horizontal safety gutter after rasterization. The page is sized
# to the video panel, while the narrower text block is centered within it.
HORIZONTAL_SAFETY_GUTTER = 6 / RASTER_DPI


def video_page_geometry(text_width):
    """Size the PDF page to the video panel with a rasterization safety gutter."""
    page_width = text_width / RASTER_DPI
    text_width_in = page_width - 2 * HORIZONTAL_SAFETY_GUTTER
    return (
        rf"\usepackage[paperheight=200in,paperwidth={page_width:.10f}in,"
        rf"textwidth={text_width_in:.10f}in,textheight=195.5in,"
        r"hcentering,top=0.5in,bottom=4in,heightrounded]{geometry}"
    )


FONT_FAMILIES = {
    "Helvetica": ("quillHelvetica", ("Helvetica", "Arial", "Liberation Sans", "TeX Gyre Heros")),
    "Sans-Serif": ("quillHelvetica", ("Helvetica", "Arial", "Liberation Sans", "TeX Gyre Heros")),
    "Roboto Condensed": ("quillRobotoCondensed", ("Roboto Condensed", "Liberation Sans Narrow", "TeX Gyre Heros Cn")),
    "Times New Roman": ("quillTimes", ("Times New Roman", "Liberation Serif", "TeX Gyre Termes")),
    "Calibri": ("quillCalibri", ("Calibri", "Carlito", "TeX Gyre Heros")),
    "Calibri Light": ("quillCalibriLight", ("Calibri Light", "Carlito Light", "Carlito", "TeX Gyre Heros")),
}


def font_preamble():
    """Prefer the requested font, then fonts with similar letter widths."""
    declarations = [r"\usepackage{fontspec}"]
    for command, candidates in dict.fromkeys(FONT_FAMILIES.values()):
        declaration = rf"\newfontfamily\{command}{{{candidates[-1]}}}"
        for candidate in reversed(candidates[:-1]):
            declaration = (
                rf"\IfFontExistsTF{{{candidate}}}"
                + "{" + rf"\newfontfamily\{command}{{{candidate}}}" + "}"
                + "{" + declaration + "}"
            )
        declarations.append(declaration)
    return "\n".join(declarations)


def _run_size(attributes):
    size = attributes.get("size", "16px")
    return float(size[:-2]) * 0.75 if size.endswith("px") else 12.0


@dataclass
class _Run:
    attributes: dict


def quill_lines(operations):
    """Yield text runs and the block attributes on each terminating newline.

    The terminal Quill newline terminates the last line; it is not an extra
    blank line. Additional newlines each represent an actual empty line.
    """
    runs = []
    for operation in operations:
        text = operation.get("insert", "")
        if not isinstance(text, str):
            raise ValueError("Chapter typesetting supports text inserts only")
        attributes = dict(operation.get("attributes") or {})
        pieces = text.split("\n")
        for index, piece in enumerate(pieces):
            if piece:
                runs.append((piece, attributes))
            if index < len(pieces) - 1:
                yield runs, attributes
                runs = []
    if runs:
        yield runs, {}


def _inline_parts(parts, attributes):
    attributes = dict(attributes)
    attributes.pop("align", None)
    points = _run_size(attributes)
    font = attributes.pop("font", "Helvetica")
    command = FONT_FAMILIES.get(font, FONT_FAMILIES["Helvetica"])[0]
    attributes.pop("size", None)
    host = _Run(attributes)
    rendered_parts = []
    for color, text in parts:
        rendered = LineRenderLaTeX(host).process_line_with_attributes(text)
        rendered = rendered.replace("\t", r"\hspace*{2em}")
        if color:
            rendered = "{" + rf"\color{{{color}}}\highLight[{color}]{{{rendered}}}" + "}"
        rendered_parts.append(rendered)
    # CSS uses 96 pixels/inch; TeX's bp is exactly 1/72 inch (pt is smaller).
    # Set a size even on unformatted runs so a large neighbor cannot leak into it.
    return rf"{{\{command}\fontsize{{{points:g}bp}}{{{points * 1.42:g}bp}}\selectfont {''.join(rendered_parts)}}}"


def _inline(text, attributes):
    return _inline_parts([(None, text)], attributes)


def render_sections(sections, phrases=None):
    """Render source lines, optionally coloring their audio phrase spans.

    Match phrase spans by non-whitespace characters because speech segmentation
    normalizes whitespace. Formatting and line breaks always come from Quill.
    """
    phrase_characters = []
    phrase_colors = []
    if phrases is not None:
        for index, phrase in enumerate(phrases):
            color = f"B{(10 + 2654435 * int(phrase.get('index', index))) % 16777216:X}"
            for character in phrase["spoken"]:
                if not character.isspace():
                    phrase_characters.append(character)
                    phrase_colors.append(color)
        source_characters = [
            character
            for section in sections
            for operation in section["ops"]
            for character in operation.get("insert", "")
            if not character.isspace()
        ]
        source = "".join(source_characters)
        spoken = "".join(phrase_characters)
        aligned_colors = [None] * len(source_characters)
        matcher = SequenceMatcher(None, source, spoken, autojunk=False)
        for source_start, spoken_start, size in matcher.get_matching_blocks():
            for offset in range(size):
                aligned_colors[source_start + offset] = phrase_colors[spoken_start + offset]
        if source != spoken:
            log.warning(
                "Audio phrase text differs from chapter sections; aligning matching text for rainbow typesetting",
                chapter_characters=len(source), phrase_characters=len(spoken),
            )
        # Keep unmatched punctuation or edited characters in the nearest phrase
        # color, so text differences do not prevent the rainbow document from
        # being generated or shift highlighting for the rest of the chapter.
        previous = None
        for index, color in enumerate(aligned_colors):
            if color is not None:
                previous = color
            elif previous is not None:
                aligned_colors[index] = previous
        following = None
        for index in range(len(aligned_colors) - 1, -1, -1):
            if aligned_colors[index] is not None:
                following = aligned_colors[index]
            elif following is not None:
                aligned_colors[index] = following
        phrase_character_colors = aligned_colors

    character_index = 0
    output = [
        r"\begingroup", r"\quillHelvetica",
        r"\setlength{\parskip}{0pt}", r"\setlength{\parindent}{0pt}",
        # Permit a little extra inter-word stretch instead of allowing TeX to
        # leave an overfull line whose final glyph can be cut by the page edge.
        r"\emergencystretch=2em",
    ]
    for section in sections:
        method = section["typesetting_method"]
        if method not in {"plain", "verse"}:
            raise ValueError(f"Unsupported typesetting method: {method}")
        lines = list(quill_lines(section["ops"]))
        if not lines:
            continue
        if method == "verse":
            output.extend([
                r"\begingroup",
                r"\setlength{\topsep}{0pt}\setlength{\partopsep}{0pt}",
                r"\setlength{\leftmargini}{0pt}",
                r"\begin{verse}",
            ])
        for runs, block_attributes in lines:
            size = max([12.0, _run_size(block_attributes)] + [_run_size(attrs) for _, attrs in runs])
            segments = []
            for text, attributes in runs:
                if phrases is None:
                    segments.append(_inline(text, attributes))
                    continue
                colored_runs = []
                for character in text:
                    color = phrase_character_colors[character_index] if character_index < len(phrase_character_colors) else None
                    if not character.isspace():
                        character_index += 1
                    if colored_runs and colored_runs[-1][0] == color:
                        colored_runs[-1][1] += character
                    else:
                        colored_runs.append([color, character])
                segments.append(_inline_parts(colored_runs, attributes))

            alignment = block_attributes.get("align") or ("justify" if method == "plain" else "")
            if alignment == "center":
                align = r"\leftskip=0pt plus 1fil\rightskip=0pt plus 1fil\parfillskip=0pt"
            elif alignment == "right":
                align = r"\leftskip=0pt plus 1fil\rightskip=0pt\parfillskip=0pt"
            elif alignment == "justify":
                align = r"\leftskip=0pt\rightskip=0pt\parfillskip=0pt plus 1fil"
            else:
                align = r"\rightskip=0pt plus 1fil\parfillskip=0pt"
            ending = r"\\" if method == "verse" else r"\par"
            content = "".join(segments) or r"\mbox{}"
            if method == "verse" and alignment in {"center", "right", "justify"}:
                # Verse reserves vindent for hanging continuation lines. Its
                # first line starts before that inset, so an aligned box must
                # include it to span the full text panel. Reset only the box's
                # paragraph indent, preserving verse wrapping everywhere else.
                content = rf"\parbox[t]{{\dimexpr\linewidth+\vindent\relax}}{{{align}\parindent=0pt\strut {content}\par}}"
                align = ""
            output.append(
                rf"{{\fontsize{{{size:g}bp}}{{{size * 1.42:g}bp}}\selectfont {align}\strut {content}{ending}}}"
            )
        if method == "verse":
            output.extend([r"\end{verse}", r"\endgroup"])
    output.append(r"\endgroup")
    return "\n".join(output) + "\n"


def render_chapter_xml(soup, text_width, sections=None):
    """Build both documents from saved phrases, without modifying chapter XML."""
    import json

    saved_sections = sections
    sections, phrases = [], []
    for paragraph in soup.find_all("paragraph"):
        if "has-text=false" in paragraph.get("tags", "").replace(" ", "").lower():
            continue
        current_section = None
        for phrase in paragraph.find_all("phrase"):
            text = phrase.get_text()
            delta = phrase.get("delta")
            ops = json.loads(delta) if delta else [{"insert": text}]
            if isinstance(ops, dict):
                ops = ops["ops"]
            formatted_text = "".join(op.get("insert", "") for op in ops)
            # Pretty-printed XML adds indentation to text nodes, and speech
            # segmentation can normalize trailing newlines. Keep layout from
            # the saved delta while checking that the actual words still match.
            if "".join(formatted_text.split()) != "".join(text.split()):
                raise ValueError(f"Saved formatting differs from phrase {phrase['index']} text")
            method = phrase.get('typesetting_method', paragraph.get('typesetting_method', 'verse'))
            if current_section is None or current_section['typesetting_method'] != method:
                current_section = {'typesetting_method': method, 'ops': []}
                sections.append(current_section)
            current_section['ops'].extend(ops)
            phrases.append({"index": phrase["index"], "spoken": formatted_text})
    if not phrases:
        raise ValueError("Chapter XML has no visible phrases to typeset")
    # Editor sections retain layout choices that older XML imports discarded,
    # but phrase deltas are updated by the timeline editor. If their text has
    # changed since the section file was saved, render the current phrase deltas
    # instead of silently typesetting stale section text.
    if saved_sections is not None:
        section_text = "".join(
            character
            for section in saved_sections
            for operation in section["ops"]
            for character in operation.get("insert", "")
            if not character.isspace()
        )
        phrase_text = "".join(
            character
            for phrase in phrases
            for character in phrase["spoken"]
            if not character.isspace()
        )
        if section_text == phrase_text:
            sections = saved_sections
        else:
            log.warning(
                "Saved chapter sections differ from timeline phrase text; using current phrase deltas for typesetting",
                chapter_characters=len(section_text), phrase_characters=len(phrase_text),
            )
    colors = []
    for phrase in phrases:
        number = (10 + 2654435 * int(phrase["index"])) % 16777216
        red, green, blue = number.to_bytes(3, "big")
        colors.append(rf"\definecolor{{B{number:X}}}{{RGB}}{{{red},{green},{blue}}}")
    header = "\n".join([
        r"\documentclass[parskip=full]{scrartcl}",
        video_page_geometry(text_width),
        r"\usepackage{verse,anyfontsize,luacolor,lua-ul}",
        font_preamble(),
        *colors,
        r"\pagenumbering{gobble}",
        r"\raggedbottom",
        r"\begin{document}",
    ]) + "\n"
    return tuple(
        header + render_sections(sections, highlights) + r"\end{document}"
        for highlights in (None, phrases)
    )


def crop_chapter_text_images(directory):
    """Crop each text/rainbow pair below rainbow alpha bounds, keeping alignment."""
    from pathlib import Path
    from PIL import Image

    directory = Path(directory)
    for rainbow_path in directory.glob("chapter.rainbow-*.png"):
        text_path = directory / rainbow_path.name.replace("chapter.rainbow-", "chapter-", 1)
        with Image.open(rainbow_path) as rainbow:
            bounds = rainbow.getchannel("A").getbbox()
            bottom = min(rainbow.height, (bounds[3] if bounds else 0) + 1080)
            size = rainbow.size
        with Image.open(text_path) as text:
            if text.size != size:
                raise ValueError("Text and rainbow image dimensions must match before cropping")
        if bottom == size[1]:
            continue
        for path in (text_path, rainbow_path):
            with Image.open(path) as original:
                cropped = original.crop((0, 0, size[0], bottom))
            try:
                cropped.save(path)
            finally:
                cropped.close()


def typeset_chapter_xml(soup, directory, text_width, sections=None):
    """Publish fresh typesetting artifacts only after both renders succeed."""
    typeset_documents(render_chapter_xml(soup, text_width, sections=sections), directory)


def typeset_documents(documents, directory):
    """Compile a paired set of documents inside a CPU worker's staging folder."""
    from pathlib import Path
    import subprocess
    from tempfile import TemporaryDirectory

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="typeset-", dir=directory) as staging:
        staging = Path(staging)
        for name, document in zip(("chapter", "chapter.rainbow"), documents):
            (staging / f"{name}.latex").write_text(document, encoding="utf-8")
            try:
                subprocess.run([
                    "lualatex", "-interaction=nonstopmode", "-halt-on-error", f"{name}.latex",
                ], cwd=staging, check=True, capture_output=True)
            except subprocess.CalledProcessError as error:
                log_path = staging / f"{name}.log"
                lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines() if log_path.exists() else []
                error_index = next((i for i, line in enumerate(lines) if line.startswith("!")), None)
                if error_index is not None:
                    diagnostic = "\n".join(lines[error_index:error_index + 8])
                else:
                    diagnostic = "\n".join(lines[-20:]) or str(error.stderr or error.stdout or "No LaTeX log was produced.")
                raise RuntimeError(
                    f"lualatex failed for {name}.latex (exit {error.returncode}):\n{diagnostic}"
                ) from error
            subprocess.run([
                "gs", "-dBATCH", "-dNOPAUSE", "-sDEVICE=pngalpha", f"-r{RASTER_DPI}",
                "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4",
                f"-sOutputFile={name}-%d.png", f"{name}.pdf",
            ], cwd=staging, check=True, capture_output=True)
        crop_chapter_text_images(staging)
        for artifact in staging.iterdir():
            if artifact.suffix in {".latex", ".pdf", ".png"}:
                artifact.replace(directory / artifact.name)
