# Styler

Styler's blueprint, style library, widgets, templates, and bundled style data
live in this package. The editor registers `styler.views.bp` at `/styles/` on
port 8082. Home links to that port from List, Array, and Icons views.

Other editor modules import `styler.styles` and `styler.htmx` for shared style
selection. Sample generation uses the editor's chapter/image helpers and
ComfyUI; Redis stores style selections and ranking data.

## Shared GPU service

The `video_worker` and `artifactserver` use one Redis-locked GPU lease to
switch between the ComfyUI container and the direct llama.cpp server. ComfyUI
is used for media-generation workflows. Grouped image-tag tasks use the
Qwen2.5-VL GGUF server, submitting the available engine images concurrently
(up to three per style group) and keeping Llama loaded through tag matching.
The Compose Llama service has a separate `llama` profile so it is not started
alongside ComfyUI by the normal `on-demand` profile.

Every queued task records its `provider` (`llama`, `comfyui`, or `none` for
GPU-independent control work). A queued job is marked **Next** only when its
provider matches the active service. When the worker reaches a job for the
other provider, it puts that job back in its queue, runs the visible service
switch task, and takes the waiting job on a later worker pass.

The ComfyUI/Llama control in the Styler task-queue header queues a priority
service-switch task. A user-requested switch waits for any running task to
finish and overrides the automatic five-minute switch cooldown. Automatic
provider changes continue to observe the cooldown.

Before the first switch to Llama, create its stopped Compose container once
from the repository root:

```sh
docker compose --profile llama create llama
```

The application then starts and stops the prepared container through the
Docker socket already mounted in these services. The initial server settings
allow three parallel slots with 24,576 total context tokens; tune these against
GPU memory and observed throughput before increasing parallelism.

## Safely reload the worker

Use the **rotate** button in the Styler **Task queue** header to enqueue a
`worker_reset` task. This is the normal way to reload `video_worker` after
changing code that it imports; it avoids killing a running image or render
task.

The reset is placed in the priority queue. The current task finishes first,
then the worker exits at its safe restart point. Docker restarts the worker
container, which reloads the code and resumes the remaining Redis queues. The
queue status changes from **Reset queued** to **Restarting worker** and then
**Worker ready**. Existing queued tasks are preserved. A reset can wait behind
priority tasks that were already queued.

The reset only reloads `video_worker`; it does not restart ComfyUI or
`artifactserver`. It is a process reload, not a way to cancel or interrupt a
stuck active task. For a worker that is still busy, wait for that task to
finish; use the ComfyUI VRAM recovery path separately if the GPU service itself
needs recovery.
