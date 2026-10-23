# Web / P&ID Studio verification — 22 October 2026

This report separates executed software checks from engineering accuracy. The local recorded model server supplies fixed JSON responses; it is not a recognition system.

## Executed results

The complete suite finished with **120 passed, 2 skipped, 104 warnings in 30.18 seconds**. Both skips require explicitly enabled licensed native CAD installations. Warnings are existing PyMuPDF SWIG and ezdxf/pyparsing deprecations. Real output: `artifacts/web-tests.txt` and `artifacts/web-tests.xml`.

```text
pytest -q -p no:cacheprovider --disable-warnings --junitxml=artifacts/web-tests.xml
120 passed, 2 skipped, 104 warnings in 30.18s
```

The suite covers the original converter/PDF toolkit and desktop regression checks, specialist orchestration, durable FastAPI jobs, privacy gates, graph editing, real saved DXF geometry, and actual MCP sessions. A separate repeat-launch regression also verifies that an occupied port cannot reopen/reset the active durable job store. No production recognition score follows from these tests.

| Area | Evidence exercised |
|---|---|
| Agent context | Full-page overview plus high-resolution target tile, observed legend profile and earlier canonical IDs; text reviewers receive bounded graph excerpts and neighboring endpoint summaries |
| Agent routing | All 28 catalog roles enabled in a real localhost OpenAI-compatible HTTP integration test; VLM requests include images and LLM requests carry structured text |
| Geometry | Rasterizer-derived page size and tile origins; non-square axis scales; overlap reference remapping; out-of-bounds rejection; explicit tile-seam review |
| Uncertainty | UNREADABLE cannot be accepted without verified edits; models cannot return ACCEPTED or REJECTED; all source findings must be resolved before CAD |
| Noise | Changed candidates receive independent before/after verification; suspected loss restores original pixels; an operator can rerun with removal masks disabled |
| Review consistency | Calibration/provenance remain fixed; manual geometry and connection edits validate references; graph/CSV/review downloads refresh; decision hashes are recorded |
| CAD snapshots | Each queued CAD job stores an immutable reviewed graph, so subsequent source edits do not change its drawing |
| MCP | Real stdio discovery for both bundled helpers and authenticated localhost Streamable HTTP discovery; argument contract checks; complete command-schema preflight before mutation; external schema references blocked |
| CAD files | Offline DXF is saved, reopened, audited, counted and rendered; source IDs checked; source/render tile comparisons run through the HTTP test provider |
| Failures | Missing tools, tool errors, malformed responses, budgets, cancellation, incompatible schemas, corrupt artifacts, wrong hashes and unavailable native CAD produce explicit failures |
| Privacy | No external transfers in recorded local tests; cloud jobs require consent; reference catalog withheld from cloud models; local provenance paths excluded from model context; outgoing remote CAD plans logged as hashes |

## Visible browser workflow

The app was exercised through its actual UI using **QA-SYNTHETIC-PID.pdf** and clearly named `recorded-qa-vlm` / `recorded-qa-llm` endpoints on loopback. Model settings were saved, connections tested, a file uploaded, and digitization run. The review rejected the unreadable fixture element and explicitly approved the other two. CAD generation produced a real DXF and a source/render QA report, while retaining the source graph and review context in P&ID Studio.

The test drawing and responses are synthetic. Their three-element count is a fixture assertion, not a symbol detection metric. Screenshot: `artifacts/pid-studio-desktop.jpg`.

Lighthouse accessibility reports are stored as `artifacts/web-a11y-*.json`. P&ID Studio scored **100/100 at 360px and desktop width**; the connection and agent screens also scored 100. Inactive inspector-tab contrast and heading order were corrected during this audit. These are automated accessibility checks, not a complete WCAG certification.

## Packaged Windows validation

The actual `LineaForgeWeb.exe` passed localhost startup/health/static-asset checks, both bundled MCP helper discovery probes, a real PDF-to-DXF conversion with two saved entities, and repeat-launch reuse. The packaged app also ran all 28 roles through the recorded HTTP model server, enforced human review, saved/reopened CAD, and compared a source/render tile. Evidence: `artifacts/frozen-web-smoke.json` and `artifacts/frozen-web-smoke.txt`. This still does not measure recognition accuracy or execute licensed native CAD.

The audit found and fixed a missing SSL policy data file in the managed Python packaging. Certificate verification remains enabled. Windows builds use fresh release directories to avoid locked generated folders.

## Accuracy and release limits

- **Symbol precision/recall, tag exact match, line-number exact match and connectivity F1 remain unmeasured.** No real VLM/LLM endpoint has been supplied. Existing annotations cover only a partial equipment set and cannot support full-sheet accuracy claims.
- The supplied eight PDFs are clean vector drawings with outlined lettering. Three independent noisy scans were not available, so the noisy-scan acceptance requirement is unverified.
- Native AutoCAD/SOLIDWORKS drawing creation, native DWG fidelity, installed-version compatibility and backend parity require the user's licensed Windows applications. A successful MCP probe verifies protocol/tool compatibility; it does not verify licensing or drawing fidelity.
- Independent automated rendering currently accepts DXF. Choosing native DWG records save/reopen evidence and leaves native plotted comparison for human review. Use DXF for the complete automated source/render comparison path.
- Tile-seam continuations and uncertain topology are flagged. The system does not silently invent cross-tile joins or missing process data.
- Complex multipart symbols, curved details without complete traces, and exact tag placement can require manual geometry completion and native CAD editing. The current graph-to-CAD builder cannot certify that every source shape has been reproduced.
- Repair agents propose evidence-based changes; proposals never automatically modify process data. Deskew/dewarp and uncertain annotations remain review work. Masks are rectangular tooling, not a learned pixel segmentation system.
- Clean mode snaps coordinates to a 1 mm grid. Manual correction uses source-pixel coordinates and an advanced graph editor; it is not a full CAD drawing editor.
- CAD QA findings appear in the workbench and downloadable evidence. Final engineering release remains a human responsibility; the software never converts structural counts into a fidelity certificate.
- Docker/Compose files are supplied. Docker execution is unverified here because the Docker CLI is unavailable. Deployment currently supports one trusted workspace and one ASGI process, with two bounded background-job workers; tenant isolation and SSO are not implemented.

For commercial acceptance, supply representative real drawings, independent complete annotations, the intended model endpoints, and the exact licensed CAD versions. Record the measured recognition and round-trip results before treating this release as production-qualified.

## Dependency security pass

The final web runtime pins FastAPI 0.160.0, Starlette 1.9.0, python-multipart 0.0.32 and MCP SDK 1.30.0. Transitive web dependencies are pinned in requirements-web.txt. These replace the earlier compatibility-test pins before final distribution.

The change follows the maintainers' advisories for [Windows static-file UNC resolution](https://github.com/Kludex/starlette/security/advisories/GHSA-wqp7-x3pw-xc5r), [multipart parser resource exhaustion](https://github.com/Kludex/python-multipart/security/advisories/GHSA-mj87-hwqh-73pj), and [MCP output-schema reference retrieval](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-rwrf-2pqf-9j8j). A Windows regression asserts UNC input is rejected before network path resolution. Updated dependency versions do not constitute a complete penetration test.
