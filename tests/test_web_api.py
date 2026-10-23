"""Real conversion/API tests; recorded graphs do not measure AI recognition accuracy."""
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import time
import types
import uuid

import fitz
import pytest
from fastapi.testclient import TestClient

from lineaforge import core
from lineaforge.models import Element, PidGraph, PipelineResult, ReviewItem
from lineaforge.web_api import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    for key in ("LINEAFORGE_API_TOKEN", "LINEAFORGE_DEPLOYMENT", "LINEAFORGE_HOST", "LINEAFORGE_ALLOWED_ORIGINS"):
        monkeypatch.delenv(key, raising=False)
    return create_app(tmp_path / "data", frontend_dir=tmp_path / "no-frontend")


@pytest.fixture
def client(app):
    with TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000)) as client:
        yield client


def pdf_bytes():
    with fitz.open() as doc:
        page = doc.new_page(width=180, height=100)
        page.draw_line((10, 10), (100, 70))
        page.insert_text((20, 35), "PT-101", fontsize=10)
        return doc.tobytes()


def upload(client, raw=None, name="drawing.pdf"):
    response = client.post("/api/files", files={"file": (name, raw if raw is not None else pdf_bytes(), "application/pdf")})
    assert response.status_code == 201, response.text
    return response.json()


def finish(client, job_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        response = client.get("/api/jobs/" + job_id)
        assert response.status_code == 200
        value = response.json()
        if value["status"] not in {"queued", "running"}:
            return value
        time.sleep(.02)
    raise AssertionError("Job did not finish")


def test_upload_convert_preview_and_download_real_dxf(client, app):
    original = pdf_bytes()
    source = upload(client, original, name="../../drawing.pdf")
    assert source["name"] == "drawing.pdf" and source["pages"] == 1
    preview = client.get(f"/api/files/{source['id']}/preview?page=0")
    assert preview.status_code == 200 and preview.content.startswith(b"\x89PNG")
    response = client.post("/api/jobs", json={"type": "convert", "file_ids": [source["id"]], "options": {"output_format": "dxf", "pages": "all"}})
    assert response.status_code == 202
    job = finish(client, response.json()["id"])
    assert job["status"] == "completed", job
    artifact = next(item for item in job["artifacts"] if item["kind"] == "dxf")
    downloaded = client.get(artifact["url"])
    assert downloaded.status_code == 200 and b"SECTION" in downloaded.content[:512]
    import ezdxf
    document = ezdxf.read(io.StringIO(downloaded.content.decode("utf-8").replace("\r\n", "\n")))
    assert len(document.modelspace()) > 0
    assert app.state.manager.file_path(source["id"]).read_bytes() == original
    assert job["events"][-1]["progress"] == 100


def test_corrupt_drawing_failure_isolated_and_metadata_survives_restart(client, app):
    bad = upload(client, b"not a pdf")
    assert bad["inspection_error"]
    response = client.post("/api/jobs", json={"type": "convert", "file_ids": [bad["id"]], "options": {"target": "dxf"}})
    failed = finish(client, response.json()["id"])
    assert failed["status"] == "failed" and failed["error"] and not failed["artifacts"]
    good = upload(client)
    response = client.post("/api/jobs", json={"type": "pdf_tools", "file_ids": [good["id"]], "options": {"operation": "compress", "quality": "lossless"}})
    assert finish(client, response.json()["id"])["status"] == "completed"
    from lineaforge.web_jobs import JobManager
    fresh = JobManager(app.state.manager.root, app.state.config)
    try:
        assert fresh.get(failed["id"])["status"] == "failed"
        assert len(fresh.files()) == 2
    finally:
        fresh.close()


def test_config_redacts_preserves_and_explicitly_clears_api_keys(client):
    secret = "private-secret-value"
    response = client.put("/api/config", json={"models": {"vlm": {"api_key": secret, "model": "vision-test"}}})
    assert response.status_code == 200 and secret not in response.text
    assert response.json()["models"]["vlm"]["api_key_set"] is True
    response = client.put("/api/config", json={"models": {"vlm": {"api_key": "", "model": "updated-test"}}})
    assert response.json()["models"]["vlm"]["api_key_set"] is True
    response = client.put("/api/config", json={"models": {"vlm": {"clear_api_key": True}}})
    assert response.json()["models"]["vlm"]["api_key_set"] is False
    assert "api_key\"" not in client.get("/api/config").text
    invalid = client.put("/api/config", json={"models": {"vlm": {"mode": "invalid", "api_key": secret}}})
    assert invalid.status_code == 422 and secret not in invalid.text


def test_local_api_blocks_rebinding_cross_origin_and_path_references(client, app):
    assert client.get("/api/config", headers={"host": "attacker.example"}).status_code == 403
    assert client.get("/api/config", headers={"origin": "https://attacker.example"}).status_code == 403
    assert client.get("/api/config", headers={"origin": "http://127.0.0.1:8765"}).status_code == 200
    assert client.get("/api/jobs/..%5C..%5Cconfig.json").status_code == 404
    assert client.get(f"/api/jobs/{uuid.uuid4().hex}/artifacts/{uuid.uuid4().hex}").status_code == 404
    assert client.post("/api/jobs", json={"type": "convert", "file_ids": ["C:\\Windows\\secret.pdf"]}).status_code == 404
    assert client.post("/api/files", files={"file": ("payload.exe", b"MZ", "application/octet-stream")}).status_code == 415
    # A forged loopback Host from an actual remote peer still requires deployment auth.
    with TestClient(app, base_url="http://127.0.0.1:8765", client=("203.0.113.5", 50001)) as remote:
        assert remote.get("/api/config").status_code == 403


def test_deployment_refuses_missing_token_and_requires_bearer(tmp_path, monkeypatch):
    monkeypatch.delenv("LINEAFORGE_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="LINEAFORGE_API_TOKEN"):
        create_app(tmp_path / "blocked", deployment=True)
    token = "test-deployment-secret-0123456789"
    monkeypatch.setenv("LINEAFORGE_API_TOKEN", token)
    deployed = create_app(tmp_path / "hosted", deployment=True)
    with TestClient(deployed, base_url="https://lineaforge.example") as remote:
        assert remote.get("/api/health").status_code == 401
        assert remote.get("/api/config", headers={"Authorization": "Bearer wrong"}).status_code == 401
        response = remote.get("/api/health", headers={"Authorization": "Bearer " + token})
        assert response.status_code == 200 and response.json()["mode"] == "deployment"


def test_local_model_test_never_contacts_remote_endpoint(client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Local model test must not contact a remote endpoint")
    monkeypatch.setattr("httpx.AsyncClient.get", forbidden)
    assert client.put("/api/config", json={"models": {"vlm": {"mode": "local", "base_url": "https://example.com/v1", "model": "vision"}}}).status_code == 200
    response = client.post("/api/models/test", json={"kind": "vlm"})
    assert response.status_code == 200 and response.json()["ok"] is False
    assert "loopback" in response.json()["message"]


def test_cad_probe_normalizes_success_for_the_frontend(client):
    response = client.post("/api/cad/test", json={"backend": "ezdxf"})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["connected"] is True
    assert response.json()["message"]


@pytest.mark.skipif(os.name != "nt", reason="Windows UNC path handling")
def test_static_files_reject_unc_before_network_path_resolution(tmp_path,monkeypatch):
    from starlette.staticfiles import StaticFiles
    directory=tmp_path / "frontend"
    directory.mkdir()
    (directory / "index.html").write_text("<!doctype html>")
    files=StaticFiles(directory=directory)
    realpath=os.path.realpath
    def confined(path,*args,**kwargs):
        assert not str(path).startswith("\\\\"), "An untrusted UNC path must never reach Windows path resolution"
        return realpath(path,*args,**kwargs)
    monkeypatch.setattr(os.path,"realpath",confined)
    _,metadata=files.lookup_path("\\\\attacker.invalid\\share\\private.txt")
    assert metadata is None


def graph_fixture():
    return PidGraph(page_id="page-1", width_px=200, height_px=100, width_mm=100, height_mm=50,
        elements=[Element(id="text-1", kind="text", tag="UNREADABLE", bbox=[10, 10, 30, 20], confidence=.1,
                          tile_id="test-tile", source_agent="recorded-test", status="UNREADABLE")],
        review=[ReviewItem(id="review-1", element_id="text-1", reason="Verify source text")])


def test_unreadable_review_is_transactional_and_cad_requires_resolved_findings(client, app, monkeypatch):
    # Inject a graph contract to exercise review logic without claiming VLM accuracy.
    import lineaforge.agentic as actual_agentic
    module = types.ModuleType("lineaforge.agentic")
    module.AGENT_CATALOG = []
    def recorded(source, config, artifact_dir, **kwargs):
        graph = graph_fixture()
        return PipelineResult(graph=graph, review=graph.review, artifact_dir=str(artifact_dir), status="NEEDS_REVIEW")
    module.run_agentic_job = recorded
    monkeypatch.setitem(sys.modules, "lineaforge.agentic", module)
    source = upload(client)
    response = client.post("/api/jobs", json={"type": "pid", "file_ids": [source["id"]]})
    job = finish(client, response.json()["id"])
    assert job["status"] == "needs_review"
    cad_request = {"type": "cad", "options": {"source_job_id": job["id"], "backend": "ezdxf"}}
    assert client.post("/api/jobs", json=cad_request).status_code == 400
    before = client.get(f"/api/jobs/{job['id']}/graph").json()
    response = client.post(f"/api/jobs/{job['id']}/review", json={"element_id": "text-1", "action": "accept"})
    assert response.status_code == 400
    assert client.get(f"/api/jobs/{job['id']}/graph").json() == before
    bypass = json.loads(json.dumps(before))
    bypass["elements"][0]["status"] = "ACCEPTED"
    assert client.put(f"/api/jobs/{job['id']}/graph", json=bypass).status_code == 400
    assert client.get(f"/api/jobs/{job['id']}/graph").json() == before
    bad = client.post(f"/api/jobs/{job['id']}/review", json={"element_id": "text-1", "action": "edit", "updates": {"bbox": [100, 100, 500, 400], "tag": "PT-101"}})
    assert bad.status_code == 422
    assert client.get(f"/api/jobs/{job['id']}/graph").json() == before
    edited = client.post(f"/api/jobs/{job['id']}/review", json={"element_id": "text-1", "review_id": "review-1", "action": "edit", "updates": {"tag": "PT-101", "text": "PT-101"}})
    assert edited.status_code == 200
    assert edited.json()["elements"][0]["status"] == "ACCEPTED"
    assert edited.json()["review"][0]["status"] == "ACCEPTED"
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "completed"
    reviewed = client.get(f"/api/jobs/{job['id']}").json()
    for name in ("pid_graph.json", "tags.csv", "review_report.json", "review_audit.jsonl"):
        artifact = next(item for item in reviewed["artifacts"] if item["name"] == name)
        expected = b"operator" if name == "review_audit.jsonl" else b"ACCEPTED" if name == "review_report.json" else b"PT-101"
        assert expected in client.get(artifact["url"]).content
    changed = client.get(f"/api/jobs/{job['id']}/graph").json()
    changed["width_px"] = 300
    assert client.put(f"/api/jobs/{job['id']}/graph", json=changed).status_code == 400
    # The reviewed graph now runs through the real offline CAD writer and QA.
    monkeypatch.setitem(sys.modules, "lineaforge.agentic", actual_agentic)
    generated = client.post("/api/jobs", json=cad_request)
    assert generated.status_code == 202
    cad_job = finish(client, generated.json()["id"])
    assert cad_job["status"] == "needs_review", cad_job
    assert cad_job["result"]["qa"]["structural"]["structural_traceability_pass"] is True
    assert cad_job["result"]["qa"]["visual_fidelity_pass"] is False
    snapshot = next(item for item in cad_job["artifacts"] if item["name"] == "reviewed_graph.json")
    assert client.get(snapshot["url"]).json()["elements"][0]["tag"] == "PT-101"
    assert len(cad_job["result"]["reviewed_graph_sha256"]) == 64
    dxf = next(item for item in cad_job["artifacts"] if item["kind"] == "dxf")
    assert client.get(dxf["url"]).status_code == 200

    # Queue an approved copy, then edit its source; the queued snapshot remains stable.
    monkeypatch.setattr(app.state.manager.executor, "submit", lambda *args, **kwargs: None)
    queued = client.post("/api/jobs", json=cad_request)
    assert queued.status_code == 202
    changed = client.post(f"/api/jobs/{job['id']}/review", json={"element_id":"text-1", "action":"edit", "updates":{"tag":"PT-999","text":"PT-999"}})
    assert changed.status_code == 200
    queued_copy = app.state.manager._job_dir(queued.json()["id"]) / "artifacts" / "reviewed_graph.json"
    assert json.loads(queued_copy.read_text())["elements"][0]["tag"] == "PT-101"
    assert app.state.manager.graph(job["id"]).elements[0].tag == "PT-999"


def test_cloud_drawing_requires_job_consent(client):
    source = upload(client)
    client.put("/api/config", json={"models": {"vlm": {"mode": "cloud", "base_url": "https://example.com/v1", "model": "vision"}}})
    response = client.post("/api/jobs", json={"type": "pid", "file_ids": [source["id"]]})
    assert response.status_code == 403
    assert client.get("/api/jobs").json()["jobs"] == []


def test_cancel_checks_cooperatively_and_retry_uses_new_job(client, monkeypatch):
    entered = threading.Event()
    def slow(source, destination, config, progress, cancel, **options):
        entered.set()
        while not cancel.wait(.01):
            pass
        core._check_cancel(cancel)
    monkeypatch.setattr(core, "convert_file", slow)
    source = upload(client)
    response = client.post("/api/jobs", json={"type": "convert", "file_ids": [source["id"]]})
    job_id = response.json()["id"]
    assert entered.wait(2)
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    assert finish(client, job_id)["status"] == "cancelled"
    retry = client.post(f"/api/jobs/{job_id}/retry")
    assert retry.status_code == 202 and retry.json()["id"] != job_id
    client.post(f"/api/jobs/{retry.json()['id']}/cancel")
    assert finish(client, retry.json()["id"])["status"] == "cancelled"


def test_real_http_model_routes_agent_pipeline_review_and_cad(client):
    """Recorded HTTP responses test orchestration, never recognition accuracy."""
    from lineaforge.agentic import AGENT_CATALOG
    calls = []
    lock = threading.Lock()
    class RecordedModelServer(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value):
            payload = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            assert self.path == "/v1/models"
            self.reply({"data": [{"id": "recorded-vlm"}, {"id": "recorded-llm"}]})

        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            content = request["messages"][1]["content"]
            prompt = content[0]["text"]
            schema = json.loads(prompt.rsplit("\nReturn JSON conforming exactly to this schema:\n", 1)[1])
            title = schema["title"]
            with lock:
                calls.append({"model": request["model"], "schema": title, "images": sum(item["type"] == "image_url" for item in content)})
            responses = {
                "LayoutPlan": {"page_class": "P&ID", "regions": {}, "observations": ["Recorded test sheet"]},
                "StandardProfile": {"observations": [], "symbol_conventions": [], "tag_patterns": [], "line_number_fields": []},
                "DenoisePlan": {"regions": [], "deskew_degrees": 0, "rationale": "Recorded identity plan"},
                "LossCheck": {"content_lost": False, "details": []},
                "Extraction": {"elements": [], "edges": []},
                "Findings": {"findings": []},
                "QaResult": {"discrepancies": ["Recorded response requires human confirmation"], "needs_human": True},
                "BuildPlan": {"observations": ["Recorded CAD plan"], "missing_geometry_ids": [], "needs_human": True},
                "RepairProposals": {"proposals": []},
            }
            result = responses[title]
            if title == "Extraction" and prompt.startswith("You are Tag & text reader "):
                item = Element(id="label-1", kind="text", subclass="tag", tag="PT-101", text="PT-101", bbox=[50, 50, 120, 80],
                    confidence=.8, tile_id="recorded-tile", source_agent="recorded-http")
                result = {"elements": [item.model_dump()], "edges": []}
            self.reply({"choices": [{"message": {"content": json.dumps(result)}}]})

    server = ThreadingHTTPServer(("127.0.0.1", 0), RecordedModelServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/v1"
        settings = {"models": {"vlm": {"base_url": endpoint, "model": "recorded-vlm"}, "llm": {"base_url": endpoint, "model": "recorded-llm"}},
                    "agents": {"enabled_roles": [item["id"] for item in AGENT_CATALOG], "max_calls": 100, "max_seconds": 90, "max_estimated_cost": 3}}
        assert client.put("/api/config", json=settings).status_code == 200
        for kind in ("vlm", "llm"):
            assert client.post("/api/models/test", json={"kind": kind}).json()["ok"] is True
        source = upload(client)
        response = client.post("/api/jobs", json={"type": "pid", "file_ids": [source["id"]]})
        assert response.status_code == 202
        job = finish(client, response.json()["id"])
        assert job["status"] == "needs_review", job
        graph = client.get(f"/api/jobs/{job['id']}/graph").json()
        assert len(graph["elements"]) == 1 and graph["review"]
        roles = {item["agent"] for item in job["events"]}
        assert {"intake", "equipment", "piping", "tag_ocr", "verifier", "repair", "cad_planner", "release_gate"}.issubset(roles)
        assert any(item["model"] == "recorded-vlm" and item["images"] > 0 for item in calls)
        assert any(item["model"] == "recorded-llm" and item["images"] == 0 for item in calls)
        summary = next(item for item in job["artifacts"] if item["name"] == "run_summary.json")
        assert client.get(summary["url"]).json()["recognition_accuracy_measured"] is False
        element = graph["elements"][0]
        assert client.post(f"/api/jobs/{job['id']}/review", json={"element_id": element["id"], "action": "accept"}).status_code == 200
        for finding in graph["review"]:
            assert client.post(f"/api/jobs/{job['id']}/review", json={"review_id": finding["id"], "action": "accept"}).status_code == 200
        created = client.post("/api/jobs", json={"type": "cad", "options": {"source_job_id": job["id"], "backend": "ezdxf"}})
        assert created.status_code == 202
        built = finish(client, created.json()["id"])
        assert built["status"] == "needs_review", built
        assert built["result"]["qa"]["structural"]["structural_traceability_pass"] is True
        assert built["result"]["qa"]["visual"]["compared_tiles"] == 1
        assert built["result"]["qa"]["visual_fidelity_pass"] is False
        assert any(item["name"].endswith("build_verification.json") for item in built["artifacts"])
        assert any(item["kind"] == "dxf" for item in built["artifacts"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
