# Home

Independent Flask dashboard. It does not import artifact_editor. Service-owned
panels are loaded into Home; Redis's `home_panel` class and its templates live
in `src/redis/home_panel.py` and `src/redis/templates/redis/`. Home instantiates the
panel in its application factory. Compose supplies `src/redis` as a separate
build context and mounts it for development.

Redis appears as a top-level panel containing the Redis Insight launcher.
Its status uses a bounded Redis PING on page load and every 60 seconds. Looking
at the panel never starts Redis; choosing Redis Insight starts its dependencies.
The nested Insight controls also poll at 60-second intervals.

PostgreSQL has the same service-owned panel in `src/postgres`, with PostgreSQL
and a nested **Open pgAdmin 4** launcher. Both are on-demand Compose services;
opening pgAdmin starts PostgreSQL and waits for its health check. PostgreSQL
data and pgAdmin settings use the `postgres_data` and `pgadmin_data` named
volumes. The Home build includes both panel directories as additional contexts.

Local development connections:

| Service | Address | Account |
| --- | --- | --- |
| PostgreSQL | `localhost:5432`, database `bookadaptor` | `bookadaptor` / `bookadaptor` |
| pgAdmin 4 | `http://localhost:5050` | `admin@bookadaptor.dev` / `bookadaptor` |

These development services publish their ports on localhost. Containers on the
Compose network connect to `postgres:5432`. pgAdmin imports the BookAdaptor
server definition from `src/postgres/servers.json`; use the database password
above when connecting. Configure credentials in Compose before initializing a
new database if different local credentials are desired. Changing initialization
environment values later does not change existing database or pgAdmin accounts.

PostgreSQL 18 mounts its persistent volume at `/var/lib/postgresql`, following
the [official image's data layout](https://hub.docker.com/_/postgres).
pgAdmin uses its documented [container configuration and server import](https://www.pgadmin.org/docs/pgadmin4/latest/container_deployment.html).

Home defaults to a plain, selectable service table. List columns include the
service name, Compose service, version, state, image, ports, and status. Open and
Info are explicit actions; clicking or dragging across cells selects text.
The table uses locally served DataTables and ColReorder for sortable headers,
global search, individual column filters, pagination, and draggable columns.
Layout, search, and sorting are remembered for the browser session. Reset table
restores the default column order and clears searches. Copy filtered rows exports
TSV to the clipboard; Download CSV exports all matching rows in their current
sort and column order, across all pages. Actions are excluded from exports.
Version and Info refreshes pause while text is selected. Info opens beneath its
row and loads current Compose data on demand.

The List / Array / Icons controls switch between the table, a three-column array
of brass pushbuttons, and the detailed card grid. Array wraps to two columns below
1100px and one below 700px. Array buttons stay stationary and flash with a brass glow on activation;
clicking a panel's header reveals detailed contents spanning three base grid rows.
Array uses fixed 8rem base rows and dense packing, so other entries fill the
space beside an expanded entry instead of leaving a tall, empty row. Each
collapsed entry occupies one row; the grid accounts for spacing between rows.
Panel order, grid positions, sizes, expanded state, and the selected view are
saved in browser localStorage and restored across reloads, new tabs, and browser sessions.
The last-used List, Array, or Icons mode is saved in localStorage as the
default for future visits and new tabs. First visits default to List.
Drag a panel's header to position it, expanded or collapsed. The slot under the
cursor becomes its upper-left corner; the preview snaps there and the other
panels pack around it. Wide panels require enough columns to their right.
A click still toggles its details. Moving preserves its size and selected contents; Escape cancels a
drag. Alt plus arrow keys on a focused header also reorders panels.
Drag any of a panel's four corners to resize both axes, its left or right edge
to resize width, or its top or bottom edge to resize height. These regions are
invisible and show the corresponding resize cursor. A preview shows the
size in grid slots; releasing applies it and lets the array repack. Width is
limited to the visible column count, while height can grow by whole rows. Use
arrow keys on a focused grip for slot-by-slot resizing, or Escape to cancel a
drag. The panel's Info contents continue to scroll within its resized bounds. Only one button expands at a time; clicking its header or pressing Escape closes it.

Open tabs show each panel's Compose members as vertical rows. Each row has its
round image action button on the left and that service's UI on the right; all
rows remain visible. Running services show a full-color Stop button; other states
show a monochrome Start button. Starting includes declared Compose dependencies;
stopping affects only that service. Status refreshes every 60 seconds and after
an action. Redis owns its member metadata and UI templates in `src/redis`.
Open entries are explicitly grouped UI services, such as Redis and Redis Insight.
Dependencies remain visible in Info and start through Compose, without receiving
extra Open rows in every panel that uses them.

Editor workflow links open ComfyUI directly when it is ready. Otherwise they use
Home's `/programs/comfyui` launcher, which starts ComfyUI and waits before opening
the requested workflow. `HOME_UI_URL` can override the editor's default Home
address (the current browser host on port 8080).

Every panel has Open and Info tabs. Info queries `docker compose ps --all` and
`docker compose stats --no-stream` for its declared services. It shows container
state, health, image, command, ports, resource usage, and raw Compose data, including
stopped containers and services that have never been created. Snapshots load when
Info opens, refresh every 60 seconds while visible, and can be refreshed manually.
Inspecting Info never starts a service. Failed refreshes preserve the previous
snapshot and its timestamp.

Home declares panel/service mappings in `home/service_info.py`; service-owned
panels supply their own `services` tuple (Redis declares Redis and Redis Insight).
The shared `service_panel` Jinja macro supplies the tabs and Info view.

From the repository root:

```sh
docker compose up -d --build home
```

Open http://localhost:8080/. Home is the only service enabled by default. Other
services use the `on-demand` profile (the test runner uses `test`). Explicitly
requesting a service starts it and its declared dependencies.

Polish is served directly by Home at `/polish` and appears in List, Array, and
Icons views. It displays a vertical list of described, side-by-side text diffs
with one changeable reaction per entry: Trash, Bad, Good, or Awesome.
Diffs show four lines of unchanged context around each change. The “…” controls
expand or collapse omitted prefix, middle, and suffix sections in place.
Saved grades fade out and collapse from the default ungraded list. Show graded
opens rating history for review or re-rating; application warnings remain visible
above the queue. Show Goals toggles the accepted catalogue at the top and updates
immediately when a goal proposal is accepted. The empty
list links to `/polish?preview=1`, which shows sample entries with temporary
reactions without inserting data. Real entries and reactions persist in
`polish.sqlite3` alongside Home's TODO data (`TODO_DIR`); `POLISH_DB` in the app
configuration can override the database path. Each new internal text proposal
records a model label and an established goal. The initial goal catalogue is
staged as proposals attributed to GPT 6.1 Sol Medium, all advancing G001;
`docs/polish.md` describes their scope. General entry ingestion is not exposed yet. The installed Codex `$polish` skill opens a new browser tab at
this endpoint. Good earns 1 point and Awesome earns 5; both immediately apply compatible
goal or text-file targets. Git three-way merging preserves non-overlapping edits; overlapping edits
produce warnings without overwriting targets or writing conflict markers.
Bad earns −0.5 points and Trash −2.5; both skip application/conflict work. Home mounts the
checkout at `/polish-workspace` for accepted text-file changes. Source application
does not restart other services. Changing a rating updates points without undoing
an already applied change. Polish's local CSS and JavaScript use versioned URLs; bump their
`v` parameters in `polish.html` when changing assets.

**Rub one out**, above Model scores, uses the selected model for one small
polish toward the lowest-scoring available goal. A goal's score is the sum of
current rating points on suggestions attributed to it, across all models; goals
without ratings score zero. Ties are chosen randomly. Goals with queued or
running searches are excluded. The chosen goal/score and job status are shown,
and retries preserve the original selection.

**Six pack** beside it reserves six distinct available goals and queues one
small improvement for each with the same selected model and prompt snapshot.
Progress is saved per job and as one batch, so each completed entry can be
reviewed independently after a reload. It is available when at least six goals
have no queued/running search or correction.

Each accepted goal has a model selector and **Seek improvements**. Opening Goals
starts the on-demand `polish_worker` for model discovery; it does not generate.
The worker uses host networking to reach a host-local Ollama at
`http://127.0.0.1:11434` (`OLLAMA_BASE_URL` overrides it). It reads the checkout
through a separate read-only mount and persists its queue/progress in the shared
Polish database. A selected model produces one attributed, ungraded entry;
**Review new entry** opens it. Jobs survive page reloads, and failed/interrupted
requests require explicit retries, except that proposal validation failures get
one bounded correction attempt with the same model and diagnostic context.

Code proposals are syntax/lint checked and run against the relevant tests in
isolated baseline and patched checkouts before entering review. Positive ratings
recheck the actual Git-merged result before any live source replacement. The
validation image uses the existing application dependencies plus Node; build it
with Docker's local builder (the base application image is local):

```sh
BUILDX_BUILDER=default docker compose build polish_validator
```

Checks run in disposable containers with no network, GPUs, Docker socket,
provider credentials, or writable host mounts. The same relevant tests run before
and after the diff. Missing coverage, failed baselines, or unavailable validation
infrastructure block application. Candidate syntax/lint/test failures get at most
one additional generation using the same model and the failure details. A
corrected proposal requires its own review; a second failure stops. Bad/Trash
entries do not start correction work, and Git conflicts never invoke the model.

Each model has an editable Markdown prompt profile in `home/polish/prompts/`.
YAML frontmatter controls structured output, system/user instruction delivery,
Ollama thinking, and Python fragment indentation. Qwen3 profiles request no
thinking and use relative-indentation fragments. Jobs/attempts retain the exact
profile and revision hash. G018 searches expose the selected profile and recent
validation failures so its prompting method can be improved through Polish.
Profile frontmatter is validated before application; old jobs retain their
original profile snapshots. No service restart is needed for prompt-file edits
because each new search loads and snapshots the current profile.

To enable OpenAI models, set `OPENAI_API_KEY` in the environment used by Compose,
then recreate only the worker:

```sh
docker compose up -d --no-build --no-deps --force-recreate polish_worker
```

Keys remain in worker environment configuration and are never returned to the
browser or stored with jobs. Refresh models after updating provider configuration.
The worker uses the Home image; build `home` first on a fresh installation.

Choosing Library opens a loader, starts `artifactserver` and its Redis dependency,
waits for `/library/` to respond, then opens http://localhost:8082/library/.
Choosing Styler starts `artifactserver`, Redis, and `websocket`, waits for `/styles/`
to respond, then opens http://localhost:8082/styles/.
Glances and Redis Insight use the same loader; Redis Insight also starts Redis.
The Stop control stops only the selected service, preserving shared dependencies.
Existing Home breadcrumbs in the editor return to Home on port 8080.

Launches use Docker Compose with the running Home container's project name and
host working directory. Compose applies the service's mounts, command, ports,
and dependencies. Images must already exist; launches do not build images.
To prepare the editor image explicitly:

```sh
docker compose build artifactserver
```

To add another launcher, add its metadata to `PROGRAMS` in `programs.py` and
point its Home card at `/programs/<service>`. Specify `internal_port` when the
container port differs from the published browser port, and `path` when its
landing page is not `/`. Configure dependencies in `docker-compose.yaml`.
ComfyUI, WebSocket, and video worker launchers can follow this same pattern.

Home mounts the Docker socket and Compose configuration for program controls
and the test runner. Test runs use the existing `bookadaptor-app:latest` image.
TODO data persists in `src/todo/home.json`.

The video worker delegates GPU provider handoffs to Home through
`POST /api/gpu/provider/switch`. Before a sibling handoff, the worker asks the
current provider to unload its model and verifies at least 8 GiB of free VRAM.
Home keeps both provider containers running during a normal handoff and starts
the requested `comfyui` or `llama` service. If model unloading fails, the
worker requests a sibling container stop as a recovery. Training and Python
switches still stop both model services. `GET /api/gpu/provider` reports which
provider services are running.

Nginx remains an optional shared entry point on port 80. Starting it currently
starts its declared editor, ComfyUI, and WebSocket dependencies as well.

For local development, install requirements.txt and run from the repository root:

```sh
TODO_DIR=./src/todo flask --app home.app:create_app run --port 8080
```


The DataTables integration check uses jsdom with the actual vendored libraries:

```sh
npm install --prefix /tmp/bookadaptor-datatable-tests --cache /tmp/bookadaptor-npm-cache jsdom
NODE_PATH=/tmp/bookadaptor-datatable-tests/node_modules node tests/test_service_table_js.cjs
```
