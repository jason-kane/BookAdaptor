# UI-first test audit

Scope starts at Library, Styler, Theater, and Comfy Workflow Templates. The
chapter Workshops trigger is disabled in **both** shared layouts and its menu
links are Jinja comments, so they neither render nor evaluate `url_for`.

Disabling Workshops does **not** make the chapter's Character Changes, Props,
Pronunciation, Timeline, player, or publication tabs obsolete. These remain
reachable through `chapter/templates/text.html`. Worker operations, imported
helpers, websocket handlers, shared model methods, and Comfy adapters are also
not obsolete merely because they lack a direct link.

## Running the isolated HTTP suite

From the repository root:

```sh
PYTHONPATH=src .venv/bin/pytest -q tests/ui \
  tests/test_styler_choose_tag.py tests/test_styler_tier.py \
  tests/test_styler_pagination.py tests/test_library_image_leaves.py
```

`tests/ui/conftest.py` compiles the actual view modules with their real Flask
route decorators and function bodies, replacing module-level service imports
with explicit collaborators. Tests use Flask's HTTP client, real Jinja templates
where practical, and temporary files. Function-local imports are preserved and
injected service modules are restored by pytest's monkeypatch fixture. Tests do
not initialize deployment `/active`, connect to Redis/Postgres, or start GPU
workers. This establishes HTTP/state contracts; it does not prove full app
registration, live worker execution, or browser component behavior.

## Feature ledger

The first column follows the visible UI, rather than the Python module tree.
"Existing" means test source is present, not that its entire suite passed in
this audit. A file-level mapping is not a claim of complete branch coverage.

| UI feature | New isolated tests | Existing behavior/browser tests |
| --- | --- | --- |
| Workshops disabled, no rendered destinations | `tests/ui/test_navigation.py` | — |
| Library inventory, author/book cards | `tests/ui/test_library.py` | — |
| Add author, duplicate/missing author | `tests/ui/test_library.py` | — |
| Book/chapter covers and first-image fallback, sound serving | `tests/ui/test_library.py` | — |
| Add book, book tabs, metadata title/subtitle/style/aspect | `tests/ui/test_book.py` | — |
| Shared book characters, validation | `tests/ui/test_book.py` | `src/artifact_editor/characters/tests/test_timeline_panel.py` |
| Chapter section load/save, verse/plain validation | `tests/ui/test_chapter.py` | `src/artifact_editor/chapter/tests/test_section_latex.py`, `tests/test_chapter_persistence.py` |
| Chapter title, mood/theme edits and generation errors | `tests/ui/test_chapter.py` | `src/artifact_editor/chapter/tests/test_views.py` |
| Text previews, plain/rainbow | `tests/ui/test_chapter.py` | `tests/test_phrase_highlight_bounds.py` |
| Chapter column collapse, resize, tabs | — | `tests/test_chapter_columns_js.cjs`, `tests/test_panel_drag_js.cjs` |
| Import, phrase parsing/audio, typesetting and status | — | `src/artifact_editor/chapter/tests/test_views.py`, `tests/test_cpu_typesetting.py`, `tests/test_cpu_queue_cancel_js.cjs` |
| Timeline phrase/image insertion, drag and timing | — | `src/artifact_editor/chapter/tests/test_timeline.py`, `tests/test_image_silence_duration.py` |
| Timeline delete and silence duration editing | `tests/ui/test_chapter.py` | `src/artifact_editor/chapter/tests/test_timeline.py` |
| Timeline scene creation/editing, character/prop references | — | `tests/test_scenes.py`, `tests/test_reference_images.py`, `src/artifact_editor/images/scene/tests/test_scene_characters.py` |
| Image editing, source/reference selection, generation/workflow opening | — | `src/artifact_editor/images/tests/test_workflow_selection.py`, `src/artifact_editor/images/tests/test_source_workflow.py`, `src/artifact_editor/images/tests/test_workflow_open.py`, `src/artifact_editor/images/tests/test_asset_batches.py` |
| Image completion leaves and manual redraw identity | — | `tests/test_library_image_leaves.py`, `tests/test_image_leaves_js.cjs`, `tests/test_chapter_image_socket_js.cjs` |
| Animation source, quality, rerender, frame count | — | `src/artifact_editor/images/tests/test_animation_source.py`, `tests/test_animation_workflow.py`, `tests/test_animation_rerender.py`, `tests/test_animation_frame_count.py` |
| Character extraction, appearance changes, validation and aliases | — | `src/artifact_editor/characters/tests/` |
| Props add/edit/extract/delete/generate | — | `src/artifact_editor/props/tests/test_props.py`, `tests/test_props_events.cjs` |
| Pronunciation add/edit/delete/scan and changed-audio invalidation | `tests/ui/test_pronunciation.py` | — |
| Player, source motion and video execution queues | — | `tests/test_source_motion.py`, `tests/test_video_execution_queues.py`, `tests/test_frame_cache.py` |
| YouTube connection, upload settings, metadata and upload | — | `tests/test_youtube_oauth.py`, `tests/test_youtube_upload.py`, `tests/test_youtube_metadata.py` |
| TikTok connection/upload | — | `tests/test_tiktok_publish.py` |
| Styler sample prompt, picker/search and queue presence | `tests/ui/test_styler.py` | `tests/test_styler_samples_js.cjs`, `tests/test_styler_queue_js.cjs` |
| Topics/subtopics/tag tabs, pending tree, rebuild | `tests/ui/test_styler.py` | `tests/test_styler_categories_js.cjs`, `tests/test_styler_idle_maintenance.py` |
| Search suggestions, target navigation and standalone cards | `tests/ui/test_styler.py` | `tests/test_styler_categories_js.cjs` |
| Style rename, manual tags/removal, deletion, reserved tag protection | `tests/ui/test_styler.py` | `tests/test_styler_tags_js.cjs`, `tests/test_styler_tag_consensus.py` |
| Per-engine tier controls | `tests/ui/test_styler.py` | `tests/test_styler_tier.py`, `tests/test_styler_tier_sliders_js.cjs` |
| Choosing tags, LOST paging/refill, retained cards | — | `tests/test_styler_choose_tag.py`, `tests/test_styler_pagination.py`, `tests/test_styler_page_refill_js.cjs`, `tests/test_styler_lost_js.cjs` |
| Stockpile paging, sample viewer, loaded image updates | — | `tests/test_styler_stockpile.py`, `tests/test_styler_image_viewer_js.cjs`, `tests/test_styler_loaded_js.cjs` |
| Sample generation, tags/reconciliation, queue dependencies | — | `tests/test_styler_jobs.py`, `tests/test_styler_retag.py`, `tests/test_styler_samples_contracts_js.cjs`, `tests/test_queue_dependencies_js.cjs` |
| Theater project creation/list/room and director validation | `tests/ui/test_theater.py` | `src/artifact_editor/theater/tests/test_theater.py` |
| Director story reading/title/caching, conversation without script mutation | `tests/ui/test_theater.py` | `src/artifact_editor/theater/tests/test_gpu_priority.py` |
| Read-along configuration, script save/review/archive | `tests/ui/test_theater.py` | — |
| Rough draft, rough film, final rendering and prerequisites | `tests/ui/test_theater.py` | `tests/test_video_execution_queues.py` |
| Theater media/status, queue errors, reset and image reset | `tests/ui/test_theater.py` | — |
| Workflow mode catalog, existing template edit and saved graph reuse | `tests/ui/test_workflow_templates.py` | — |
| Workflow creation, mode prime/generic prime fallback | `tests/ui/test_workflow_templates.py` | — |
| Prime edit, Comfy redirect and missing sample-image errors | `tests/ui/test_workflow_templates.py` | — |
| Obsolete warning emission and stable IDs | `tests/ui/test_obsolete_code.py` | — |

## Archived code and corrected classifications

`src_archive/` mirrors `src/` and holds sixteen unused top-level definitions
(including the complete legacy `Grouper` class) plus two inactive Styler templates.
`src_archive/manifest.json` records the moves. `docs/ui-obsolete-code.json` now
points at the archived functions and retains stable warning IDs and evidence.
There are no runtime imports or fallback calls into the archive.

Two earlier classifications were wrong and have been corrected:

- Book breakdown is reachable when a book has no chapters through
  `chapter.htmx.chapter_list`, which renders `breakdown_chapters.html`.
- `Card` is still used by the Favorite action through `/styles/choice`, even
  though its old docstring called it obsolete.

Both remain in `src/` and no longer emit obsolete warnings. Regression tests in
`tests/ui/test_archive_boundary.py` preserve those paths and check removal of
archived definitions and routes. Existing style-details/regeneration controls
and all shared worker dependencies remain active.

The archive is historical source, not an importable replacement implementation.
See `src_archive/README.md` for the deletion/restoration contract.

## Remaining verification before claiming every UI feature is covered

This is an initial feature ledger, not an exhaustive completed browser audit.
In particular, add/verify tests for GitHub publication, complete player controls
and director edits in the embedded editor, missing chapter-creation wiring,
all generated image/scene editor variants, and destructive bulk actions.
Theater image reset is tested without a persisted chapter; its per-image cleanup
branch still needs an integration fixture.

The new suite avoids full application initialization. The full deployment still
needs a disposable Library, Postgres/Redis and stub GPU/video workers to verify
registration, generated controls and asynchronous updates together. Existing
`src/conftest.py` creates the full app during collection and `/active` is absent
in this workspace. Do not label uncovered code obsolete on that basis.

Validation on this workspace:

- Focused Python command above: **174 passed** after the archive migration (includes archive integrity/warning tests).
- With `src_archive/` physically moved outside the repository and archive-only tests excluded: **133 passed**. The directory was restored afterward.
- Installed `jsdom` under `/tmp/bookadaptor-ui-tests` after the sandbox DNS lookup failed, then ran twelve existing JavaScript test files with `NODE_PATH=/tmp/bookadaptor-ui-tests/node_modules PYTHONPATH=src node --test ...`.
- Seven JavaScript files passed: chapter columns, image leaves, Styler loaded updates, LOST, page refill, tags, and tier sliders.
- Five existing JavaScript files failed: Styler categories (expected legacy target), image viewer (expected progress text), queue (expected retained row), samples (expected progress attribute), and sample contracts. These failures remain unresolved; they must be reconciled with the current UI before claiming complete coverage.
- The Comfy loader test hit `spawnSync python EPERM` in this environment.
- A broader exploratory Python run found stale Comfy adapter fixtures; it was not green and is not included in the focused pass count.
- `git diff --check` passed.
