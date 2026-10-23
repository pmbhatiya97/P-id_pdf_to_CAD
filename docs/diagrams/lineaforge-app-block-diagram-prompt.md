# LineaForge Studio architecture image

Generated with the built-in image generation tool on 2026-10-23.

## Generation prompt

Use case: infographic-diagram.
Create a polished, technically accurate BLOCK DIAGRAM IMAGE of the existing "LineaForge Studio" app. This is a software architecture diagram for the product owner. Make an original high-resolution landscape diagram, approximately 3:2 aspect ratio. Prioritize perfectly readable English text, generous spacing, clear arrowheads, and a logical top-to-bottom flow. White background, dark navy text, thin slate connectors, rounded rectangular cards, restrained teal and indigo section accents, amber only for human review gates. Flat vector-like technical infographic, crisp typography, no photorealistic objects, no decorative circuitry, no tiny text, no perspective, no invented metrics.

Title: "LineaForge Studio"
Subtitle: "Web App • P&ID AI • CAD Automation"

LAYOUT AND EXACT CONTENT:
At the top, one compact input card: "Input Drawings" / "PDF • scans • DXF / DWG".
Arrow down into a wide frontend layer:
"React + TypeScript Workbench"
Inside show five short module labels: "P&ID Studio" (visually emphasized), "Agent Studio", "Connections", "Converter", "PDF Tools".
Arrow down into a wide backend layer:
"Python + FastAPI"
Smaller text: "Uploads & previews • Configuration • Job queue • Progress & cancellation".

The central and largest area is a bordered group titled:
"P&ID Agent Orchestrator"
Subheading: "28 specialist roles • Shared VLM / LLM providers"
Inside, six stage cards in a clear 3-column by 2-row serpentine sequence with arrows and numbered headings:
1 "Understand" / "Intake • Layout • Legend"
2 "Prepare" / "Tiles • Noise removal • Content-loss check"
3 "Extract" / "Equipment • Valves • Instruments • Tags"
4 "Connect" / "Piping • Signals • Graph building"
5 "Verify" / "Topology • Tags • Visual checks"
6 "Human Review" / "Accept • Edit • Reject"
The first row flows left to right, down to second row rightmost, then second row right to left; position cards 4,5,6 accordingly so arrow order is unambiguous. Human Review amber.
Then arrow down from Human Review into "Reviewed Graph Snapshot".

To the RIGHT of the central orchestrator, a separate provider card:
"Model Configuration"
"VLM: drawing understanding"
"LLM: reasoning & checks"
"Local / on-prem / cloud"
Connect it bidirectionally to the orchestrator group with a dashed line labelled "Configured endpoints".
Small text beneath the provider card: "External transfer requires consent".

To the LEFT of the orchestrator, a compact storage card:
"Project & Evidence Store"
"Originals • Tiles • Graphs"
"Review decisions • Audit logs"
Connect to backend and orchestrator using simple thin connectors.

Below Reviewed Graph Snapshot, a wide CAD automation band:
"CAD Builder & Validation"
Within it use a branching diagram:
"Typed CAD Plan" branches to two cards:
A. "Offline DXF" / "ezdxf"
B. "MCP Client" / "stdio / authenticated HTTPS"
From B an arrow to "Windows CAD Bridge", branching to two clearly labelled small cards "AutoCAD" and "SOLIDWORKS".
A and the native CAD branches converge into "CAD QA" / "Reopen • Count • Render comparison".
Make absolutely clear that MCP connects via the bridge to CAD apps, and that CAD QA follows CAD generation.
A small amber "Engineering Release Review" gate follows CAD QA.
Then a final output card: "Deliverables" / "DXF / DWG • Graph JSON • Tags CSV • Review PDF • QA report".
Do not imply SOLIDWORKS creates native AutoCAD Plant 3D projects or 3D assemblies.

A thin separate side path from FastAPI, outside the orchestrator, labelled "Conversion & PDF Engine" / "PDF ⇄ DXF • External DWG converter • PDF tools" may connect to Deliverables if space permits. It must not crowd the main P&ID pipeline.

Bottom footer in two concise lines:
"Run locally: Windows launcher starts the web app"
"Deploy: React + FastAPI server → protected Windows CAD bridge"
Small but readable final note: "Real-model accuracy and native CAD acceptance require validation."

Use accurate group hierarchy. Arrows must never pass through cards or text. Limit text to the supplied labels; do not add claims such as perfect accuracy, production certified, fully verified, or 28 simultaneous models. P&ID AI should visually dominate. All text should be spelled correctly and readable when zoomed.
