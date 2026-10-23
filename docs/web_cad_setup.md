# Connect AutoCAD and SOLIDWORKS

Open **Connections** in the LineaForge web app. Configure **Vision model**, **Language model**, and **CAD bridge** there. Native CAD runs on a licensed Windows workstation, even when React and FastAPI are deployed elsewhere. A successful connection test verifies MCP initialization, required tool names and their argument contracts; it does not prove the installed CAD application can create/export a drawing.

## Local Windows: simplest setup

1. Install and activate AutoCAD or SOLIDWORKS desktop. Start it once under the Windows account that will run LineaForge and complete any first-run/license dialogs.
2. Start LineaForge with its Windows launcher. In Connections choose **AutoCAD** or **SOLIDWORKS**, then **stdio**.
3. Leave Command and Arguments blank to use the bundled source bridge. It runs with the same Python environment as the web backend and the job's confined output folder. The Python environment needs `mcp` and, on Windows, `pywin32`.
4. For SOLIDWORKS, enter an existing `.drwdot` drawing template in the CAD connection's **Template path** field. Use a blank, single-sheet template with suitable sheet dimensions. As an alternative, set the template environment variable before starting LineaForge. In PowerShell:

   ```powershell
   $env:LINEAFORGE_SW_DRAWING_TEMPLATE = 'C:\CAD\Templates\PidDrawing.drwdot'
   .\Start-LineaForge.cmd
   ```

   You can also set `LINEAFORGE_SW_DRAWING_TEMPLATE` in Windows **Environment Variables** and restart the launcher. The setting is read on the CAD host, not in the browser.
5. Save settings, use **Test connection**, then run a small reviewed drawing job. Inspect the generated drawing and the save/reopen report in the installed CAD application before processing a large engineering sheet.

If entering a custom Command, use a real executable such as `C:\LineaForge\.venv\Scripts\python.exe`. Arguments are a JSON array, not a shell command. For AutoCAD:

```json
["-m", "lineaforge.mcp_bridge", "--backend", "autocad", "--output-root", "C:\\LineaForge\\cad-exports"]
```

For SOLIDWORKS:

```json
["-m", "lineaforge.mcp_bridge", "--backend", "solidworks", "--output-root", "C:\\LineaForge\\cad-exports", "--template", "C:\\CAD\\Templates\\PidDrawing.drwdot"]
```

If you specify a fixed output root, also set **Remote output root** to that same absolute folder, even for a local custom bridge. This tells LineaForge where the bridge permits files and enables artifact retrieval. Default bridges launched without custom arguments automatically use the current job's output root.

The default modules are `lineaforge.mcp_bridge` for AutoCAD and SOLIDWORKS. Existing AutoCAD `lineaforge.cad_server` stdio clients continue working locally; use `mcp_bridge` when file retrieval or HTTP is needed.

## What each native adapter produces

| Backend | Output | Geometry and verification |
|---|---|---|
| Editable DXF | `.dxf` | Offline ezdxf entities, blocks, attributes, source XDATA, saved-file audit |
| AutoCAD | `.dwg` or `.dxf` | Native COM layers, blocks, attributes, literal polylines/text; saved file reopened in AutoCAD and enumerated |
| SOLIDWORKS | `.dxf` or `.dwg`, with a sibling `.slddrw` on the CAD host | Native drawing-sheet sketch geometry and notes; exported CAD file reimported into SOLIDWORKS and enumerated |

SOLIDWORKS is a **2D drawing adapter**. P&ID symbols expand into editable sketch segments. Visible TAG text is a drawing note; full attributes and source IDs are retained in a `.provenance.json` sidecar on the CAD host. It does not create an Electrical project, piping assembly, 3D model, or AutoCAD-style attributed block. Native line styles map to continuous/hidden/phantom and the normal SOLIDWORKS lineweight. The protocol uses millimetres and converts coordinates to SOLIDWORKS API metres. The drawing template retains its display-unit settings.

Use AutoCAD or offline DXF when attributed CAD blocks are required. No bridge executes drawing-provided macros, LISP, arbitrary commands, or executable paths.

## Local HTTP bridge

Run a persistent bridge when FastAPI and CAD use separate local processes. From a Python environment with LineaForge installed:

```powershell
python -m lineaforge.mcp_bridge --backend autocad --output-root C:\LineaForge\cad-exports --transport streamable-http --host 127.0.0.1 --port 8791
```

For SOLIDWORKS, replace `autocad` with `solidworks` and supply `--template` or the template environment variable. Configure Connections:

```json
{
  "backend": "autocad",
  "transport": "streamable_http",
  "endpoint": "http://127.0.0.1:8791/mcp",
  "remote_output_root": "C:\\LineaForge\\cad-exports",
  "timeout_seconds": 600
}
```

`localhost` is normalized to literal `127.0.0.1`; HTTP is accepted only for literal loopback addresses. Each drawing job initializes one MCP session, verifies tool availability, executes the CAD build, saves, reopens, and retrieves the artifact. Concurrent HTTP jobs receive a busy error instead of interleaving operations in one CAD document. Run one drawing job per CAD bridge/application at a time. Interrupted abandoned sessions release their lease when disposed, or after five minutes without a tool call; restart the bridge if an interactive CAD dialog has stalled a job.

## Deployed FastAPI + Windows CAD workstation

A deployed Linux/container backend cannot start Windows COM, and its `127.0.0.1` refers to its own machine. Keep the CAD bridge on the logged-in licensed Windows workstation. Reach it through a trusted VPN and HTTPS reverse proxy. Set a strong random bearer token on the CAD host and supply every exact trusted proxy Host header:

```powershell
$env:LINEAFORGE_CAD_TOKEN = 'YOUR_LONG_RANDOM_SECRET'
python -m lineaforge.mcp_bridge --backend autocad --output-root C:\LineaForge\cad-exports --transport streamable-http --host 0.0.0.0 --port 8791 --allowed-host cad.internal.example
```

Terminate HTTPS at the trusted proxy and restrict access to the FastAPI deployment/VPN. Preserve the configured Host header and MCP session headers; support streaming responses with buffering disabled and an adequate request timeout. The bridge refuses non-loopback bindings without a token and explicit allowed hosts. It validates Host and Origin headers and does not add permissive browser CORS rules.

Set `LINEAFORGE_CAD_TOKEN` in the **FastAPI server's** environment to the same secret. Configure the app with its environment-variable name, not the secret value:

```json
{
  "backend": "autocad",
  "transport": "streamable_http",
  "endpoint": "https://cad.internal.example/mcp",
  "auth_token_env": "LINEAFORGE_CAD_TOKEN",
  "remote_output_root": "C:\\LineaForge\\cad-exports",
  "timeout_seconds": 600
}
```

Remote MCP requests require external-data consent on the job. Geometry, text, and tags are sent to the explicitly configured CAD endpoint. Raw API secrets, inline authorization headers, URL credentials, and query-string credentials are rejected in saved CAD configuration. The HTTP client verifies TLS and follows no redirects or inherited proxies.

The `read_artifact` tool transfers generated DWG/DXF files in bounded base64 chunks with a final SHA-256 checksum. Paths are confined to the CAD host's configured output folder, and only `.dwg`/`.dxf` artifacts are exposed. Downloads are limited to 512 MiB and validated for the requested file signature before they become completed artifacts. A remote custom server that only reports a CAD-host filename cannot provide a downloadable file; it must implement this retrieval contract. SOLIDWORKS native `.slddrw` and provenance sidecars stay on the CAD host; the web download is the requested DWG/DXF.

Each remote job uses a distinct hashed subfolder on the CAD host. Before outgoing drawing commands, `mcp_transmission.jsonl` records the configured destination, consent state, tool names, argument byte counts, and SHA-256 hashes. It does not record literal tags, geometry arguments, or bearer credentials. This log records a transmission attempt; a completed artifact and native reopen report establish whether that attempt succeeded.

## MCP tool contract

The required tools are `new_drawing`, `set_units`, `create_layer`, `define_block`, `insert_block`, `draw_polyline`, `add_text`, `save_drawing`, and `reopen_and_enumerate`. Both bundled bridges also implement `draw_circle` and `draw_arc`; `mcp_bridge` adds `read_artifact`. Missing tools and MCP tool errors stop the job explicitly. Tool argument contracts and the complete command plan are validated before any drawing mutation. External schema references are disabled; a third-party server with a different argument contract needs an adapter. The model produces a reviewable P&ID graph; the deterministic CAD builder maps reviewed geometry to these tools. Specialist agents do not independently issue conflicting changes to the CAD application.

Inspect current schemas without starting CAD:

```powershell
python -m lineaforge.mcp_bridge --backend autocad --output-root C:\LineaForge\cad-exports --write-schema autocad-tools.json
python -m lineaforge.mcp_bridge --backend solidworks --output-root C:\LineaForge\cad-exports --write-schema solidworks-tools.json
```

Run the opt-in SOLIDWORKS native smoke test on the licensed workstation:

```powershell
$env:LINEAFORGE_SW_DRAWING_TEMPLATE = 'C:\CAD\Templates\PidDrawing.drwdot'
$env:LINEAFORGE_TEST_SOLIDWORKS = '1'
python -m pytest tests/test_solidworks_server.py -m local_cad -v
```

The regular test suite checks configuration, consent, path confinement, artifact validation, and actual MCP discovery without launching licensed CAD. Native API execution and full P&ID recognition accuracy require acceptance tests with your licensed CAD versions, configured models, and representative drawings. Connection-test success is not engineering acceptance.

## Native API references

The adapter uses the documented [SOLIDWORKS NewDocument](https://help.solidworks.com/2019/English/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.ISldWorks~NewDocument.html), [drawing note CreateText2](https://help.solidworks.com/2025/English/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.IDrawingDoc~CreateText2.html), [native save](https://help.solidworks.com/2026/English/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.IModelDocExtension~SaveAs.html), and [DXF/DWG import data](https://help.solidworks.com/2025/english/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.IImportDxfDwgData.html) interfaces. These references describe the native API; they do not establish that an untested local installation has passed an integration run.
