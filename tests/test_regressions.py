"""Regression evidence for defects found after the first application build.

Recorded responses and mocked HTTP envelopes exercise contracts, not recognition
accuracy. The CAD QA test uses a real DXF and measures the resulting vector PDF.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import ezdxf
import fitz
import pytest
from pydantic import ValidationError

from lineaforge import batch, providers
from lineaforge.cad import EzdxfBackend, build_graph_cad
from lineaforge.cad_qa import run_cad_qa
from lineaforge.core import PT_TO_MM
from lineaforge.evaluation import evaluate
from lineaforge.models import Element, PidGraph, PipelineSettings, ReviewItem
from lineaforge.pipeline import Pipeline, PipelineError, deduplicate, export_graph
from lineaforge.providers import EndpointProvider, RecordedProvider


def element(identifier="e", **updates):
    fields = dict(id=identifier, kind="instrument", tag="TG-1", bbox=[10, 10, 30, 30],
                  confidence=.99, tile_id="recorded", source_agent="recorded")
    fields.update(updates)
    return Element(**fields)


def graph(elements, **updates):
    fields = dict(page_id="recorded-sheet", width_px=250, height_px=250,
                  width_mm=60, height_mm=60, elements=elements)
    fields.update(updates)
    return PidGraph(**fields)


@pytest.fixture
def source_pdf(tmp_path):
    source = tmp_path / "recorded.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=60, height=60)
        page.draw_line((5, 5), (50, 50))
        doc.save(source)
    return source


def recorded_responses(symbols=None):
    return {
        "layout": {"page_class": "P&ID", "regions": {}, "observations": []},
        "denoise": {"regions": [], "deskew_degrees": 0, "rationale": "Recorded no-noise decision"},
        "loss_check": {"content_lost": False, "details": []},
        "symbols": {"elements": symbols or [], "edges": []},
        "tags": {"elements": [], "edges": []},
        "connectivity": {"elements": [], "edges": []},
        "verify": {"discrepancies": [], "needs_human": True},
    }


def test_batch_rejects_outputs_that_are_other_inputs_before_work(tmp_path, monkeypatch):
    first, second = tmp_path / "source.pdf", tmp_path / "source.dxf"
    first.write_bytes(b"original PDF input")
    second.write_bytes(b"original CAD input")
    calls = []
    monkeypatch.setattr(batch, "convert_file", lambda *a, **k: calls.append(a))
    items = [batch.BatchItem(first, second), batch.BatchItem(second, tmp_path / "plot.pdf")]
    with pytest.raises(ValueError, match="input"):
        batch.run_batch(items, workers=2)
    assert calls == []
    assert first.read_bytes() == b"original PDF input"
    assert second.read_bytes() == b"original CAD input"


def test_region_connectivity_ignores_unannotated_outside_edges():
    gold = {"scope": "regions", "evaluation_regions": [[0, 0, 30, 30]],
            "elements": [{"id": "g1", "kind": "instrument", "bbox": [0, 0, 5, 5]},
                         {"id": "g2", "kind": "instrument", "bbox": [10, 0, 15, 5]}],
            "connections": [["g1", "g2"]]}
    prediction = {"elements": [
        {"id": "p1", "kind": "instrument", "bbox": [0, 0, 5, 5]},
        {"id": "p2", "kind": "instrument", "bbox": [10, 0, 15, 5]},
        {"id": "outside1", "kind": "instrument", "bbox": [100, 100, 105, 105]},
        {"id": "outside2", "kind": "instrument", "bbox": [110, 100, 115, 105]}],
        "connections": [["p1", "p2"], ["outside1", "outside2"], ["p1", "outside1"]]}
    result = evaluate(gold, prediction)
    assert result["predicted_symbols"] == 2
    assert result["symbol_precision"] == result["symbol_recall"] == 1
    assert result["connectivity_f1"] == 1


def test_csv_escapes_source_names_tags_and_connection_ids(tmp_path):
    target = element("=2+2", tag=None)
    tagged = element("normal", tag="  @SUM(1,1)", connected_to=[target.id])
    export_graph(graph([tagged, target], page_id="=1+1"), tmp_path)
    with (tmp_path / "tags.csv").open(encoding="utf-8-sig", newline="") as stream:
        row = list(csv.reader(stream))[1]
    assert row[0] == "'  @SUM(1,1)"
    assert row[2] == "'=1+1"
    assert row[4] == "'=2+2"


def test_long_review_report_has_every_finding_and_no_blank_pages(tmp_path):
    findings = [ReviewItem(id=f"issue-{i}", reason=(
        "Inspect the confirmed source geometry, small instrument text and pipe endpoint. " * 12
        + f"FINDING_{i:03}_END")) for i in range(45)]
    export_graph(graph([], review=findings), tmp_path)
    with fitz.open(tmp_path / "review_report.pdf") as report:
        texts = [page.get_text() for page in report]
    assert all(text.strip() for text in texts)
    complete_text = "\n".join(texts)
    assert all(f"FINDING_{i:03}_END" in complete_text for i in range(45))


def test_rejected_assignments_restore_entire_previous_record():
    record = element()
    original = record.model_dump()
    with pytest.raises(ValidationError):
        record.bbox = [-1, 0, 30, 30]
    assert record.model_dump() == original
    with pytest.raises(ValidationError):
        record.status = "UNREADABLE"
    assert record.model_dump() == original
    container = graph([record])
    original_graph = container.model_dump()
    with pytest.raises(ValidationError):
        container.elements = [element(connected_to=["missing"])]
    assert container.model_dump() == original_graph


def test_overlap_dedup_retains_both_connections_and_parent_alias():
    equipment = element("R1", kind="equipment", tag="R-1", connected_to=["left"])
    duplicate = element("R2", kind="equipment", tag="R-1", confidence=.9, connected_to=["right"])
    instrument = element("I", bbox=[40, 40, 60, 60], parent_equipment="R2")
    kept, aliases = deduplicate([equipment, duplicate, instrument])
    assert len(kept) == 2 and aliases["R2"] == "R1"
    assert set(next(e for e in kept if e.id == "R1").connected_to) == {"left", "right"}
    assert next(e for e in kept if e.id == "I").parent_equipment == "R1"


def test_same_bounds_crossing_lines_remain_separate():
    down = element("down", kind="pipe", tag=None, bbox=[0, 0, 100, 100], points=[[0, 0], [100, 100]])
    up = element("up", kind="pipe", tag=None, bbox=[0, 0, 100, 100], points=[[0, 100], [100, 0]])
    kept, aliases = deduplicate([down, up])
    assert {e.id for e in kept} == {"down", "up"}
    assert aliases == {"down": "down", "up": "up"}


def test_tile_parent_and_reused_agent_edge_ids_resolve(source_pdf, tmp_path):
    equipment = element("R", kind="equipment", tag="R-1", connected_to=["I"])
    instrument = element("I", bbox=[40, 40, 60, 60], parent_equipment="R", connected_to=["R"])
    text = element("T", kind="text", tag=None, text="R-1", bbox=[70, 70, 90, 90])
    pipe = element("P", kind="pipe", tag=None, bbox=[100, 100, 140, 140], points=[[100, 100], [140, 140]],
                   connected_to=["R", "I"], attributes={"line_number": "P-01-001-25-D1"})
    data = recorded_responses([equipment.model_dump(), instrument.model_dump()])
    data["symbols"]["edges"] = [{"id": "edge1", "from_id": "R", "to_id": "I"}]
    data["tags"] = {"elements": [text.model_dump()], "edges": [{"id": "edge1", "from_id": "I", "to_id": "T"}]}
    data["connectivity"] = {"elements": [pipe.model_dump()], "edges": [{"id": "edge1", "from_id": "P", "to_id": "R"}]}
    result = Pipeline(RecordedProvider(data), PipelineSettings(tile_size=256), tmp_path / "run").run(source_pdf)
    assert len(result.graph.edges) == 3
    assert len({e.id for e in result.graph.edges}) == 3
    child = next(e for e in result.graph.elements if e.id.endswith(":I"))
    parent = next(e for e in result.graph.elements if e.id.endswith(":R"))
    assert child.parent_equipment == parent.id


def test_outside_tile_geometry_is_rejected_without_clamping(source_pdf, tmp_path):
    bad = element(points=[[10, 10], [500, 10]])
    root = tmp_path / "out-of-bounds"
    with pytest.raises(PipelineError, match="geometry outside tile"):
        Pipeline(RecordedProvider(recorded_responses([bad.model_dump()])),
                 PipelineSettings(tile_size=256), root).run(source_pdf)
    assert not (root / "pid_graph.json").exists()


def test_unknown_parent_and_outside_page_points_fail_contract():
    with pytest.raises(ValidationError, match="parent equipment"):
        graph([element(parent_equipment="missing")])
    with pytest.raises(ValidationError, match="outside the page"):
        graph([element(points=[[10, 10], [251, 10]])])


def test_malformed_endpoint_content_uses_bounded_logged_retries(source_pdf, tmp_path, monkeypatch):
    attempts = []
    class MalformedResponse:
        def raise_for_status(self):
            pass
        def json(self):
            return {"choices": [{"message": {"content": None}}]}
    class MockClient:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, **kwargs):
            attempts.append(url)
            return MalformedResponse()
    monkeypatch.setattr(providers.httpx, "Client", MockClient)
    root = tmp_path / "malformed"
    provider = EndpointProvider("http://127.0.0.1:8000/v1", "mock-envelope", artifact_dir=root)
    with pytest.raises(PipelineError, match="after retries"):
        Pipeline(provider, PipelineSettings(retries=1), root).run(source_pdf)
    records = [json.loads(line) for line in (root / "agents.jsonl").read_text().splitlines()]
    assert len(attempts) == len(records) == 2
    assert all(record["status"] == "failed" for record in records)
    assert not (root / "pid_graph.json").exists()


def test_real_dxf_cad_qa_checks_source_ids_and_exact_physical_grid(tmp_path):
    line = element("source-pipe-001", kind="pipe", tag=None, bbox=[100, 100, 900, 100],
                   points=[[100, 100], [900, 100]], status="ACCEPTED")
    rejected = element("rejected-source", status="REJECTED")
    source_graph = graph([line, rejected], width_px=1000, height_px=700, width_mm=1000, height_mm=700)
    dxf = tmp_path / "literal.dxf"
    build_graph_cad(source_graph, EzdxfBackend(), dxf)
    report = run_cad_qa(source_graph, dxf, tmp_path / "cad-qa")
    assert report["reopened"] is True
    assert report["expected_graph_source_ids"] == report["reopened_source_ids"] == 1
    assert report["missing_source_ids"] == []
    assert report["structural_traceability_pass"] is True
    assert report["visual_fidelity_pass"] is False and report["status"] == "NEEDS_HUMAN"
    assert report["vlm_compared_tiles"] == 0
    with fitz.open(report["render"]) as rendered:
        page = rendered[0]
        assert page.rect.width * PT_TO_MM == pytest.approx(1000, abs=.001)
        assert page.rect.height * PT_TO_MM == pytest.approx(700, abs=.001)
        strokes = [d for d in page.get_drawings() if d["type"] == "s"]
        assert len(strokes) == 1
        assert strokes[0]["rect"].width * PT_TO_MM == pytest.approx(800, abs=.002)
        assert strokes[0]["rect"].x0 * PT_TO_MM == pytest.approx(100, abs=.002)
        assert strokes[0]["rect"].y0 * PT_TO_MM == pytest.approx(100, abs=.002)
    # Removing provenance must fail structural traceability without pretending
    # that identical-looking geometry is sufficient evidence.
    damaged = ezdxf.readfile(dxf)
    for entity in damaged.modelspace():
        if entity.has_xdata("LINEAFORGE"):
            entity.discard_xdata("LINEAFORGE")
    damaged.saveas(dxf)
    missing = run_cad_qa(source_graph, dxf, tmp_path / "missing-provenance")
    assert missing["structural_traceability_pass"] is False
    assert missing["missing_source_ids"] == [line.id]
