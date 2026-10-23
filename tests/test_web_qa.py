"""Independent CAD/source crop geometry and honest QA gating."""
import json
from PIL import Image
from lineaforge.agentic import AgentRuntime
from lineaforge.agentic_qa import verify_built_cad
from lineaforge.cad import EzdxfBackend, build_graph_cad
from lineaforge.models import Element, PidGraph
from lineaforge.providers import RecordedProvider


def drawing(tmp_path):
    graph = PidGraph(page_id="non-square-pixels", width_px=200, height_px=100,
                     width_mm=50, height_mm=50,
                     elements=[Element(id="pipe", kind="pipe", bbox=[10, 10, 190, 90],
                        points=[[10, 10], [190, 90]], confidence=1, tile_id="tile-0000",
                        source_agent="fixture", status="ACCEPTED")])
    cad = tmp_path / "drawing.dxf"
    build_graph_cad(graph, EzdxfBackend(), cad)
    return graph, cad


def test_cad_qa_uses_independent_axis_scales(tmp_path, monkeypatch):
    graph, cad = drawing(tmp_path)
    source = tmp_path / "source"
    tile = source / "tiles" / "tile-0000"
    tile.mkdir(parents=True)
    Image.new("RGB", (200, 100), "white").save(tile / "cleaned.png")
    (source / "tile_plan.json").write_text(json.dumps([[0, 0, 200, 100]]))
    provider = RecordedProvider({"cad_qa": {"discrepancies": [], "needs_human": False}})
    monkeypatch.setattr(AgentRuntime, "provider", lambda self, role: provider)
    report = verify_built_cad(graph, cad, source, tmp_path / "qa", {"models": {"vlm": {"model": "fixture"}}})
    with Image.open(tmp_path / "qa" / "cad-tile-0000.png") as compared:
        assert abs(compared.width - 200) <= 1 and abs(compared.height - 100) <= 1
    assert report["structural"]["structural_traceability_pass"]
    assert report["visual"]["compared_tiles"] == 1
    assert report["status"] == "NEEDS_HUMAN" and not report["visual_fidelity_pass"]


def test_no_vlm_is_structural_only(tmp_path):
    graph, cad = drawing(tmp_path)
    report = verify_built_cad(graph, cad, tmp_path / "source", tmp_path / "qa", {})
    assert report["structural"]["reopened"]
    assert report["visual"]["compared_tiles"] == 0
    assert "configure a VLM" in report["reason"]


def test_cloud_qa_without_consent_sends_nothing(tmp_path, monkeypatch):
    graph, cad = drawing(tmp_path)
    def unexpected(*args):
        raise AssertionError("Cloud provider must not be created")
    monkeypatch.setattr(AgentRuntime, "provider", unexpected)
    report = verify_built_cad(graph, cad, tmp_path / "source", tmp_path / "qa",
        {"models": {"vlm": {"model": "remote", "mode": "cloud"}}})
    assert "not authorized" in report["reason"]
    assert report["visual"]["compared_tiles"] == 0
