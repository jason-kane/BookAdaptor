# Styler Qwen Image sampler

The third Styler engine is `qwen`, using the installed Qwen Image 2.1 model,
Qwen3-VL 8B encoder, and Qwen Image 2.1 VAE. Samples have separate `_qwen.png`
filenames, so existing Flux samples cannot be mistaken for Qwen results.

The fragment was decomposed from the official workflow:
https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_qwen_image_2_1_t2i.json

It keeps the official 25-step Euler/simple sampler, CFG 1, and Qwen 2.1 model
cache. The style prompt goes directly to the encoder, without the optional LLM
prompt rewriting branch. The application supplies PROMPT, WIDTH, HEIGHT, and
FILENAME_PREFIX through primitive nodes, as with the other Styler samplers.

Edit the fragment and blueprint sources, then rebuild the UI template:

```sh
curl --fail http://localhost:8188/object_info -o /tmp/comfy-object-info.json
python scripts/build_styler_qwen.py --object-info /tmp/comfy-object-info.json
```

The build composes the fragment with `comfy workflow compose` and converts the
result to UI format. Do not edit the generated template directly.

## Image tagging

`sample-image-tags` uses the existing Qwen 3.5 4B vision-capable LLM with
`TextGenerate.image` connected to the actual sample image. It returns up to
eight short style tags as JSON. Rebuild its API template with:

```sh
python scripts/build_styler_image_tags.py
```

The render worker finishes a category’s image batches, then analyzes that
category’s samples before generating images for the next category. Interactive
render jobs retain priority. Existing samples are analyzed when
requested; content hashes invalidate evidence after regeneration.

Image analyses are evidence for per-prompt tags. Keep unanimous tags first,
then top up to 4–5 tags using candidates supported by at least two engines.
Choose among equally supported candidates randomly but consistently for the same
image evidence. If fewer than four tags have two-engine support, use only those
available. Missing, stale, failed, or unfinished analyses prevent promotion;
an explicit empty array is an abstention. One engine alone cannot promote tags.
Analysis cache versions invalidate older image evidence when the pipeline changes.

## Matching equivalent tags

`src/tag_matching.py` passes the item's tags and the full tag vocabulary to the
existing local Qwen text workflow. The vocabulary includes style memberships,
per-image tag names, style sample sidecars, and Stockpile sidecars. Style tag
spellings and image usage counts help choose an established name for equivalent
tags. Distinct concepts should remain separate; an unmatched tag stays as-is.
Responses must provide one choice per input and cannot invent vocabulary names.

Both sample analysis (including Retag) and Stockpile jobs use this step. Style
consensus consumes the matched image tags under the same engine agreement rules.
Sidecars save canonical `tags`, original `raw_tags`, `tag_matches`, and matching
version/hash fields. Scan Stockpile also queues complete descriptions whose tags
have not been matched yet, without repeating the vision description workflow.

Consensus updates style tag memberships, category counts, and the UI. When
agreement disappears, automatically managed memberships are withdrawn; manual
memberships and explicitly removed tags are respected. Individual evidence is
stored in Redis and `.tags.json` sidecars beside the sample PNGs.

## Background coverage

Choosing a category starts a resumable sweep: finish that category's missing
samples, then walk the other registered categories, grouping work by engine.
Interactive render jobs take priority. Existing files and duplicate style/engine
pairs shared across categories are skipped; failed pairs are tried at most once
per sweep. Opening the main Styles page clears the sweep along with queued
samples; selecting a category starts a fresh sweep. After each category’s images, the worker processes its missing image-tag
analyses before advancing to the next category.
