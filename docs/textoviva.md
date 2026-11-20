# Book Textoviva

The book page has **Title Page** and **Chapter** Quill editors. Save a template
before importing chapters. The Chapter template expands `<CHAPTER TITLE>` and
`<CHAPTER NUMBER>`, including tokens split across Quill formatting runs.

Each section in a chapter's Text editor has an independent role and typesetting
method. Roles are **Book Page**, **Chapter Page**, and **Chapter Text**. New raw
imports create template copies for the first two roles and preserve the body as
Chapter Text. The selected raw-book parser supplies chapter headings; exact
leading title, author, and chapter lines are recognized conservatively. Review
unusual front matter in Text.

Template copies refresh on the next import. Editing the copied text or changing
its role makes that section independent of the book template. Existing manual
sections remain editable. Imported phrases and paragraphs retain section roles
and section boundaries in both XML and database storage.

**Textoviva** is enabled only when every chapter has a video. It includes every
chapter in numeric order, keeps the first chapter intact, and removes Book Page
intervals and their trailing pauses from subsequent chapters. The CPU Queue
assembles existing frame PNGs and matching narration samples without generating
new images, animation, or speech. All chapters must use the same aspect ratio.
The tab shows job status, playback, and a download link. When chapter video,
narration, or retained frame ranges have changed, it lists the affected chapter
numbers and changes the action to **Update book video**.

For legacy chapters, editor sections can identify title intervals without
regenerating assets when their text still matches the timeline. A phrase that
crosses a page boundary, changed text, missing frames/audio, or mismatched timing
requires another chapter import/render. Failed assembly preserves the previous
book MP4. Chapter files are never modified by book assembly.
## Whole-book chapter companion files

New whole-book assemblies publish three files together in the book's `textoviva/` directory:

- `book.mp4`: the assembled video.
- `book.chapters.json`: versioned chapter boundaries and source ranges.
- `book.toc.txt`: chapter-start timestamps and titles, one per line.

Download the companions from the book's Textoviva tab. Older book videos need a new
assembly to obtain a trustworthy map; chapter assets may have changed since they
were encoded, so current timings must not be assumed to describe an older MP4.

The JSON uses zero-based, end-exclusive frames: `[start_frame, end_frame)`.
Its `fps` numerator/denominator is the time base; frames are authoritative for
editing, while `start_seconds`, `end_seconds`, and `duration_seconds` are convenient
time values. TOC timestamps round chapter starts down to whole seconds.

Each chapter includes its number, title, frame count, original video/audio paths
and modification stamps, SHA-256 hashes of both source video and narration audio,
and `segments` mapping retained source-frame ranges to
book-frame ranges. Later chapters omit repeated Book Page sections, so replacing
a chapter must apply these cuts rather than insert its entire standalone video.
The manifest also records the source plan fingerprint and the assembled MP4's
size and modification stamp; downloads reject companions that do not match the
current MP4. New assemblies cache a separately encoded MP4 segment per chapter.
An update re-encodes only changed or new chapter segments, concatenates those
with cached unchanged segments using stream copy, shifts later chapter
boundaries when frame counts change, and refreshes the companion files. Removed
chapters are omitted. Older whole-book MP4s without a segment cache require one
full rebuild to prepare the cache; later updates are chapter-only. Failed
encoding or remuxing leaves the current book MP4 and companion manifest in
place.
