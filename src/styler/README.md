# Styler

Styler's blueprint, style library, widgets, templates, and bundled style data
live in this package. The editor registers `styler.views.bp` at `/styles/` on
port 8082. Home links to that port from List, Array, and Icons views.

Other editor modules import `styler.styles` and `styler.htmx` for shared style
selection. Sample generation uses the editor's chapter/image helpers and
ComfyUI; Redis stores style selections and ranking data.

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
