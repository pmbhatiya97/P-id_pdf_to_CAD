import asyncio
import json
import os
from pathlib import Path
import sys

import ezdxf
import pytest

from lineaforge.cad import (
    ATTRIBUTE_TAGS, AutoCadMcpBackend, CadBackend, CadError, CadUnavailable,
    EzdxfBackend, SolidWorksMcpBackend, build_graph_cad,
)
from lineaforge.cad_server import AutoCadComAdapter, create_server
from lineaforge.models import Element, PidGraph


def element(identifier, kind, **kwargs):
    return Element(id=identifier, kind=kind, bbox=[10, 10, 50, 50],
                   confidence=.95, tile_id="tile-1", source_agent="manual-test", **kwargs)


def graph(elements):
    return PidGraph(page_id="test", width_px=100, height_px=100,
                    width_mm=200, height_mm=100, elements=elements)


def test_real_dxf_geometry_attributes_xdata_and_reopen(tmp_path):
    source = graph([
        element("pipe-1", "pipe", points=[[10.25, 20.5], [40.7, 20.5]]),
        element("eq-1", "equipment", subclass="source-outline", tag="R-316",
                points=[[10, 10], [50, 10], [50, 50], [10, 50]],
                attributes={"closed": "true", "service": "wash", "size": "25", "spec": "T1"}),
        element("text-1", "text", text="SOURCE NOTE"),
    ])
    result = build_graph_cad(source, EzdxfBackend(), tmp_path / "graph.dxf")
    doc = ezdxf.readfile(result["output_path"])
    pipe = doc.modelspace().query('LWPOLYLINE[layer=="PIPING-PROCESS"]')[0]
    assert [(p[0], p[1]) for p in pipe.get_points()] == [(20.5, 79.5), (81.4, 79.5)]
    block = doc.modelspace().query("INSERT")[0]
    attrs = {a.dxf.tag: a.dxf.text for a in block.attribs}
    assert set(attrs) == set(ATTRIBUTE_TAGS)
    assert attrs["TAG"] == "R-316"
    assert attrs["SOURCE_ID"] == "eq-1"
    assert attrs["SERVICE"] == "wash"
    assert result["source_ids"] == ["eq-1", "pipe-1", "text-1"]
    assert result["reopened"] and result["audit_errors"] == 0
    assert result["entity_count"] == 3 and not result["review"]
    assert result["layer_counts_match"]


def test_unknown_symbol_is_review_geometry_and_unreadable_tag_stays_empty(tmp_path):
    source = graph([element("unknown-1", "equipment", status="UNREADABLE", tag=None)])
    result = build_graph_cad(source, EzdxfBackend(), tmp_path / "unknown.dxf")
    doc = ezdxf.readfile(result["output_path"])
    ref = doc.modelspace().query("INSERT")[0]
    assert ref.dxf.layer == "REVIEW-FLAGS"
    assert ref.get_attrib_text("TAG") == ""
    assert len(result["review"]) == 1
    assert "fallback" in result["review"][0]["reason"]
    assert not doc.modelspace().query('INSERT[layer=="EQUIPMENT"]')


def test_clean_mode_is_explicit_and_changes_only_on_request(tmp_path):
    source = graph([element("pipe", "pipe", points=[[10.23, 20.41], [40.73, 20.41]])])
    result = build_graph_cad(source, EzdxfBackend(), tmp_path / "clean.dxf", mode="clean")
    doc = ezdxf.readfile(result["output_path"])
    assert [(p[0], p[1]) for p in doc.modelspace().query("LWPOLYLINE")[0].get_points()] == [(20., 80.), (81., 80.)]
    assert "changed" in result["fidelity_notice"]


def test_partial_gold_requires_review_for_all_missing_shapes(tmp_path):
    gold_path = Path(__file__).parents[1] / "docs/dataset_gold.partial.json"
    if not gold_path.exists():
        pytest.skip("Private dataset evidence is excluded from the sanitized source download")
    gold = json.loads(gold_path.read_text())
    page = gold["pages"][0]
    elements = [Element(id=a["id"], kind="equipment", subclass=a["family"], tag=a["label"],
                        bbox=a["bbox_xyxy_px"], confidence=1, tile_id="manual-gold", source_agent="manual")
                for a in page["annotations"]]
    source = PidGraph(page_id=page["sheet_id"], width_px=2400, height_px=1696,
                      width_mm=841, height_mm=594, elements=elements)
    result = build_graph_cad(source, EzdxfBackend(), tmp_path / "partial.dxf")
    assert len(result["review"]) == len(elements) == 5
    assert result["build_counts"]["blocks"] == 5
    assert set(result["entities_per_layer"]) == {"REVIEW-FLAGS"}


def test_protocol_and_explicit_unavailable_formats(tmp_path):
    assert isinstance(EzdxfBackend(), CadBackend)
    with pytest.raises(CadError, match="DXF"):
        EzdxfBackend().save(tmp_path / "not-a-dwg.dwg")
    with pytest.raises(CadUnavailable, match="SolidWorks generation is unavailable"):
        SolidWorksMcpBackend()
    with pytest.raises(CadError, match="output root"):
        AutoCadMcpBackend(output_root=tmp_path / "allowed").save(tmp_path / "outside.dwg")
    with pytest.raises(CadError, match="output root"):
        AutoCadComAdapter(tmp_path / "allowed").output_path(tmp_path / "outside.dwg")


def test_mcp_tool_schemas_exist_without_launching_autocad(tmp_path):
    tools = asyncio.run(create_server(tmp_path).list_tools())
    names = {t.name for t in tools}
    assert {"new_drawing", "define_block", "draw_arc", "set_attribute", "save_drawing", "reopen_and_enumerate", "plot_pdf"} <= names
    assert not names.intersection({"send_command", "execute_lisp", "run_shell"})
    insert = next(t for t in tools if t.name == "insert_block")
    assert "source_id" in insert.inputSchema["properties"]


def test_actual_mcp_stdio_handshake_and_tool_list(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def exercise():
        project = Path(__file__).parents[1]
        paths = [str(project)]
        if (project / ".runtime").is_dir():
            paths.append(str(project / ".runtime"))
        params = StdioServerParameters(command=sys.executable,
                 args=["-m", "lineaforge.cad_server", "--output-root", str(tmp_path)],
                 env={"PYTHONPATH": os.pathsep.join(paths)})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                assert len(result.tools) == 17
                assert "save_drawing" in {t.name for t in result.tools}
    asyncio.run(exercise())


@pytest.mark.local_cad
@pytest.mark.skipif(os.environ.get("LINEAFORGE_TEST_LOCAL_CAD") != "1", reason="requires local CAD: licensed AutoCAD installation and explicit opt-in")
def test_native_autocad_round_trip(tmp_path):
    source = graph([element("line", "pipe", points=[[10, 10], [20, 20]])])
    result = build_graph_cad(source, AutoCadMcpBackend(output_root=tmp_path), tmp_path / "native.dwg")
    assert result["reopened"] and result["entity_count"] > 0
