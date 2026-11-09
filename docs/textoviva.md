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
The tab shows job status, playback, and a download link.

For legacy chapters, editor sections can identify title intervals without
regenerating assets when their text still matches the timeline. A phrase that
crosses a page boundary, changed text, missing frames/audio, or mismatched timing
requires another chapter import/render. Failed assembly preserves the previous
book MP4. Chapter files are never modified by book assembly.
