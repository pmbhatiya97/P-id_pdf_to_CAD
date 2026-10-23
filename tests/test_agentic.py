"""Specialist orchestration contracts. Recorded responses do not measure AI accuracy."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
from threading import Event
from types import SimpleNamespace

import fitz
from PIL import Image
import pytest

from lineaforge import agentic
from lineaforge.agentic import (AGENT_CATALOG, AgentRuntime, DEFAULT_ROLES, Findings,
    StandardProfile, _canonicalize, _clean_tile, run_agentic_job)
from lineaforge.models import Element, Extraction, LayoutPlan, PidGraph
from lineaforge.pipeline import PipelineError
from lineaforge.providers import PrivacyError, RecordedProvider


def item(identifier="unit", kind="equipment", **updates):
    return {"id": identifier, "kind": kind, "subclass": "vessel", "tag": "V-1",
        "bbox": [20, 20, 60, 60], "points": [[20,20],[60,20],[60,60],[20,60]],
        "confidence": .99, "tile_id": "model-local", "source_agent": "model-local", **updates}


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "tiny.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=60, height=40)
        page.draw_rect(fitz.Rect(5, 5, 15, 15), color=(0, 0, 0))
        page.draw_line(fitz.Point(15, 10), fitz.Point(50, 10), color=(0, 0, 0))
        page.draw_circle(fitz.Point(24, 15), 2, color=(0, 0, 0))
        doc.save(path)
    return path


def config(**options):
    return {"agents": {"enabled_roles": DEFAULT_ROLES, "max_calls": 100,
            "max_seconds": 300, "retries": 0, "max_parallel": 4, **options},
            "pipeline": {"dpi": 300, "tile_size": 256, "overlap": .2}}


def responses():
    values = {role: {"elements": [], "edges": []} for role in agentic.EXTRACTION_INSTRUCTIONS}
    values.update({
        "intake": {"page_class": "P&ID"}, "layout": {"page_class": "P&ID"},
        "legend": {"observations": ["Recorded contract test"]},
        "denoise": {"regions": [], "rationale": "No noise in the recorded fixture"},
        "content_loss": {"content_lost": False},
        "equipment": {"elements": [item(connected_to=["piping:run"])], "edges": []},
        "instruments": {"elements": [item("bubble", "instrument", subclass="unknown", tag="UNREADABLE",
            bbox=[90,50,110,70], points=[], confidence=.2, status="UNREADABLE")], "edges": []},
        "piping": {"elements": [item("run", "pipe", subclass="process", tag=None,
            bbox=[60,35,200,45], points=[[60,40],[200,40]], connected_to=["equipment:unit"],
            attributes={"line_number": "P-011-002-25-T1"})], "edges": []},
        "connectivity": {"elements": [], "edges": [{"id": "connection", "from_id": "equipment:unit", "to_id": "piping:run"}]},
        "topology_qa": {"findings": []}, "tag_qa": {"findings": []}, "line_qa": {"findings": []},
        "verifier": {"needs_human": False, "discrepancies": []},
        "cad_planner": {"needs_human": False, "missing_geometry_ids": []},
        "repair": {"proposals": [{"element_id": "tile-0000:instruments:bubble",
            "proposed_updates": {"tag": "FORBIDDEN-AUTO-APPLY"}, "evidence": "Recorded proposal only"}]},
    })
    return values


def test_all_specialists_trace_graph_and_cache_without_network(source, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Recorded orchestration must not connect to any network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    provider = RecordedProvider(responses())
    root = tmp_path / "run"
    result = run_agentic_job(source, config(), root, providers={"vlm": provider, "llm": provider})
    assert result.status == "NEEDS_REVIEW" and len(result.graph.elements) == 3
    assert len(result.graph.edges) == 1
    edge = result.graph.edges[0]
    assert edge.from_id == "tile-0000:equipment:unit" and edge.to_id == "tile-0000:piping:run"
    assert result.graph.elements[0].status != "ACCEPTED"
    instrument = next(e for e in result.graph.elements if e.kind == "instrument")
    assert instrument.status == "UNREADABLE" and instrument.tag == "UNREADABLE"
    assert all(review.status == "NEEDS_HUMAN" for review in result.review)
    assert any("visual verifier" in review.reason for review in result.review)
    summary = json.loads((root / "run_summary.json").read_text())
    assert summary["recognition_accuracy_measured"] is False and summary["review_required"] is True
    assert set(provider.calls) >= {"intake", "layout", "equipment", "valves", "instruments", "inline_items",
        "tag_ocr", "line_numbers", "notes_tables", "piping", "signals", "connectivity", "offpage", "verifier", "cad_planner"}
    assert not (root / "transmission.jsonl").exists()
    assert (root / "review_report.pdf").is_file()
    with fitz.open(source) as original:
        actual = original[0].get_pixmap(matrix=fitz.Matrix(300/72,300/72),alpha=False)
        assert (result.graph.width_px,result.graph.height_px) == (actual.width,actual.height)
    for index, (x0,y0,x1,y1) in enumerate(json.loads((root / "tile_plan.json").read_text())):
        with Image.open(root / "tiles" / f"tile-{index:04}" / "source.png") as raster:
            assert raster.size == (x1-x0,y1-y0)
    count = len(provider.calls)
    run_agentic_job(source, config(), root, providers={"vlm": provider, "llm": provider})
    assert len(provider.calls) == count


def test_verifier_renders_fresh_600dpi_source_and_keeps_cleaned_context(source, tmp_path):
    class InspectProvider(RecordedProvider):
        def complete(self, task, prompt, images, schema):
            if task == "verifier":
                assert len(images) == 2
                assert images[0].parent.name == "verifier" and images[1].name == "cleaned.png"
                metadata = json.loads(images[0].with_suffix(".json").read_text())
                assert metadata["render_dpi"] == 600 and metadata["source_dpi"] == 300
                assert metadata["pixel_scale"] == 2
                with Image.open(images[0]) as detail:
                    assert detail.width > 2 * 20 and detail.height > 2 * 20
                    assert abs(detail.info["dpi"][0] - 600) < 1
                assert "fresh 600-DPI" in prompt
            return super().complete(task, prompt, images, schema)
    provider = InspectProvider(responses())
    result = run_agentic_job(source, config(), tmp_path / "run", providers={"vlm": provider, "llm": provider})
    assert any(Path(review.evidence).parent.name == "verifier" for review in result.review if review.evidence)


@pytest.mark.parametrize("status", ["ACCEPTED", "REJECTED"])
def test_models_cannot_accept_or_reject_their_own_extraction(tmp_path, status):
    image = tmp_path / "source.png"
    Image.new("RGB", (100, 100), "white").save(image)
    extraction = Extraction(elements=[Element.model_validate(item(status=status))])
    with pytest.raises(PipelineError, match="human review status"):
        _canonicalize("equipment", extraction, image, 0, 0, 100, 100, "tile-0000")


def test_untrusted_schema_values_never_appear_in_retry_logs(tmp_path):
    secret = "sensitive-source-value-DO-NOT-LOG"
    provider = RecordedProvider({"layout": [{"page_class": secret}, {"page_class": "P&ID"}]})
    runtime = AgentRuntime(config(retries=1), tmp_path / "run", providers={"vlm": provider})
    result = runtime.call("layout", "layout", [], LayoutPlan, "Read layout")
    assert result.page_class == "P&ID" and runtime.calls == 2
    log = (runtime.root / "agent_events.jsonl").read_text()
    assert "page_class" in log and "literal_error" in log and secret not in log


def test_cloud_denial_precedes_image_reads_and_model_calls(tmp_path):
    provider = RecordedProvider({"layout": {"page_class": "P&ID"}})
    provider.mode = "cloud"
    runtime = AgentRuntime(config(), tmp_path / "run", providers={"vlm": provider})
    with pytest.raises(PrivacyError, match="authorized"):
        runtime.call("layout", "layout", [tmp_path / "not-read.png"], LayoutPlan, "Read layout")
    assert runtime.calls == 0 and provider.calls == []
    assert not (runtime.root / "agent_events.jsonl").exists()


@pytest.mark.parametrize("mode,expected_images", [("local", 2), ("cloud", 1)])
def test_private_reference_catalog_is_withheld_from_external_models(source, tmp_path, monkeypatch, mode, expected_images):
    references = tmp_path / "catalog-root" / "docs" / "dataset_crops"
    references.mkdir(parents=True)
    Image.new("RGB", (8,8), "white").save(references / "private-test-reference.png")
    monkeypatch.setattr(agentic, "__file__", str(tmp_path / "catalog-root" / "lineaforge" / "agentic.py"))
    class InspectProvider(RecordedProvider):
        def complete(self, task, prompt, images, schema):
            if task == "legend":
                assert len(images) == expected_images
                if mode == "cloud":
                    assert all("private-test-reference" not in str(path) for path in images)
            return super().complete(task, prompt, images, schema)
    provider = InspectProvider(responses())
    provider.mode = mode
    root = tmp_path / ("run-" + mode)
    run_agentic_job(source, config(), root, cloud_consent=True, providers={"vlm": provider, "llm": provider})
    assert ("catalog withheld" in (root / "agent_events.jsonl").read_text()) == (mode == "cloud")


def test_vlm_roles_cannot_be_routed_to_text_only_provider(tmp_path):
    runtime = AgentRuntime(config(assignments={"equipment": "llm"}), tmp_path / "run")
    with pytest.raises(PipelineError, match="requires the VLM"):
        runtime.provider("equipment")


def test_required_gate_roles_cannot_be_disabled(tmp_path):
    with pytest.raises(PipelineError, match="cannot be disabled"):
        AgentRuntime(config(enabled_roles=[r for r in DEFAULT_ROLES if r != "release_gate"]), tmp_path / "run")


def test_parallel_model_budget_is_shared_and_atomic(tmp_path):
    provider = RecordedProvider({"equipment": {}, "valves": {}, "instruments": {}, "inline_items": {}})
    runtime = AgentRuntime(config(max_calls=2), tmp_path / "run", providers={"vlm": provider})
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(runtime.call, role, role, [], Findings, "Return findings")
                   for role in ("equipment", "valves", "instruments", "inline_items")]
        results = []
        for future in futures:
            try:
                results.append(future.result())
            except PipelineError as error:
                assert "budget exhausted" in str(error)
    assert len(results) == runtime.calls == len(provider.calls) == 2


def test_cancellation_during_model_call_does_not_accept_or_cache_response(tmp_path):
    cancel = Event()
    class CancellingProvider(RecordedProvider):
        def complete(self, task, prompt, images, schema):
            cancel.set()
            return super().complete(task, prompt, images, schema)
    provider = CancellingProvider({"layout": {"page_class": "P&ID"}})
    runtime = AgentRuntime(config(), tmp_path / "run", cancel=cancel, providers={"vlm": provider})
    with pytest.raises(PipelineError, match="cancelled"):
        runtime.call("layout", "layout", [], LayoutPlan, "Read layout")
    assert not list((runtime.root / "cache").glob("*.json"))
    assert not list((runtime.root / "agents").glob("*.json"))


def test_elapsed_budget_after_provider_returns_does_not_accept_response(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(agentic.time, "monotonic", lambda: clock[0])
    class LateProvider(RecordedProvider):
        def complete(self, task, prompt, images, schema):
            clock[0] = 11.0
            return super().complete(task, prompt, images, schema)
    runtime = AgentRuntime(config(max_seconds=10), tmp_path / "run",
        providers={"vlm": LateProvider({"layout": {"page_class": "P&ID"}})})
    with pytest.raises(PipelineError, match="latency budget exhausted"):
        runtime.call("layout", "layout", [], LayoutPlan, "Read layout")
    assert not list((runtime.root / "cache").glob("*.json"))


def test_rejected_denoise_keeps_pixels_and_records_loss(tmp_path):
    image = tmp_path / "source.png"
    Image.new("RGB", (100, 100), "black").save(image)
    provider = RecordedProvider({"denoise": {"regions": [{"bbox": [10,10,20,20], "label": "stain",
        "action": "remove", "reason": "Recorded candidate"}], "rationale": "Test only"},
        "content_loss": {"content_lost": True, "details": ["May remove a thin pipe"]}})
    runtime = AgentRuntime(config(), tmp_path / "run", providers={"vlm": provider})
    cleaned, warnings = _clean_tile(runtime, image, "tile-0000")
    with Image.open(image) as original, Image.open(cleaned) as final:
        assert final.tobytes() == original.tobytes()
    assert any("Rejected denoise" in warning for warning in warnings)
    assert json.loads(image.with_name("loss_check.json").read_text())["content_lost"] is True


def test_out_of_tile_geometry_is_rejected_without_clamping(tmp_path):
    image = tmp_path / "source.png"
    Image.new("RGB", (100, 100), "white").save(image)
    extraction = Extraction(elements=[Element.model_validate(item(bbox=[20,20,150,50]))])
    with pytest.raises(PipelineError, match="outside tile"):
        _canonicalize("equipment", extraction, image, 0, 0, 100, 100, "tile-0000")


def test_repairs_remain_unapplied_proposals(source, tmp_path):
    provider = RecordedProvider(responses())
    result = run_agentic_job(source, config(enabled_roles=[*DEFAULT_ROLES, "repair"]),
        tmp_path / "run", providers={"vlm": provider, "llm": provider})
    element = next(e for e in result.graph.elements if e.kind == "instrument")
    assert element.tag == "UNREADABLE" and element.status == "UNREADABLE"
    assert "FORBIDDEN-AUTO-APPLY" in element.attributes["repair_proposals"]


def test_empty_model_extraction_requires_human_review(source, tmp_path):
    values = responses()
    for role in agentic.EXTRACTION_INSTRUCTIONS:
        values[role] = {"elements": [], "edges": []}
    provider = RecordedProvider(values)
    result = run_agentic_job(source, config(), tmp_path / "run", providers={"vlm": provider, "llm": provider})
    assert result.graph.elements == [] and result.status == "NEEDS_REVIEW"
    assert any(review.id == "empty-extraction" for review in result.review)


def test_model_context_omits_machine_paths_but_local_provenance_remains(source, tmp_path):
    class Inspect(RecordedProvider):
        def complete(self, task, prompt, images, schema):
            assert "source_crop" not in prompt
            assert str(tmp_path) not in prompt
            assert "cleaned_tile" not in prompt
            if task in agentic.EXTRACTION_INSTRUCTIONS:
                assert len(images) == 2 and images[1].name == "overview.png"
                assert "FIRST image is the target detail tile" in prompt
            return super().complete(task, prompt, images, schema)
    provider = Inspect(responses())
    result = run_agentic_job(source, config(), tmp_path / "private-workspace", providers={"vlm": provider, "llm": provider})
    assert all(Path(element.attributes["source_crop"]).is_file() for element in result.graph.elements)


def test_tile_seams_are_reviewed_without_fabricating_connections():
    element = Element.model_validate(item("run", "pipe", tag=None, bbox=[100,30,256,40],
        points=[[100,35],[256,35]], tile_id="tile-0000"))
    findings = agentic.seam_findings([element], [[0,0,256,200],[204,0,460,200]],460,200)
    assert len(findings) == 1 and findings[0].element_id == "run"
    assert "continuation" in findings[0].reason
    assert element.connected_to == []


def test_operator_can_keep_original_pixels_even_when_model_proposes_removal(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (50,50), "black").save(source)
    values = {"denoise":{"regions":[{"bbox":[1,1,20,20],"label":"stain","action":"remove","reason":"Recorded candidate"}],"rationale":"Test"}}
    provider = RecordedProvider(values)
    settings = config()
    settings["pipeline"]["apply_noise_removal"] = False
    runtime = AgentRuntime(settings,tmp_path / "run",providers={"vlm":provider})
    cleaned, warnings = _clean_tile(runtime,source,"tile-0000")
    with Image.open(cleaned) as image:
        assert image.getpixel((5,5)) == (0,0,0)
    assert provider.calls == ["denoise"] and any("retained original pixels" in warning for warning in warnings)
