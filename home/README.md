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
`POST /api/gpu/provider/switch`. Home stops the other provider and starts the
requested `comfyui` or `llama` Compose service; `GET /api/gpu/provider` reports
which provider services are running.

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
