# Technical ecosystem image generation

Built-in image generation tool, 2026-10-23. Labels verified against the current agent catalog. The editable SVG companion provides precise technical connectors.

## Initial prompt

+Edit the supplied LineaForge Studio block diagram into a substantially more detailed TECHNICAL AGENT ECOSYSTEM POSTER. Preserve its professional navy/teal/indigo style, but redesign the layout on a much taller canvas so every agent is readable. Create one coherent diagram, portrait 2:3 aspect ratio, maximum available resolution, ideally 2048 x 3072 or larger. White background, crisp typography, technical flat vector-like boxes, clean orthogonal arrows. This is an architecture deliverable, not marketing art.

MANDATORY: show ALL 28 numbered roles exactly once. Distinguish V = VLM model role, L = LLM model role, D = deterministic Python role using colored letter badges on every role. The 28 roles share configured model endpoints; they are not 28 separately installed models.

TITLE:
"LineaForge Studio"
"Technical Agent Ecosystem"

TOP APPLICATION LAYERS, compact but readable:
"Browser / React + TypeScript"
"P&ID Studio • Agent Studio • Connections • Converter • PDF Tools"
Down arrow into:
"Python + FastAPI"
"Uploads & previews • Config & secrets • Persistent jobs • Progress / cancel • Review API • Downloads"

MAIN TECHNICAL CORE, the visually dominant section:
Group heading: "P&ID AI Pipeline — 28 roles"
Immediately below, a control bar:
"01 Job orchestrator [D]"
"Dependencies • Call budgets • Up to 4 parallel specialist calls"
The orchestrator schedules all subsequent groups; it is not a model.

Show the following sequential grouped cards. Every numbered role must have its V/L/D badge. Use two columns of pipeline groups if needed to keep typography large, with very explicit ordered arrows; do not make one extremely tall unreadable column. Role names can occupy two lines but must not be clipped.

Group "A • Whole-sheet understanding"
"02 Intake classifier [V]"
"03 Global layout analyst [V]"
"04 Legend & standard expert [V]"
Small output caption: "Overview + drawing conventions"

Arrow through a small non-agent operation chip:
"300–600 DPI overlapping tiles"

Group "B • Noise & preservation"
"05 Noise analyst [V]"
"06 Content preservation verifier [V]"
Small output caption: "Local masks • Cleaned / diff images • Restore if content is lost"

Group "C • Symbols & text"
"07 Equipment specialist [V]"
"08 Valve specialist [V]"
"09 Instrumentation specialist [V]"
"10 Inline component specialist [V]"
"11 Tag & text reader [V]"
"12 Line number reader [V]"
"13 Notes & table reader [V]"

Group "D • Connections"
"14 Piping tracer [V]"
"15 Signal tracer [V]"
"16 Connectivity analyst [V]"
"17 Off-page & tie-in analyst [V]"

Group "E • Graph & evidence validation"
"18 Process graph builder [D]"
"19 Topology reviewer [L]"
"20 Tag & loop reviewer [L]"
"21 Piping consistency reviewer [L]"
"22 Independent visual verifier [V]"
"23 Evidence repair proposer [V]"
Beside role23 show "Optional • proposals only".
Small caption under role18: "Coordinates • Deduplication • Reference checks"
Small caption under role22: "Fresh 600 DPI source evidence"

Then role:
"24 CAD build planner [L]"
Then a clearly visible amber gate:
"HUMAN REVIEW"
"Accept / edit / reject → immutable reviewed graph"
This human operator box is NOT numbered and must not be counted as an extra agent.

Then:
"25 MCP CAD builder [D]"
"Validated typed command plan"
From role25 branch into CAD execution paths described below.
After those paths converge:
"26 CAD render verifier [V + Python checks]"
"Reopen • Entity counts • Source / render comparison"
Then:
"27 Human release gate [D]"
Amber styling. Caption: "Engineering review required"
Then:
"28 Evidence reporter [D]"
Then final output box:
"Deliverables"
"DXF / DWG • Graph JSON • Tags CSV"
"Review PDF • CAD render • QA evidence"

MODEL ROUTING ECOSYSTEM, to the side of the main pipeline:
A panel titled "Model Router"
"OpenAI-compatible endpoints"
"VLM: 19 vision roles"
"LLM: 4 reasoning roles"
"Blank LLM → VLM fallback"
"Per-role prompts • JSON schemas • Retries"
Connect with dashed arrows to the V/L roles as groups, without cluttering individual rows. Add a small key:
"V = VLM   L = LLM   D = Python"
"5 deterministic roles"
A nearby compact configuration/privacy card:
"Configuration & consent"
"Endpoints • Model IDs • Keys"
"Role toggles • Budgets"
"External transfer consent"

SHARED STORAGE, side panel with bidirectional links to FastAPI and core:
"Shared Evidence Store"
"Original PDFs • Tiles"
"Cleaned / diff / source crops"
"Per-role JSON • Cache"
"Graph • Review decisions"
"Events • Transmission hashes"
"QA reports • CAD artifacts"

CAD ECOSYSTEM, connected specifically between role25 and role26:
Branch A:
"Offline CAD"
"ezdxf → editable DXF"
Branch B:
"MCP Client"
"Local stdio / authenticated HTTPS"
→ "Windows MCP Bridge"
→ two sibling cards:
"AutoCAD COM"
"Layers • Blocks • Attributes • DWG / DXF"
and
"SOLIDWORKS COM"
"2D drawing / sketch • DWG / DXF"
Both native cards and Offline CAD must join before role26.
A compact adjacent panel titled "MCP Tool Contract" with these exact tool identifiers, arranged into short rows:
"new_drawing • set_units"
"create_layer • define_block • insert_block"
"draw_polyline • add_text"
"save_drawing • reopen_and_enumerate"
"Extras: draw_circle • draw_arc • read_artifact"
Small caption: "Only the deterministic builder sends CAD commands"
Do not draw specialist agents directly editing native CAD.
Small QA note: "DWG visual QA needs a reviewed plot or DXF export"

OTHER WORKLOADS, compact auxiliary card connected to FastAPI:
"Conversion & PDF Engine"
"PyMuPDF • OpenCV • ezdxf"
"PDF ⇄ DXF • External DWG converter"
"PDF merge / split / compress"

BOTTOM DEPLOYMENT STRIP:
"Windows local"
"Start-LineaForge.cmd → React + FastAPI"
"Server deployment"
"Docker web/backend → HTTPS / VPN → licensed Windows CAD host"

FOOTNOTES, readable:
"27 roles enabled by default; repair is opt-in."
"One selected page per analysis job; cross-sheet links require review."
"Real-model accuracy and native CAD acceptance require validation."

DESIGN CONSTRAINTS:
The full ecosystem must be understandable as a real implemented architecture. All 28 role numbers must be present, with correct names and execution types. Avoid false claims of perfect accuracy or autonomous engineering approval. Models propose structured evidence; Python enforces contracts; humans review; the CAD builder alone executes the reviewed plan. Avoid spaghetti arrows, unnecessary decoration, repeated numbered roles, tiny captions, vague generic agent labels, invented services, invented databases, Kubernetes, vector databases, or agent frameworks not listed. Use generous spacing and a clear visual hierarchy.

## Arrow correction prompt

+Make a precise technical correction to the supplied "LineaForge Studio — Technical Agent Ecosystem" poster. Keep all 28 role numbers, every role name, all sidebar text, the portrait format, colors, typography, and overall layout. Improve only the flow-arrow accuracy and the CAD execution layout. Use the highest available resolution.

CRITICAL PIPELINE CORRECTION:
Use exactly this main execution chain with solid directional arrows:
01 Job orchestrator -> A Whole-sheet understanding -> 300–600 DPI overlapping tiles -> B Noise & preservation -> C Symbols & text -> D Connections -> E Graph & evidence validation -> 24 CAD build planner -> HUMAN REVIEW -> 25 MCP CAD builder.
Remove arrows A->D, B->E, C->24 and 01->D, because those arrows incorrectly show bypasses. Route C->D neatly along the outside edge of the two-column pipeline; use a thin orthogonal connector, crossing no cards or text. One coherent main execution chain only. Sidebar data/model links may remain dashed or bidirectional.
Connect the INPUT DRAWINGS card to React. Conversion & PDF Engine is invoked by FastAPI, rather than acting as an input drawing source.

CRITICAL CAD CORRECTION:
Redraw the entire inside of the bottom "CAD Ecosystem" panel to fit an unmistakable TWO-BRANCH PARALLEL diagram. The output arrow from25 must land on a small split point labelled "Select backend". This split has exactly two branches:
Branch A -> "A. Offline CAD" / "ezdxf -> editable DXF" ->26 CAD render verifier.
Branch B -> "B. MCP Client" / "Local stdio / authenticated HTTPS" -> "Windows MCP Bridge" -> forks to "AutoCAD COM" and "SOLIDWORKS COM"; each native application then joins26 CAD render verifier.
There must be NO arrow from Offline CAD to MCP Client. There must be NO direct arrow from25 to AutoCAD bypassing the MCP Client and bridge. There must be NO arrow from Windows MCP Bridge to Offline CAD.
The offline branch and MCP branch are alternative selected backends, never consecutive stages.
Use a spacious two-row CAD layout if needed: offline DXF on the upper left of CAD panel going directly down to26, MCP Client on upper middle, Windows bridge next, AutoCAD and SOLIDWORKS stacked upper right, both converge down to26. A single clear backbone can fork toA orB. Keep the MCP tool contract as an adjacent information panel with a dashed association to MCP Client, NOT as an execution step between native CAD and26.
Preserve26->27 Human release gate->28 Evidence reporter->Deliverables unchanged.
Do not add extra numbered agents. Keep all main text readable and ensure orthogonal connectors never pass through cards or text.
The result must be accurate to a deterministic reviewed-graph CAD builder selecting either offline DXF or MCP-controlled native CAD.
