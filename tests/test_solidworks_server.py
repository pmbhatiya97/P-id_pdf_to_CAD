import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from lineaforge.cad import CadError, CadUnavailable
from lineaforge.mcp_connections import REQUIRED_TOOLS, make_backend
from lineaforge.solidworks_server import SolidWorksComAdapter, create_server


def test_solidworks_real_tool_schema_includes_required_contract(tmp_path):
    tools = {t.name: t for t in asyncio.run(create_server(tmp_path).list_tools())}
    assert REQUIRED_TOOLS <= tools.keys()
    assert set(tools["draw_polyline"].inputSchema["properties"]) == {"points", "layer", "closed", "source_id"}
    assert "expand" in tools["define_block"].description
    assert "command" not in tools and "run_macro" not in tools


def test_solidworks_paths_are_confined(tmp_path):
    adapter = SolidWorksComAdapter(tmp_path / "outputs")
    with pytest.raises(CadError):
        adapter.output_path(tmp_path / "escape.dwg")
    with pytest.raises(CadError):
        adapter.output_path(tmp_path / "outputs" / "macro.swp")


def test_missing_native_installation_fails_honestly(tmp_path, monkeypatch):
    adapter = SolidWorksComAdapter(tmp_path)
    def unavailable():
        raise CadUnavailable("Licensed SOLIDWORKS is not installed")
    monkeypatch.setattr(adapter, "_connect", unavailable)
    with pytest.raises(CadUnavailable, match="not installed"):
        adapter.call("new_drawing")
    assert adapter.doc is None and not list(tmp_path.iterdir())


def test_source_millimetres_are_converted_to_native_metres(tmp_path):
    adapter = SolidWorksComAdapter(tmp_path)
    seen = []
    class SketchManager:
        ActiveSketch = object()
        def CreateLine(self, *values):
            seen.append(values)
            return SimpleNamespace(GetName=lambda: "Line1")
    adapter.doc = SimpleNamespace(SketchManager=SketchManager())
    assert adapter._polyline([[25, 50], [75, 100]]) == ["Line1"]
    assert seen == [(0.025, 0.05, 0, 0.075, 0.1, 0)]
    with pytest.raises(CadError):
        adapter._polyline([[float("inf"), 0], [1, 2]])


@pytest.mark.local_cad
@pytest.mark.skipif(os.environ.get("LINEAFORGE_TEST_SOLIDWORKS") != "1", reason="Enable only on a licensed SOLIDWORKS Windows workstation with LINEAFORGE_SW_DRAWING_TEMPLATE")
def test_licensed_solidworks_roundtrip(tmp_path):
    backend = make_backend({"backend": "solidworks", "timeout_seconds": 180}, tmp_path)
    backend.create_layer("PIPING-PROCESS")
    backend.draw_polyline([[10, 10], [100, 10], [100, 50]], layer="PIPING-PROCESS", source_id="native-smoke")
    backend.add_text("LineaForge CAD smoke", [20, 30], 3, layer="PIPING-PROCESS")
    value = backend.save(tmp_path / "smoke.dxf")
    assert value["reopened"] and value["entity_count"] >= 3
    assert value["native_cad_verified"]
