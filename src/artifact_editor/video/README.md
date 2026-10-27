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

Frame composition, MP4 encoding, and LaTeX typesetting go to `cpu_jobs`. The `cpu_worker` service
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
