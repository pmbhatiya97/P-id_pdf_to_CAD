# LineaForge Studio web setup

LineaForge Studio uses a React frontend and Python/FastAPI backend. The Windows launcher serves both at one address, normally **http://127.0.0.1:8765**. The same application can run in a container, with a separate Windows MCP bridge for licensed CAD.

This release implements the conversion, configurable AI orchestration, review, and CAD handoff workflows. Real recognition accuracy and native AutoCAD/SOLIDWORKS execution still require acceptance testing with your models, drawings, software versions, and licenses. Passing software tests or a connection probe does not establish engineering correctness.

## Run on Windows

### Portable package

1. Extract the complete `LineaForge-Web-Windows.zip` archive to a normal folder.
2. Open its `LineaForgeWeb` folder and double-click **Start-LineaForge.cmd**. `Run-LineaForge.cmd` is an equivalent shortcut.
3. The launcher starts the backend and opens the React app in your browser. Keep the server window open while working. Close it or press Ctrl+C to stop.

Keep `LineaForgeWeb.exe`, its `_internal` directory, the frontend assets, and `LineaForgeCadBridge.exe` together. The portable app includes its Python runtime and compiled frontend; it does not require a separate Node.js installation. Native CAD still requires the licensed desktop application on the CAD host.

Clicking the launcher again opens an existing healthy local LineaForge instance. Jobs, settings, and uploaded files are kept in the data directory rather than inside the extracted app folder.

### Run from source

Install Python **3.12 or 3.13** and Node.js **24**, then double-click **Start-LineaForge.cmd** in the source folder. The source launcher prepares a Python environment, installs `requirements-web.txt`, builds React when its compiled assets are missing, and starts the combined server. The first run needs internet access for these dependencies.

For manual startup:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-web.txt
Set-Location web
npm.cmd ci
npm.cmd run build
Set-Location ..
.\.venv\Scripts\python.exe -m lineaforge.web_launcher --open
```

The default data directory is `%USERPROFILE%\.lineaforge\web`. To choose another folder or port, run:

```powershell
$env:LINEAFORGE_DATA_DIR = 'D:\LineaForgeData'
.\.venv\Scripts\python.exe -m lineaforge.web_launcher --port 8766 --open
```

## Configure your VLM and LLM

Open **Connections → Models** in the app. Set the API base URL, exact model identifier, optional API key, and local/cloud mode separately for the vision-language model and language model.

- The **VLM must accept image inputs** through an OpenAI-compatible `/chat/completions` endpoint and return JSON matching the supplied schemas. It reads the sheet overview and detailed source tiles.
- The **LLM** reviews structured graph evidence, topology, tags, lines, and the CAD build plan. If its model identifier is blank, those roles use the configured VLM endpoint.
- Local mode requires a literal loopback URL such as `http://127.0.0.1:8000/v1` or `http://127.0.0.1:11434/v1`. A hostname or another machine's IP requires cloud mode and per-job consent. In a container, loopback refers to the container itself.
- Cloud mode requires explicit approval each time a drawing job will transmit source images or extracted content. Retrying a job does not reuse earlier cloud consent; start a new approved job when needed.

Save your settings, then use **Test connection**. The test checks `/models` and whether the configured model is listed. It sends no drawing and does not verify vision quality, structured output reliability, or the model's recognition accuracy. Run a small representative drawing next and inspect its evidence and graph.

API keys remain in the backend's private `config.json` file. API responses report whether a key exists without returning it. A blank key field preserves the stored key; **Remove stored key** explicitly clears it. Settings are stored locally rather than encrypted by an external secret manager, so protect and back up the data directory appropriately.

## Connect AutoCAD or SOLIDWORKS MCP

Open **Connections → CAD**. For local Windows operation:

1. Install, activate, and open the chosen CAD application once under the Windows account running LineaForge. Complete its license and first-run dialogs.
2. Choose **AutoCAD** or **SOLIDWORKS**, with **Local process / stdio** transport.
3. Leave the executable and arguments blank to use the bundled bridge. The portable app uses its sibling `LineaForgeCadBridge.exe`; source startup uses its Python environment and `lineaforge.mcp_bridge`.
4. For SOLIDWORKS, configure a blank, single-sheet `.drwdot` drawing template through the template setting or `LINEAFORGE_SW_DRAWING_TEMPLATE` on the CAD host.
5. Save, test discovery, then build and inspect a small reviewed drawing in the licensed application.

For a custom source bridge, the executable is your Python executable and the arguments are separate values, for example:

```text
-m
lineaforge.mcp_bridge
--backend
autocad
--output-root
C:\LineaForge\cad-exports
```

For SOLIDWORKS, use `solidworks` as the backend and add `--template` followed by the template path. If using a fixed custom output root, set the app's **Remote output root** to that same absolute directory. Blank bundled bridge settings automatically use the current job's output directory.

A connection test initializes MCP and discovers required tools. It does not start a drawing or prove that the native CAD application can export correctly. Models produce the graph; one deterministic builder translates the reviewed graph into typed CAD tool calls. Agents do not issue competing changes to an active CAD document.

See [the full CAD and remote bridge guide](web_cad_setup.md) for HTTP setup, authentication, the MCP tool contract, format differences, and native acceptance tests. AutoCAD produces DWG/DXF with attributed blocks. SOLIDWORKS produces a 2D drawing and exports DWG/DXF; it does not create an Electrical project, piping assembly, or 3D model. Its native `.slddrw` and provenance sidecar stay on the CAD host when using remote artifact retrieval.

The offline **Editable DXF** backend needs no desktop CAD installation. DWG conversion outside the native MCP workflow requires a separately configured ODA File Converter or supported LibreDWG utility.

## Use the workbench

### Drawing conversion and PDF tools

Upload PDF, DXF, or DWG files, up to **250 MB per file**. The uploaded source remains unchanged. PDF preview is rendered locally with a maximum dimension of 2000 pixels; detailed initial inspection is limited to the first 100 pages of unusually long documents.

Use **Converter** for PDF/DXF/DWG routes. Vector PDFs become editable paths, hatches, and text where semantic source text exists. Outlined lettering remains geometry. Raster contour tracing is an approximation and does not perform semantic P&ID recognition. DWG routes require the external converter. Use **PDF Tools** to merge, split, losslessly recompress, reorder, or rotate pages while retaining vectors.

### P&ID analysis, review, and CAD

1. Configure a real image-capable model, select a PDF and page, then start analysis in **P&ID Studio**. Each AI job processes one selected page using a whole-sheet overview followed by detailed overlapping tiles. Cross-sheet references are evidence for review; separate pages are not automatically merged into a verified plant model.
2. Watch agent events and progress. Failed or cancelled jobs retain downloadable partial evidence when available. Cancellation is cooperative: an in-flight model request or CAD operation may finish before stopping.
3. Inspect each element against the source. Every element must be explicitly **accepted or rejected**, and every finding must be resolved before CAD generation. Low confidence is an invitation to inspect evidence. An `UNREADABLE` element requires verified edits or rejection; changing its status alone cannot clear the gate.
4. Build CAD with the chosen backend. Fidelity mode preserves extracted positions; clean mode rounds coordinates and needs inspection. The current builder creates a new drawing.
5. Inspect the generated file and QA reports. DXF is reopened and rendered for structural traceability checks. When a VLM and source tiles are available, independent tile comparisons add visual findings. Native DWG requires a reviewed plot or DXF export for equivalent visual comparison. CAD output remains **needs review**; structural entity counts alone do not establish visual or engineering fidelity.

Use the artifact download buttons for the graph, tag index, evidence logs, review report, generated files, and QA reports. The graph JSON editor/API can provide larger geometry and connectivity corrections than the individual element form. Schema validation and release gates still apply to edits.

### What the 28 roles mean

The agent catalog contains **28 configurable roles** covering orchestration, layout, legends, noise preservation, equipment, valves, instruments, inline items, OCR, line numbers, notes, piping, signals, connectivity, off-page references, graph normalization, engineering reviewers, verification, repair proposals, CAD planning/build/QA, reporting, and release review.

These are specialist tasks coordinated around shared evidence, not 28 separate model installations. Many roles can use one VLM and one LLM. Deterministic roles enforce budgets, schemas, graph construction, CAD calls, and release gates in Python. Required roles cannot be disabled. Vision roles require the VLM; text reviewers can use the LLM or VLM fallback. The optional repair role proposes evidence-based edits for human review rather than silently changing process data.

All roles except optional repair are enabled initially. Defaults allow four parallel specialist calls, up to 1500 model calls, 7200 seconds, and an estimated cost ceiling of 30 currency units at 0.02 per call. These are execution estimates, not provider billing. Review limits before large drawings and adjust them in **Agent Studio**. More roles and calls can improve coverage but do not guarantee accuracy.

## Deployment

The Docker build compiles React and serves it from FastAPI, so browser requests use the same origin. Persistent files, configuration, job metadata, and artifacts live under `/data`.

Create a local `.env` file beside `compose.yaml` containing a random token of at least 24 characters:

```dotenv
LINEAFORGE_API_TOKEN=REPLACE_WITH_A_LONG_RANDOM_SECRET
```

Then run:

```powershell
docker compose up --build -d
```

The supplied Compose configuration publishes only `127.0.0.1:8765`. Open that address and enter the bearer token in the app. For internet or team access, place the service behind an authenticated HTTPS reverse proxy and choose the intended network exposure. API endpoints require the bearer token in deployment mode; a missing or short token stops startup. The token grants access to the entire workspace.

Relevant environment variables:

| Variable | Purpose |
|---|---|
| `LINEAFORGE_HOST` | Default `127.0.0.1`; non-loopback binding requires deployment authentication |
| `LINEAFORGE_PORT` | Default `8765` |
| `LINEAFORGE_DEPLOYMENT` | Set to `1` to require deployment mode explicitly |
| `LINEAFORGE_API_TOKEN` | Required deployment bearer token, at least 24 characters |
| `LINEAFORGE_DATA_DIR` | Persistent workspace; `/data` in Docker |
| `LINEAFORGE_FRONTEND_DIR` | Optional compiled React directory override |
| `LINEAFORGE_ALLOWED_ORIGINS` | Comma-separated explicit extra origins; wildcard CORS is refused |
| `LINEAFORGE_CAD_TOKEN` | Example environment secret for a remote Windows CAD bridge |
| `LINEAFORGE_SW_DRAWING_TEMPLATE` | SOLIDWORKS drawing template on the CAD host |

Keep the browser and API on the same origin for ordinary deployment; no extra CORS origin is necessary. The local development frontend may use `http://127.0.0.1:5173` or `http://localhost:5173`. Local mode rejects remote clients, non-loopback Host headers, and unrelated browser origins.

Run **one FastAPI worker per data directory**. This release has one shared workspace and one operator-level bearer token, with a bounded two-worker job pool and queue. It does not implement per-user accounts, tenant isolation, concurrent multi-process scheduling, or distributed job locking. Back up the persistent volume and stop the application before restoring it. After an unexpected restart, interrupted jobs are marked failed and can be restarted; completed evidence remains available.

A Linux/container deployment cannot directly automate the user's desktop CAD. Run the MCP bridge on a logged-in licensed **Windows CAD workstation**, then connect the backend to it through the authenticated HTTPS/VPN setup in [the CAD guide](web_cad_setup.md). Run one drawing job per native CAD bridge/application at a time.

## Verification scope

The test suite covers real PDF/DXF conversion, upload and download, configuration redaction, privacy guards, job persistence, review transactions, MCP contracts, and CAD evidence handling. An end-to-end HTTP test uses a local recorded OpenAI-compatible model server to validate routing through all 28 roles, human review, real DXF generation, and source/CAD comparison plumbing. Recorded responses are explicitly not measurements of recognition accuracy.

Before commercial engineering use, validate representative source drawings against reviewed annotations, measure symbol/tag/connectivity accuracy for the chosen models, and run native acceptance tests on the exact licensed CAD versions and templates. This release does not claim perfect recognition or automatic engineering approval.
