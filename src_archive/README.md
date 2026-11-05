# Archived UI code

This tree mirrors the paths under `src/`. It contains historical code that has
no identified caller from the current Library, Styler, Theater, or workflow
template UI, including work initiated through workers and websocket handlers.
It is not on the application's import path. Production code must never import
it or use it as a fallback.

This migration moves sixteen top-level definitions (including the entire unused
`Grouper` class) and two inactive Styler templates. `manifest.json` lists the
moves and the prior candidates that were retained. The warning/evidence registry
is `docs/ui-obsolete-code.json`; its paths now point here. Historical warning IDs
remain in the archived bodies.

The `.py` files are **source fragments for reference and restoration**, not
standalone modules: route/cache decorators and function bodies retain the
original module's globals. Restoring a fragment requires restoring its imports,
globals, and any associated templates to the original module. Do not import
these files to restore behavior.

## Corrected false positives

- `book.views.breakdown_chapters` and its helpers stay active. A book with no
  chapter cards renders `breakdown_chapters.html` through `chapter.htmx.chapter_list`.
- `styler.views.Card` stays active. Style details render the Favorite button,
  which calls `/styles/choice`, whose handler uses `Card.from_style_name`.

Their obsolete warnings were removed. Never classify code from an "obsolete"
comment alone; follow the current callers, conditional templates and UI actions.

## Deletion contract

The active UI tests must pass when this entire directory is absent. Archive
integrity/warning tests intentionally read this directory and are excluded from
that check. The active code has no references to `src_archive`; obsolete routes
are removed rather than redirected into this tree.

This is an evidence-backed migration, not a proof that every remaining function
across the repository is reachable. The broader browser/service integration gaps
remain documented in `docs/ui-feature-tests.md`.
