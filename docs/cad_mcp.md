# LineaForge CAD backends and local MCP helper

> Legacy desktop documentation. For the current v0.2 AutoCAD and SOLIDWORKS bridges, use [web CAD/MCP setup](web_cad_setup.md). The implementation now includes a SOLIDWORKS 2D drawing adapter; native acceptance testing is still required.

`lineaforge.cad.CadBackend` is the shared interface: `create_layer`, `define_block`, `insert_block`, `draw_polyline`, `add_text`, and `save`. `build_graph_cad(graph, backend, output_path, mode="fidelity")` consumes the validated `PidGraph` contract.

## Tested offline backend

`EzdxfBackend` writes actual editable R2013 DXF, reopens it with ezdxf, audits it and reports entity counts per layer/type and provenance IDs. The builder compares saved counts with its emitted per-layer counts and flags a mismatch. It never writes a DXF disguised as DWG. A real ODA/LibreDWG conversion adapter or licensed AutoCAD is required for native DWG.

```python
from lineaforge.cad import EzdxfBackend, build_graph_cad
from lineaforge.models import PidGraph

graph = PidGraph.model_validate_json(open("pid_graph.json", encoding="utf-8").read())
result = build_graph_cad(graph, EzdxfBackend(), "output/rebuilt.dxf")
```

Coordinates map source top-left pixels into bottom-left millimetres using the graph's physical page dimensions. Fidelity mode preserves literal points and does not snap them. Clean mode explicitly snaps mapped coordinates to a 1 mm grid. Literal source point geometry is already in page coordinates, so block insertion does not rotate it a second time. Text rotation is interpreted as clockwise in source-image coordinates and negated for CAD.

Every emitted entity carries the source graph element ID in `LINEAFORGE` XDATA. Symbol blocks carry `TAG`, `SERVICE`, `SIZE`, `SPEC`, `SOURCE_ID`, and `CONFIDENCE` attributes. Only TAG is visible by default; other attributes remain editable metadata. Distinct literal instances receive distinct block definitions because their source contours can differ.

**Missing symbol geometry is not reconstructed from a guessed textbook shape.** It produces a red review rectangle and review note, explicitly marked as a fallback. Supplied catalog crops are evidence for a VLM or a future reviewed vector block library; they are not silently converted into exact engineering blocks. Unreadable tags remain empty. Rejected elements are omitted. Graph edges without drawn points do not create invented routes. The partial gold sample therefore produces review flags for every selected equipment instance because it supplies regions/identifiers, not exact shape contours.

## AutoCAD through MCP

The helper requires Windows, a licensed and activated local AutoCAD installation, `mcp==1.16.0`, a compatible Pydantic version, and `pywin32`. COM calls run serially on one dedicated STA thread. Existing user documents remain open; the helper builds a new document.

```powershell
python -m lineaforge.cad_server --output-root C:\Work\LineaForgeOutput
```

The server uses stdio, with no network listener. The client starts the helper directly as an executable plus argument list, initializes an MCP session, calls the drawing tools, saves, reopens and enumerates real CAD entities. It does not send arbitrary AutoLISP, shell commands or CAD command strings.

```python
from lineaforge.cad import AutoCadMcpBackend, build_graph_cad

backend = AutoCadMcpBackend(output_root=r"C:\Work\LineaForgeOutput")
result = build_graph_cad(graph, backend, r"C:\Work\LineaForgeOutput\rebuilt.dwg")
```

An external local server can be selected with `AutoCadMcpBackend(command=..., args=[...], output_root=...)` if it implements the same tools. Command configuration must be supplied by the user/deployment, never by drawing content. The bundled helper checks save, reopen and plot targets against its configured output root after resolving paths. Native output currently uses AutoCAD 2013 DWG/DXF format. The offline core converter provides its own selectable DWG-version conversion settings.

Tools and key inputs:

| Tool | Inputs |
| --- | --- |
| `new_drawing` | none |
| `set_units` | `units="mm"` |
| `create_layer` | name, ACI color, linetype, lineweight |
| `define_block` | name, literal local geometry, attribute tag list |
| `insert_block` | name, position, attribute values, layer, scale, rotation, source ID |
| `draw_line` / `draw_polyline` | literal points, layer, closure, source ID |
| `draw_circle` / `draw_arc` | center, radius, angles, layer, source ID |
| `add_text` / `add_mtext` | literal text, position, size, rotation, layer, source ID |
| `set_attribute` | block handle, exact attribute tag, value |
| `create_layout` / `create_viewport` | layout name; viewport center/width/height |
| `save_drawing` | DWG/DXF output path |
| `reopen_and_enumerate` | saved output path |
| `plot_pdf` | output PDF path, optional installed canonical media name |

Generate machine-readable MCP tool schemas without launching AutoCAD:

```powershell
python -m lineaforge.cad_server --output-root output --write-schema docs/mcp_tool_schemas.json
```

The helper maps `LF-DASHED` to installed `DASHED` and `LF-DASHDOTDOT` to installed `PHANTOM` from `acad.lin`. These are deployment defaults; they are not a claim that every sample signal follows those styles. Plotting requires the installed `DWG To PDF.pc3` driver and an installed media name. Plotting and COM API compatibility must be verified on the target AutoCAD version.

## SolidWorks and validation limits

`SolidWorksMcpBackend` accepts an explicitly configured external local server implementing the same tool contract. Its default constructor raises `CadUnavailable` because this project does not bundle a SolidWorks automation server. SolidWorks needs a licensed local installation and is a weaker fit for P&ID layouts than AutoCAD; no 3D model is generated.

AutoCAD and SolidWorks were not installed on the build machine. Offline DXF tests exercise real saves, reopens, attributes, XDATA and coordinate mapping. Native tests are marked `local_cad` and skip with "requires local CAD" unless `LINEAFORGE_TEST_LOCAL_CAD=1` is set. No native-CAD parity, native plot fidelity or full-page symbol accuracy is claimed from a skipped test.

Official references: [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), [ezdxf blocks](https://ezdxf.readthedocs.io/en/stable/usage_for_beginners.html), [ezdxf units](https://ezdxf.readthedocs.io/en/stable/howto/document.html), [AutoCAD ActiveX block API](https://help.autodesk.com/cloudhelp/2020/ENU/AutoCAD-ActiveX-Reference/files/GUID-E6F7B03B-F5CC-4A18-9C48-BBF1D32A31FD.htm), [ODA converter](https://www.opendesign.com/guestfiles/oda_file_converter), [ODA SDK licensing](https://www.opendesign.com/pricing?language=en), [LibreDWG licensing](https://www.gnu.org/software/libredwg/), [SolidWorks PDF API](https://help.solidworks.com/2025/English/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.IExportPdfData.html).
