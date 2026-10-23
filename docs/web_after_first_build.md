# Web migration changes after the native first build

New files are listed separately. These excerpts show edits to pre-existing files; the full source archive contains the complete implementation.

## .gitignore

```diff
--- before/.gitignore
+++ after/.gitignore
@@ -1,3 +1,7 @@
 .runtime/
+.webruntime/
+web/node_modules/
+web/dist/
+.lineaforge/
 .webqa/
 .venv/
@@ -13,2 +17,4 @@
 .env
 landing/.openai/deploy-state/
+*.tsbuildinfo
+.webruntime-secure/
```

## lineaforge/__init__.py

```diff
--- before/lineaforge/__init__.py
+++ after/lineaforge/__init__.py
@@ -1,2 +1,2 @@
 """LineaForge: editable drawing conversion with explicit review gates."""
-__version__ = "0.1.0"
+__version__ = "0.2.0"
```

## pyproject.toml

```diff
--- before/pyproject.toml
+++ after/pyproject.toml
@@ -5,5 +5,5 @@
 [project]
 name = "lineaforge"
-version = "0.1.0"
+version = "0.2.0"
 description = "Local-first 2D drawing conversion and reviewable P&ID digitization"
 requires-python = ">=3.12,<3.14"
@@ -11,5 +11,7 @@
   "PyMuPDF==1.26.5", "ezdxf==1.4.3", "PySide6==6.10.0",
   "opencv-python-headless==4.12.0.88", "numpy==2.2.6", "Pillow==12.3.0",
-  "pydantic==2.11.9", "pydantic-settings==2.11.0", "httpx==0.28.1", "mcp==1.16.0"
+  "pydantic==2.11.9", "pydantic-settings==2.11.0", "httpx==0.28.1", "mcp==1.30.0",
+  "fastapi==0.160.0", "starlette==1.9.0", "uvicorn==0.54.0",
+  "python-multipart==0.0.32", "sse-starlette==3.5.0"
 ]

@@ -21,4 +23,6 @@
 lineaforge-desktop = "lineaforge.desktop:main"
 lineaforge-cad-server = "lineaforge.cad_server:main"
+lineaforge-web = "lineaforge.web_launcher:main"
+lineaforge-cad-bridge = "lineaforge.mcp_bridge:main"

 [tool.setuptools.packages.find]
```

## requirements.lock

```diff
--- before/requirements.lock
+++ after/requirements.lock
@@ -1,11 +1,15 @@
-# Python 3.12 x64 Windows; pinned tested runtime and build dependencies
+# Python 3.12 x64 Windows; tested desktop + web runtime and build pins
 altgraph==0.17.5
+annotated-doc==0.0.5
 annotated-types==0.8.0
 anyio==4.15.1
 attrs==26.1.0
 certifi==2026.10.19
+cffi==2.1.1
 click==8.5.0
 colorama==0.4.6
+cryptography==50.0.2
 ezdxf==1.4.3
+fastapi==0.160.0
 fonttools==4.67.0
 h11==0.16.0
@@ -17,11 +21,13 @@
 jsonschema==4.26.0
 jsonschema-specifications==2025.9.1
-mcp==1.16.0
+mcp==1.30.0
 numpy==2.2.6
 opencv-python-headless==4.12.0.88
+opentelemetry-api==1.45.1
 packaging==26.3
 pefile==2023.2.7
 pillow==12.3.0
 pluggy==1.7.0
+pycparser==3.12
 pydantic==2.11.9
 pydantic-core==2.33.2
@@ -30,4 +36,5 @@
 pyinstaller==6.16.0
 pyinstaller-hooks-contrib==2026.9
+pyjwt==2.15.1
 pymupdf==1.26.5
 pyparsing==3.3.3
```

## README.md

```diff
--- before/README.md
+++ after/README.md
@@ -1,3 +1,19 @@
 # LineaForge
+
+## React + FastAPI workbench (v0.2)
+
+**Windows:** extract `dist/LineaForge-Web-Windows.zip` completely, then double-click **Start-LineaForge.cmd**. It starts the Python API, serves the compiled React frontend, and opens `http://127.0.0.1:8765`. The portable package includes Python and needs neither Node.js nor a separate frontend process. Keep `LineaForgeCadBridge.exe` beside `LineaForgeWeb.exe`. Closing the server console stops the application. A second click opens the existing instance.
+
+In this prepared source checkout, the root **Start-LineaForge.cmd** also runs the app. Source installations require Python 3.12/3.13 and Node.js 24 for the first frontend build; the script creates a virtual environment, checks the pinned dependencies, builds React if necessary, and then starts both layers on one origin.
+
+- **Connections → Vision / Language models:** configure OpenAI-compatible base URLs, model IDs, local/cloud modes and credentials. Local mode uses a literal loopback IP such as `http://127.0.0.1:8000/v1`. No model weights are bundled. Testing a connection lists models; it does not test P&ID recognition accuracy.
+- **Connections → CAD bridge:** choose offline editable DXF, AutoCAD, or SOLIDWORKS; stdio starts the bundled Windows MCP helper automatically. For a separate workstation, configure a protected HTTPS MCP endpoint and its output root. See [CAD setup](docs/web_cad_setup.md).
+- **Agent Studio:** 28 independently prompted or deterministic specialist roles, provider routing, prompt overrides, bounded parallel extraction, call/time/cost budgets, and traceable evidence. The roles share the configured LLM/VLM; 28 roles do not require 28 model servers. Required gates cannot be disabled.
+- **P&ID Studio:** full-page overview, overlapping high-resolution tiles, specialist extraction, graph checks, independent verification, human accept/edit/reject, explicit CAD generation, and post-build QA. Model responses never count as human approval. Unreadable content cannot be silently accepted.
+- **Drawing Converter / PDF Tools:** the tested local conversion engine and page toolkit are exposed through durable background jobs with cancellation, retries and artifact downloads.
+
+For deployment, `Dockerfile` builds React and serves it from FastAPI; `compose.yaml` persists one workspace. Set a random `LINEAFORGE_API_TOKEN` of at least 24 characters, then run `docker compose up --build -d`. Use HTTPS at your reverse proxy and enter the token in the browser. CAD automation still runs on a licensed, logged-in Windows workstation. This release is a single-workspace, single-worker product foundation; tenant isolation, SSO/RBAC and distributed job processing are not implemented. Docker startup has not been validated here because Docker is unavailable.
+
+Read [web setup and deployment](docs/web_product.md), [verification evidence](docs/web_test_report.md), and [changes with before/after excerpts](docs/web_after_first_build.md). The older native desktop interface remains available below. **Real model accuracy and native AutoCAD/SOLIDWORKS generation require acceptance testing with your models and licensed installations; no perfect-reconstruction claim is made.**

 A local-first Windows toolkit for 2D PDF/CAD conversion, PDF page tools, and **reviewable** P&ID extraction. This repository contains a working development beta, an original landing page, a local AutoCAD MCP helper, schemas, tests, and an evidence-based dataset study. It is not a claim that every requirement in the production brief is complete.
```

## lineaforge/cad_qa.py

```diff
--- before/lineaforge/cad_qa.py
+++ after/lineaforge/cad_qa.py
@@ -65,7 +65,8 @@
                     continue
                 tile_path = root/f"cad-tile-{index:04}.png"
-                px_to_pt = 72*graph.width_mm/(25.4*graph.width_px)
-                output[0].get_pixmap(matrix=fitz.Matrix(1/px_to_pt,1/px_to_pt),
-                                    clip=fitz.Rect(x0*px_to_pt,y0*px_to_pt,x1*px_to_pt,y1*px_to_pt), alpha=False).save(tile_path)
+                x_to_pt = 72*graph.width_mm/(25.4*graph.width_px)
+                y_to_pt = 72*graph.height_mm/(25.4*graph.height_px)
+                output[0].get_pixmap(matrix=fitz.Matrix(1/x_to_pt,1/y_to_pt),
+                                    clip=fitz.Rect(x0*x_to_pt,y0*y_to_pt,x1*x_to_pt,y1*y_to_pt), alpha=False).save(tile_path)
                 result = QaResult.model_validate(provider.complete("cad_qa",
                     "Independent CAD verifier. Compare cleaned source (first) and CAD render (second). "
```

## New implementation

The source archive includes the React workbench, FastAPI/config/jobs, specialist orchestrator, independent CAD QA, MCP transports, Windows AutoCAD/SOLIDWORKS bridge, launch/build/package scripts, Docker/Compose and regression tests. The final runtime uses patched FastAPI/Starlette/multipart/MCP versions. See web_test_report.md for executed evidence and limits.
