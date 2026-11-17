# Chapter video execution

Generate Video submits a chapter render to `chapter_render_jobs`. The
`render_worker` coordinates asset preparation and waits for its dependencies.
It never holds GPU ownership while waiting for other workers.

Generate Video accepts still images and reuses animations that already exist.
Missing animations do not trigger animation prompts or image-to-video workflows.
Animation generation is an explicit action; regenerating one selected image
leaves the other stills alone. TODO: optional zoom/pan effects for still images.

GPU workflows, including LLM metadata requests, go through the existing serial
GPU worker and appear individually in **GPU Queue**. The underlying
`video_render_jobs` Redis name remains for compatibility with existing producers.

Text segment preparation, frame composition, MP4 encoding, and LaTeX typesetting go to `cpu_jobs`.
Video rendering first submits one task per required phrase and 2,048-pixel text
segment, deduplicated across the complete frame timeline. These tasks run in
parallel; the coordinator waits for all of them before submitting any frames.
Frames only read prepared segments and fail if one is missing. They never
regenerate missing segments, including during a forced redraw. Segment writes
retain a six-hour Redis TTL. The `cpu_worker` service
starts one worker process per CPU core available to the container. Claimed tasks
are retained in per-worker Redis lists; the supervisor recovers claims after a
process or service restart. **CPU Queue** shows active workers, pending tasks,
render progress, and failures. Home starts all three worker services together.
Typeset LaTeX starts only the CPU workers and queue feed, returns a queued task,
and refreshes PDF/image previews on completion. Chapter import also delegates
compilation and rasterization to CPU workers, waiting before its audio phase.

The video timeline measures every phrase WAV in XML order, includes silence,
and positions images at their XML timeline offsets. Missing narration or frames
fail the render. Encoding uses an exact frame count and checks the resulting
video and audio streams before atomically replacing the previous MP4.

Task progress uses estimated time remaining instead of provider percentages.
`task_timing` retains successful per-work-unit count, sum, minimum, maximum, and
128 recent durations in Redis under `task-timing:v1`, independently of expiring
queue history. Profiles distinguish execution type, model/workflow settings,
resolution, and cached work; prompts, job IDs, and output paths are excluded.
Queue estimates account for preceding GPU tasks and parallel CPU worker slots.
The UI shows min/avg/max in tooltips (also directly on CPU rows). A new profile
shows “Learning duration”; work exceeding all observed durations shows “Longer
than observed”. Completion is only shown after a terminal worker result.

Source illustrations are prepared by both chapter import and One Shot, including
imports whose text fingerprint is unchanged. Uniform paper margins may be trimmed
into a square; a paper extension is allowed only when it occupies at most 10% of
the square. Those stills remain eligible for AI animation. Other source aspects
use editable camera motion along the longer dimension and skip AI animation.
The book's opening-illustration option runs first to separate its decorative
heading from body text. Existing manual motion is preserved. **Auto Pan** in
**Choose from Source** applies the same square-or-pan decision to the selected
source image. Originals are retained, and animations predating a new crop are
excluded from reuse.
