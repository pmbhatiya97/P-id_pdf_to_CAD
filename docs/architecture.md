# Architecture and trust boundaries

LineaForge Studio v0.2 serves React/TypeScript from Python/FastAPI. The Windows launcher runs the compiled frontend and API at one origin. The earlier PySide6 desktop entry point remains optional.

## P&ID workflow

An explicit Python orchestrator coordinates **28 roles: 19 VLM, 4 LLM, and 5 deterministic Python roles**. They share configured endpoints. Repair is optional; 27 roles are enabled by default. Tiles and dependency groups progress sequentially with up to four current extraction calls in parallel.

1. Intake, layout, and legend roles analyze one selected page and visible conventions.
2. Python rasterizes 300–600 DPI overlapping tiles with exact source offsets.
3. Noise analysis proposes rectangles. Local masking and independent preservation checks retain or restore source content.
4. Specialists extract equipment, valves, instruments, inline components, tags, line numbers, and notes. Piping, signals, connectivity, and off-page roles trace explicit evidence.
5. Python normalizes coordinates, deduplicates overlap, validates references, and flags uncertain seams. LLM reviewers inspect graph topology, tags/loops, and piping consistency. A fresh visual check revisits flagged source regions.
6. Repair proposals stay unapplied. The CAD planner reports readiness issues. Humans accept, edit, or reject evidence before submission.
7. An immutable reviewed graph feeds one deterministic CAD builder, selecting offline DXF or typed native CAD MCP tools.
8. CAD QA reopens/counts entities and compares source/render evidence where supported. Human engineering release remains required; reporting publishes traceable artifacts.

[Full role diagram](diagrams/lineaforge-technical-ecosystem.svg)

## Models and evidence

Providers use OpenAI-compatible endpoints and validated JSON contracts. LLM roles fall back to the VLM when no LLM model is configured. Separate prompts do not imply independent underlying model weights.

Local mode accepts literal loopback IPs. External destinations require per-job consent; clients disable redirects and inherited proxies. Private reference catalogs are withheld from external model calls. API keys are redacted from public responses, while local configuration needs filesystem protection.

Drawing content and model text are evidence, not executable instructions. No drawing-provided shell, arbitrary AutoLISP, macros, or executable paths are run. Transmission logs retain hashes and metadata without authentication secrets.

## CAD and runtime

Offline DXF uses ezdxf. AutoCAD and SOLIDWORKS adapters use dedicated COM threads on licensed, logged-in Windows workstations. SOLIDWORKS implements 2D drawing/sketch export. A deployed backend reaches the Windows bridge through authenticated HTTPS/VPN.

The MCP client validates schemas and the entire typed command plan before mutation. Output paths are confined; downloaded artifacts are bounded and checked by signature and SHA-256. One drawing job owns a CAD bridge at a time. Only the deterministic builder sends geometry commands.

Independent rendering currently covers DXF; native DWG needs a reviewed plot or DXF export. Counts, source IDs, and an MCP handshake establish structure/protocol evidence rather than engineering fidelity.

The store keeps originals, tiles, crops, schema-validated role responses, cache, graphs, human decisions, snapshots, CAD artifacts, and reports. Budgets bound model calls, retries, time, and estimated cost. Cancellation is cooperative.

Deployment uses one ASGI process, two background-job workers, and a bounded queue for one trusted workspace. SSO/RBAC, tenant isolation, distributed scheduling, hard process memory limits, and verified automatic cross-sheet merging are absent.
