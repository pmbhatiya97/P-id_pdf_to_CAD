# Current beta limitations

This is a development beta. See [verification evidence](web_test_report.md); software tests do not establish real-model accuracy or engineering acceptance.

- Real symbol precision/recall, tag/line exact match, connectivity F1, and visual fidelity remain unmeasured. Recorded responses exercise contracts and control flow.
- Native AutoCAD/SOLIDWORKS drawing generation, licensed-version compatibility, and DWG fidelity require tests on the intended Windows workstation. Connection probes check protocol/tool compatibility.
- SOLIDWORKS produces 2D drawing/sketch geometry, rather than a 3D plant or Electrical project. Native drawing/provenance sidecars remain on the CAD host.
- Independent automated rendering accepts DXF. DWG requires a reviewed plot or DXF export for visual comparison.
- Missing shapes, unreadable tags, uncertain seams/crossings, and unknown sheet references require human review. Missing process data is not invented.
- Noise masks are coarse rectangles with a preservation check. Deskew/dewarp, learned pixel segmentation, and fully automated repair are unfinished. Repairs remain proposals.
- Generic conversion preserves outlined lettering as paths; semantic text recovery needs a configured model. Clipping, transparency, fonts, plot styles, and complex geometry can differ in CAD.
- Raster tracing is opt-in and best effort; it does not itself recognize symbols or infer connectivity.
- One selected page is analyzed per job. Off-page references are not a verified cross-sheet plant model.
- Review supports geometry/connection editing and JSON, rather than a full native CAD editor. Clean mode intentionally snaps to a 1 mm grid.
- Structural counts and source IDs do not certify visual fidelity. Humans review before CAD generation and engineering release.
- Large sheets can consume substantial memory. Threaded cancellation can wait for an active model, converter, or native CAD timeout.
- Deployment supports one trusted workspace and one ASGI process. SSO/RBAC, tenant isolation, and distributed workers are absent. Docker configuration has not been executed here.
- Windows builds are unsigned. CAD applications, model weights, DWG utilities, and installer compilers are separate dependencies. Review their licenses before commercial distribution.

Published source excludes private drawings, reference crops, evaluation annotations, credentials, runtime data, and conversion outputs. Tests needing excluded evidence skip explicitly.
