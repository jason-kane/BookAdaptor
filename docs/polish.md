# Polish

Polish is a review system for model-generated improvements. Each suggested change
advances exactly one goal and records the model responsible for it. The reviewer
compares the status quo with that model's output and selects Trash, Bad, Good, or
Awesome. Those ratings supply evidence for comparing models over useful work.

A task is assigned to one model. Model comparison accumulates across tasks;
it does not require assigning the same task to multiple models.

Models may run locally through Ollama or remotely through the OpenAI API.
Model provenance must distinguish provider, model identity/version, and the
prompt revision used for that suggestion. Preserve that provenance when a
model alias or prompt changes later.

Code improvements use syntax-highlighted side-by-side diffs. UI improvements
use before/after images. Prompt improvements, including changes to Polish's
own prompts, use text diffs. Every entry includes a description of the suggested
change and its goal.

## First goal

**G001 — Build and maintain an exhaustive list of goals.**

The agreed initial scope is **Polish and BookAdaptor**. The catalogue covers
both systems and their shared development and operation. It remains a living
list: completeness is checked against product areas, workflows, and existing
project notes as the systems evolve.

G001 is the first stated goal. The remaining items are proposed goals for
review, not an approved implementation queue. Each proposed addition is a
Polish entry attributed to **GPT 6.1 Sol Medium**, advancing G001. An empty
before side means the candidate goal has not yet been established. Ratings
record model quality. Good and Awesome now adopt conflict-free proposals
immediately, following the user's scoring and application policy below. Goals describe outcomes; concrete
tasks and suggested changes should link back to them.

## Initial goal catalogue

### Polish

| ID | Goal | Evidence of completion or ongoing success |
| --- | --- | --- |
| G001 | Build and maintain an exhaustive list of goals. | Coverage is reviewed against the agreed scope; omissions become goals and duplicates are consolidated. |
| G002 | Make goals clear and actionable. | Each goal explains the desired outcome, scope, and how progress can be judged. |
| G003 | Keep a long goal list navigable. | Goals can be found, grouped, prioritized, and tracked through their lifecycle. |
| G004 | Connect every suggested change to one goal. | A reviewer can see which goal the suggestion advances; unrelated changes become separate suggestions. |
| G005 | Turn goals into bounded tasks. | Each assigned task has enough context and a reviewable expected result. |
| G006 | Use model effort without duplicating tasks for comparison. | Tasks go to one model, and assignment history prevents accidental duplicate execution. |
| G007 | Support local Ollama models. | Selected local models can receive tasks and return attributable suggestions. |
| G008 | Support OpenAI API models. | Selected API models can receive tasks and return attributable suggestions. |
| G009 | Preserve the origin of every suggestion. | Provider, model identity/version, prompt revision, and task are retained with the result. |
| G010 | Preserve a trustworthy status quo. | Before/after evidence refers to the same task and baseline revision; later changes do not silently replace that evidence. |
| G011 | Make code changes easy to judge. | Reviewers see syntax-highlighted, aligned code diffs with enough surrounding context. |
| G012 | Make UI changes easy to judge. | Reviewers see corresponding before/after screenshots with relevant viewport and state context. |
| G013 | Make prompt changes easy to judge. | Reviewers see the prompt diff, its intended effect, and the revision responsible for subsequent outputs. |
| G014 | Keep reviewing fast and consistent. | A vertical review list presents each description, goal, comparison, and the same four reaction levels. |
| G015 | Record ratings reliably. | Each suggestion has one current rating; changing it updates its contribution without counting it twice. |
| G016 | Compare models using accumulated ratings. | Model summaries show rating counts, sample sizes, and the tasks/goals represented. |
| G017 | Make model comparisons interpretable. | Different task types, difficulty, and assignment mix are visible rather than hidden behind one unexplained score. |
| G018 | Improve Polish's prompts through its own review process. | Prompt suggestions carry goal/model provenance and rated results can be traced to prompt revisions. |
| G019 | Keep task execution observable and recoverable. | Queued, running, failed, and completed tasks are distinguishable; retries retain their relationship to the original task. |
| G020 | Preserve review data and evidence. | Goals, task records, model provenance, comparison artifacts, and ratings survive service restarts and can be backed up. |
| G021 | Control generation cost and local resource use. | Model work respects configured budgets and capacity; failed work and retries remain attributable. |
| G022 | Make reviewed improvements usable. | A rated suggestion has a traceable path to application and verification; its rating alone does not silently apply it. |


### BookAdaptor

These goals are derived from the product purpose in `README.md`, existing
`TODO.md` and `src/TODO.md` notes, the application areas, and the focused docs
listed below. Historical TODO items are leads for investigation; their wording
does not establish that a bug still exists. Research ideas remain candidates,
not commitments to a particular technology or implementation.

| ID | Goal | Evidence of completion or ongoing success |
| --- | --- | --- |
| G023 | Make book-to-video adaptation an effective iterative workflow. | Users can move from source text through editorial choices to a finished read-along video, revisiting individual stages without losing work. |
| G024 | Make installation and updates reproducible. | Documented setup brings up the application and required dependencies; upgrades preserve user data and configuration. |
| G025 | Reduce build time and image footprint. | Measured build stages improve, cached dependencies are reused, and the required runtime capabilities remain available. |
| G026 | Make navigation and service launching clear. | Users can find their book, chapter, workshop, or tool and start only the services required for the chosen work. |
| G027 | Make library import and organization dependable. | Books and source files can be imported, located, opened, and organized across configured storage locations. |
| G028 | Preserve source text and editorial structure. | Conversion and editing retain intended chapter boundaries, paragraphs, dialogue, verse, and front/back matter. |
| G029 | Make language handling consistent. | Text, pronunciation, voices, typography, and generated assets use the intended language throughout an adaptation. |
| G030 | Extract and maintain faithful character information. | Character identity and descriptions remain grounded in the source, including when chapters exceed a model's context budget. |
| G031 | Keep speaker assignments accurate and editable. | Spoken phrases refer to the intended character, and corrections remain consistent through later generation. |
| G032 | Make voices and delivery controllable. | Narrator and character voice choices, effects, and enabled state persist and produce the intended delivery. |
| G033 | Improve pronunciation and speech quality. | Users can identify and correct difficult words, regenerate affected speech, and judge the resulting audio. |
| G034 | Keep timing and phrase edits coherent. | Splits, merges, pauses, and duration adjustments preserve text/audio alignment and update affected downstream assets. |
| G035 | Produce crisp, faithful typography. | Typeset pages preserve structure and readability, including verse, titles, stanza spacing, and ornaments. |
| G036 | Keep typography and read-along highlighting synchronized. | Page images, phrase maps, camera positions, and rendered frames refer to compatible source and layout revisions. |
| G037 | Maintain story consistency across visual assets. | Characters, props, places, wardrobe, and scene context remain consistent and editable across chapters. |
| G038 | Make image prompts and styles controllable. | Prompt templates, style choices, metadata, reference selections, and per-image settings visibly affect the intended generation. |
| G039 | Support deliberate image authoring. | Users can sketch, compose, crop, and refine reference material, including an optional image-to-image finishing pass. |
| G040 | Make generated assets easy to inspect and select. | Users can compare, choose, reuse, and remove prior results with clear previews and dependable updates. |
| G041 | Improve motion, framing, and transitions. | Camera choices, animations, and transitions use the selected assets and remain predictable in previews and final video. |
| G042 | Make timeline and render planning dependable. | Timeline edits and master plans preserve intended durations, ordering, and asset relationships. |
| G043 | Make frame and video rendering correct and incremental. | Final output matches the plan; users can regenerate affected frames or sections while retaining valid completed work. |
| G044 | Make music and sound effects usable. | Audio additions can be selected, positioned, previewed, and mixed appropriately with narration. |
| G045 | Support source-grounded Theater development. | Story-to-screen planning preserves source evidence while allowing explicit director and creative choices. |
| G046 | Make Textoviva outputs useful and traceable. | Book and chapter companion outputs correspond to the intended source, scope, and generation settings. |
| G047 | Make local LoRA creation and reuse dependable. | Training inputs, progress, outputs, and model selections are inspectable and usable by the intended workflows. |
| G048 | Make publishing and export reliable. | Users can prepare, inspect, export, or publish the intended artifact with accurate metadata and clear outcomes. |
| G049 | Preserve asset provenance and authorship. | Imported, generated, and manually authored assets retain their source and relevant creation settings. |
| G050 | Make long-running work observable and recoverable. | Acknowledged requests reach persisted queues, progress survives reloads, and failures, cancellation, and retries have clear outcomes. |
| G051 | Coordinate local compute and model resources reliably. | CPU/GPU jobs use the intended workers; provider handoffs, unloading, and resource contention avoid lost work. |
| G052 | Keep edits and cached outputs consistent. | Saving, invalidation, regeneration, and service reloads ensure users see results from the current inputs and code. |
| G053 | Preserve project data through storage changes and recovery. | Database records, files, and backups remain coherent across restarts, moves, migrations, and recovery. |
| G054 | Make the UI readable, accessible, and responsive. | Essential workflows support keyboard use, clear state feedback, appropriate layout, and practical chapter sizes. |
| G055 | Keep credentials and service access appropriately protected. | Secrets stay out of source and generated artifacts, and integrations use access appropriate to their task. |
| G056 | Make maintenance and verification practical. | Focused tests, useful logs, modular code, and current documentation support safe changes and verification in the running app. |

### Coverage and refinement

G001 is complete for a review cycle when every known product area and outstanding
requirement has a goal or an explicit decision to defer it, and no known gap is
left unassigned. New discoveries can reopen the catalogue review. This initial
inventory is a starting point, not a claim that all future goals are known.

The current coverage pass includes Library; source conversion and language;
characters and speakers; narration and pronunciation; typography; images,
styles, props, places, and reference assets; timeline, motion, and rendering;
music and sound; Theater; Textoviva; LoRA training; publishing; Home;
providers and workers; persistence; and development operations.

Relevant source material:

- `README.md`: product purpose and iterative adaptation workflow.
- `TODO.md`: builds, navigation, character extraction, props, image authoring,
  language support, and workflow organization.
- `src/TODO.md`: historical workshop, audio, typography, animation, and rendering
  requirements; individual issue status needs verification.
- `home/README.md`: service dashboard, launchers, and resource coordination.
- `docs/library-locations.md`: storage and library locations.
- `docs/textoviva.md`: book and chapter companion outputs.
- `docs/lora-training.md`: local training workflow.
- `docs/theater/director-research.md`: candidate Theater methods.

Keep these goal IDs stable as wording is refined. A concrete improvement task
selects one goal; broader work spanning goals is broken into separate tasks.

## Additional user-defined goals

**G057 — Condense goals to reduce evaluation context.**

Condense goals as needed to reduce their number and the context required to
evaluate them, while preserving their intent. This goal was supplied directly
by the user and is established in the live catalogue; it is not a model-rated
suggestion.

## Decisions still to make

- Goal grouping, priority, lifecycle, and how the catalogue is maintained in the UI.
- Richer task context selection, additional proposal types, and assignment policies beyond an explicit per-goal model choice.
- How assignment balances useful work and adequate evidence across models.
- Deployment and verification of applied code changes in affected services.

## Scoring and application policy

- Trash contributes −2.5 points and Bad −0.5. Good contributes +1; Awesome +5.
- Totals are derived from each entry's current rating. Repeating a rating does
  not add points; changing Good to Awesome changes its contribution from 1 to 5.
- Good and Awesome immediately apply the proposal if its target is compatible
  with the recorded baseline. Goal proposals enter the live goal catalogue;
  text-file proposals merge the reviewed change into the current workspace.
- Use Git three-way merging with the reviewed baseline, current target, and
  proposed contents. Non-overlapping edits combine automatically. Overlapping
  edits produce a warning and leave the target untouched. Conflict-marker output
  is never written to a target. Do not use ours/theirs/union or invoke an LLM to
  resolve overlap. Independently created or deleted targets conflict unless the
  current result already matches the proposal.
- Goal updates merge title and completion-evidence fields independently; text
  overlap within a field uses the same Git merge engine.
- Bad and Trash skip application and conflict checking entirely.
- A conflicting positive rating still contributes its specified points; the
  application warning is separate from the model-quality rating. Re-selecting
  the positive rating retries application after the conflict is addressed.
- Changing a rating does not undo an already applied change. An applied entry
  never replays its old contents over subsequent edits.
- Keep rating sample counts and task mix visible when comparing models.

The initial goal proposal wording above is a historical proposal snapshot. In
particular, G022 predates the user's immediate-application policy; this section
supersedes that earlier policy assumption without rewriting submitted evidence.

## Application targets

`home.polish.proposals.submit_proposal` accepts an optional explicit `target`.
New goal changes record the goal ID and before/after objects containing `title`
and `evidence`. Text-file changes record a relative workspace `path` and exact
before/after text; a null baseline means a new target. File target evidence must
match the visible diff. Targets outside the workspace, symlinks, and `.git` paths
are rejected. A proposal without a target retains its rating but displays an
application warning rather than pretending to have been applied.

The Home image includes Git. `git merge-file --stdout --diff3` runs on temporary
snapshots outside the repository, without changing the Git index or history.
Only a clean merge is eligible for application. Git errors or timeouts block
application, and the merged applied result is stored separately from the original
review evidence.

Home's writable workspace is supplied by `POLISH_WORKSPACE`. File replacements
are atomic and preserve executable permissions. This currently supports single
text-file proposals; multi-file changes and image-file application need their
own target adapters. Filesystem writes and SQLite commits are separate: retries
recognize an already-matching result after an interrupted request. The final
pre-write check detects edits during merge preparation, but does not lock out
external editors; concurrent external writes remain a limitation.
Applying a source file does not deploy it or restart affected services. The
repository's running-service verification requirements still apply to deployment.

## Review queue and goals

The default list contains ungraded entries. A successfully saved grade fades the
card out and collapses its space; failed saves leave it available to retry.
Graded entries remain in `?view=graded` for inspection and re-rating. Conflicted
or blocked positive entries leave persistent warnings with links to their graded
record. The count and empty state follow the shrinking queue. Reduced-motion
preferences skip the animation.

Show Goals toggles the accepted goal catalogue at the top of the page, including
each goal's completion evidence. Positive goal ratings refresh that catalogue
without a reload. Proposed goals remain review entries until accepted.

## Automatic goal selection

The model selector and **Rub one out** button sit directly above Model scores.
Each click reserves one search for the available accepted goal with the lowest
sum of current rating points on entries linked to that goal, across all models.
Unrated goals score zero; Bad/Trash can make scores negative. Tied minima use a
random choice. Queued/running goals are excluded to avoid duplicating work.
Selection and queue reservation happen in one serialized database transaction.

The job records its chosen goal, score at selection, and tie count, so a transport
retry returns the same job rather than choosing again. The UI shows selection,
progress, and a link to the new entry, and restores that job after a page reload.
This triggers the same one-small-polish prompt and review process as a manual
per-goal search. Merely loading the page discovers models and progress; it does
not start generation.

**Six pack** beside it selects six distinct goals with no queued/running search
or correction, then queues one independent job per goal using the selected
model and a shared prompt-profile snapshot. The batch and each job are durable;
the page reports progress and links each generated entry as it becomes
available. It requires six eligible goals and uses an idempotency key so a
network retry cannot queue another batch.

## Seeking improvements for a goal

Each accepted goal has a model selector and Seek improvements action. Model
refresh starts the dedicated `polish_worker` and discovers installed Ollama
models and, when configured, candidate OpenAI text-generation models. OpenAI
model listing does not guarantee that every listed model supports the requested
Responses/structured-output features; unsupported requests fail visibly. The
OpenAI adapter uses the [Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create).
The local adapter uses [Ollama chat](https://docs.ollama.com/api/chat).

Home saves the selected provider/model/version, goal snapshot, and prompt
revision (`goal-improvement-v4`) in SQLite before dispatch. Only one search for
a goal may be queued or running at a time; transport retries use a stable request
key. A single worker processes searches. Progress and completed-entry links are
restored on page reload. A failed dispatch is marked failed, not left submitting.
A worker restart fails interrupted generations without automatically charging or
loading a model again; an already-persisted result is recovered without replay.

The worker selects up to four relevant source files using goal terms and file
paths, with bounded excerpts (up to roughly 32,000 source characters total).
It includes a compact accepted-goal catalogue and recent pending suggestions
for that goal to discourage duplicate work. Source snapshots and the exact
prompt are retained with the job. Credential files, runtime data, dependencies,
large files, and symlinks are excluded from source context. This is a bounded
initial exploration strategy, not unrestricted repository browsing or a shell
agent. Models cannot execute commands or apply their suggestions.

The goal is a direction, not an assignment to solve it completely. Each search
asks for one small, low-risk, non-controversial polish with minimal scope. G057
receives catalogue titles and completion evidence instead of implementation code,
and first seeks one wording simplification that reduces context without losing
intent.

A valid response creates exactly one unrated Polish entry: either an exact,
unique text replacement in an inspected file, or one goal addition/update.
Its application target retains the original baseline. If a copied search span
fails solely because of duplicated opening whitespace also present in the
replacement, that padding can be removed when the remaining literal span matches
exactly once. This does not normalize internal whitespace or guess tokens or
ambiguous matches. The raw model response and the normalization note remain
saved with the job, and the resulting real before/after diff is reviewed normally. Ollama entries include
an installed-model digest; a changed/removed tag is rejected before generation.
OpenAI entries use the returned model identity. The selected identity, provider
usage metadata, and prompt revision remain traceable through the originating job.
No fallback model, automatic model download, automatic grading, or application
occurs. Proposal syntax, lint, test, and response-format failures receive one
bounded same-model correction with failure context. Provider/infrastructure
failures and pre-existing failing baselines stop without spending a correction.
A second failed proposal stops without inventing an improvement. Image/UI
comparison generation, goal deletion/consolidation targets, multi-file edits,
and deeper model-directed source exploration are future extensions.

The worker uses host networking for local Ollama and a read-only source mount.
The review/apply service retains its existing writable workspace. Configure
`OLLAMA_BASE_URL` and `OPENAI_API_KEY` on `polish_worker`; credentials are never
stored with job evidence or sent to the browser. The local request unloads its
model after generation (`keep_alive=0`). No generation is initiated on page load.

## Code validation and bounded correction

A code proposal must pass syntax checking, applicable linting, and the relevant
regression suite in a disposable checkout before it enters review. Test selection
uses direct imports/references, preferring a matching focused unit suite. Python
uses the application image's interpreter and Ruff E9/F821/F822/F823; JavaScript
uses Node syntax checks and the matching Node tests. Templates, CSS, and data
formats use their parsers. Code without a relevant suite is blocked until coverage
is available. Text-only changes do not claim executable-code test coverage.

The validator runs the suite on the baseline, applies the exact proposed diff in
a separate checkout, then runs it on the candidate. Outputs and selected commands
are retained with the entry. Candidate failures are returned to the same model
for one correction attempt, including the original proposal and bounded failure
output. At most two model calls are allowed per originating job. Attempts retain
raw responses, diagnostics, prompts, and full model-prompt profiles. Corrections
must stay on the same file task. A corrected entry is ungraded and requires review.

Positive ratings revalidate the current three-way merged result before writing
live source. Expensive checks run outside the SQLite write transaction. A changed
baseline during validation leaves the file untouched. Test receipts are bound to
the exact path, baseline, and candidate; an old passing report cannot authorize a
different result. Syntax, lint, test, coverage, and infrastructure failures block
application. Failed application can queue the remaining same-model attempt;
Bad/Trash skip corrections, and queued corrections are cancelled if rejected.
Merge conflicts never trigger model conflict resolution.

`bookadaptor-polish-validation:latest` derives from the local application image
plus Node. Use `BUILDX_BUILDER=default docker compose build polish_validator` when
building against that local base. Each container uses the standard runc runtime,
no network/GPU/host data mounts, dropped capabilities, no-new-privileges, CPU and
memory limits, and a disposable source snapshot. It receives no provider keys or
Docker socket. The parent services use Docker to create and remove these containers.

## Per-model prompt profiles

`home/polish/prompts/*.md` are ordinary reviewable prompting artifacts. Their YAML
frontmatter selects provider/model identity, structured-output mode (`schema`,
`json`, or `none`), instruction delivery (`system` or `user`), Ollama's thinking
toggle, and Python indentation convention (`absolute` or `relative`). Identity and
supported settings are validated before a profile can be applied. The Markdown
body contains that model's instructions. Defaults are created when a model is
first selected; six installed model profiles are included in the source tree.

Jobs snapshot the profile when dispatched; each generation attempt stores the
exact instructions/settings and content hash. Entry provenance shows the used
profile revision. Refining a profile changes future searches and leaves historical
evidence intact. G018 prioritizes the selected model's profile as an editable
source and includes bounded recent failures for that same provider/model.

Qwen3 profiles disable thinking output and request structured JSON with dedented
Python replacement fragments. The edit renderer can restore indentation only at
one exact whole-line statement match, preserving relative block indentation.
Multiline string/f-string contents are never reindented. Legacy fragments can be
recovered by this rule when they otherwise fail syntax, but must still pass the
full lint/test gate. Raw output and normalization notes remain available, and
normalization does not spend another model call or silently apply the result.

## Current implementation

Home serves `/polish`. It renders text diffs, provides a sample layout preview,
and persists one changeable reaction per entry. Entries now retain and display
a model label and goal reference; G001 is the established initial goal.
The initial 55 proposed goal additions are staged as individually rateable entries,
with their descriptions and completion evidence. Retrying that import does not
duplicate entries or reset ratings. Legacy entries retain their original evidence
and ratings and are labeled unattributed/unassigned.

The internal submission helper supports attributed text proposals, immediate
goal and single text-file application, conflict warnings, and model score totals.
Per-goal model selection, durable search jobs, model generation, and goal review
are implemented. General task ingestion, manual goal editing, syntax highlighting,
image comparisons, and deployment remain to be implemented.
