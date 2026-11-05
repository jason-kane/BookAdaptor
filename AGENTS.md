# Completing changes in the running application

Code changes are not complete until the affected running services have loaded
them. Bind-mounted Python files do not refresh imports in long-lived processes.

- After changing WebSocket actions, handlers, progress messages, or worker RPC
  endpoints in `src/websocket/server.py`, restart the `websocket` service.
- After changing render coordination, chapter asset jobs, or their imported
  implementation, restart `render_worker`.
- After changing GPU/LLM/audio worker jobs or their imported implementation,
  restart `video_worker`. For CPU task changes, restart `cpu_worker`.
- Check `docker-compose.yaml` for other affected long-lived services, such as
  `home` and `lora_trainer`. The web app's `artifactserver` uses Gunicorn reload;
  verify it actually reloaded rather than assuming it did.
- For Compose configuration or environment changes, recreate the affected
  containers; a restart does not apply updated container configuration. Rebuild
  images when dependencies or image contents change.
- For JavaScript/CSS changes, verify browser cache/version handling and reload
  the page so the browser uses the changed assets.

Use `docker compose ps` and recent logs to identify affected running services
and active work before restarting. Avoid interrupting unrelated jobs. Restart
only affected services, for example:

```sh
docker compose restart websocket render_worker
```

Wait for restart completion and verify readiness in service status/logs. Check
that the changed action is acknowledged and reaches its intended queue/worker;
passing unit tests alone does not establish that the live app loaded the change.
Do not launch expensive generation merely to test acknowledgment. If runtime
verification is unavailable, say explicitly what remains unverified.

If a timeline job stays at “Submitting…”, first check whether the WebSocket
service recognizes the action and whether a persisted job/queue entry exists.
Do not assume this means the model is processing slowly. This previously
happened when Assign Characters was added without restarting `websocket`.
