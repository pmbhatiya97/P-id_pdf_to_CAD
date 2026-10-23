import json
import socket
from pathlib import Path
from threading import Event
import fitz
import pytest
from PIL import Image
from pydantic import ValidationError
from lineaforge.models import Element, PidGraph, PipelineSettings
from lineaforge.pipeline import Pipeline, PipelineError, deduplicate, tile_plan, parse_line_number
from lineaforge.providers import EndpointProvider, PrivacyError, RecordedProvider
from lineaforge.evaluation import evaluate


def element(**updates):
    return Element(id="e1", kind="instrument", subclass="temperature_gauge", tag="TG-1201",
                   bbox=[25, 25, 55, 55], confidence=.97, tile_id="t1", source_agent="Symbol", **updates)


@pytest.fixture
def sheet(tmp_path):
    path = tmp_path/"tiny.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=60, height=60)
        page.draw_circle((20, 20), 4)
        page.insert_text((10, 40), "TEST", fontsize=5)
        doc.save(path)
    return path


def responses(symbols=None):
    return {"layout": {"page_class": "P&ID", "regions": {}, "observations": []},
            "denoise": {"regions": [], "deskew_degrees": 0, "rationale": "No acquisition noise"},
            "loss_check": {"content_lost": False, "details": []},
            "symbols": {"elements": [element().model_dump()] if symbols is None else symbols, "edges": []},
            "tags": {"elements": [], "edges": []}, "connectivity": {"elements": [], "edges": []},
            "verify": {"discrepancies": ["Confirm unreadable from source"], "needs_human": True}}


def test_local_job_no_network_and_reproducible_artifacts(sheet, tmp_path, monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Network forbidden in recorded-provider contract test")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    provider = RecordedProvider(responses())
    output = tmp_path/"run"
    result = Pipeline(provider, PipelineSettings(tile_size=256), output).run(sheet)
    assert len(result.graph.elements) == 1
    assert calls == []
    assert result.status == "NEEDS_REVIEW"
    assert (output/"review_report.pdf").exists()
    assert "RecordedProvider" in (output/"run_summary.json").read_text()
    assert not (output/"transmission.jsonl").exists()
    # Cached calls do not invoke provider again.
    count = len(provider.calls)
    Pipeline(provider, PipelineSettings(tile_size=256), output).run(sheet)
    assert len(provider.calls) == count


@pytest.mark.parametrize("url", ["https://example.com/v1", "http://localhost:8000/v1", "http://127.0.0.1.example.com", "ftp://127.0.0.1", "http://user:password@127.0.0.1"])
def test_local_refuses_external_or_ambiguous_endpoints(url):
    with pytest.raises(PrivacyError):
        EndpointProvider(url, "test")


def test_cloud_requires_consent_before_reading_or_sending():
    with pytest.raises(PrivacyError):
        EndpointProvider("https://example.com/v1", "test", mode="cloud")


def test_blurred_test_region_unreadable_contract(sheet, tmp_path):
    # Recorded vision response tests propagation, not a claim of blur recognition.
    unreadable = element().model_dump()
    unreadable.update(tag="UNREADABLE", status="UNREADABLE", confidence=.1)
    result = Pipeline(RecordedProvider(responses([unreadable])), PipelineSettings(tile_size=256), tmp_path/"blur").run(sheet)
    assert result.graph.elements[0].tag == "UNREADABLE"
    assert any("UNREADABLE" in r.reason for r in result.review)
    assert result.review[0].status == "NEEDS_HUMAN"
    with pytest.raises(ValidationError):
        Element.model_validate({**unreadable, "tag": "TG-999"})


def test_noise_loss_candidate_rejected(sheet, tmp_path):
    data = responses()
    data["denoise"] = {"regions": [{"bbox": [40, 40, 120, 120], "label": "stain", "action": "remove", "reason": "candidate test"}], "rationale": "test"}
    data["loss_check"] = {"content_lost": True, "details": ["Thin circle stroke removed"]}
    result = Pipeline(RecordedProvider(data), PipelineSettings(tile_size=256), tmp_path/"loss").run(sheet)
    tile = tmp_path/"loss"/"tiles"/"tile-0000"
    with Image.open(tile/"source.png") as before, Image.open(tile/"cleaned.png") as after:
        assert before.tobytes() == after.tobytes()
    assert any("Rejected denoise" in r.reason for r in result.review)


def test_budget_and_cancellation_fail_without_fabricating(sheet, tmp_path):
    with pytest.raises(PipelineError, match="budget exhausted"):
        Pipeline(RecordedProvider(responses()), PipelineSettings(tile_size=256, max_calls=1), tmp_path/"budget").run(sheet)
    cancelled = Event()
    cancelled.set()
    with pytest.raises(PipelineError, match="cancelled"):
        Pipeline(RecordedProvider(responses()), PipelineSettings(tile_size=256), tmp_path/"cancel").run(sheet, cancel=cancelled)


def test_tile_coverage_and_overlap_dedup():
    tiles = tile_plan(4000, 3000)
    assert min(t[0] for t in tiles) == min(t[1] for t in tiles) == 0
    assert max(t[2] for t in tiles) == 4000 and max(t[3] for t in tiles) == 3000
    a = element()
    b = a.model_copy(update={"id": "e2", "tile_id": "other", "bbox": [26, 25, 56, 55], "confidence": .9})
    kept, aliases = deduplicate([b, a])
    assert len(kept) == 1 and aliases["e2"] == "e1"
    assert len(deduplicate([a, b.model_copy(update={"tag": "TG-1202"})])[0]) == 2


def test_line_profile_and_graph_references():
    assert parse_line_number("P-011-002-25-T1")["material"] == "T1"
    assert parse_line_number("P-12-004-50-D1")["sheet"] == "12"
    assert parse_line_number("unknown") is None
    with pytest.raises(ValidationError, match="unknown"):
        PidGraph(page_id="p", width_px=100, height_px=100, width_mm=100, height_mm=100,
                 elements=[element().model_copy(update={"connected_to": ["missing"]})])


def test_metrics_count_misses_and_wrong_tags():
    gold = {"elements": [{"id":"g1","kind":"instrument","tag":"TG-1","bbox":[0,0,10,10]},
                          {"id":"g2","kind":"equipment","tag":"P-2","bbox":[20,20,30,30]}], "connections":[["g1","g2"]]}
    prediction = {"elements": [{"id":"p1","kind":"instrument","tag":"TG-9","bbox":[0,0,10,10]}], "connections":[]}
    result = evaluate(gold, prediction)
    assert result["symbol_precision"] == 1 and result["symbol_recall"] == .5
    assert result["tag_exact_match"] == 0 and result["connectivity_f1"] == 0
    assert evaluate({"scope":"partial"}, prediction)["status"] == "NOT_MEASURED"
