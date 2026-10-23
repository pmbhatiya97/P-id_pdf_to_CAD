import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import os
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from lineaforge.cad import CadError, CadUnavailable, EzdxfBackend
from lineaforge.mcp_connections import (ConfigurableMcpBackend, REQUIRED_TOOLS, CLIENT_TOOL_ARGUMENTS,
    make_backend, probe_connection, validate_cad_config)
from lineaforge.mcp_bridge import create_bridge, http_app, read_artifact_chunk


def result(value=None, error=False):
    return SimpleNamespace(isError=error, content=[], structuredContent=value or {})


def advertised(name):
    return SimpleNamespace(name=name,inputSchema={"type":"object",
        "properties":{key:{} for key in CLIENT_TOOL_ARGUMENTS.get(name,set())}})


def test_argument_contract_checked_before_any_cad_mutation(tmp_path):
    backend = make_backend({"backend":"autocad"},tmp_path)
    calls=[]
    class Session:
        async def list_tools(self,cursor=None):
            tools=[advertised(name) for name in REQUIRED_TOOLS]
            next(tool for tool in tools if tool.name=="save_drawing").inputSchema["required"]=["unexpected_template"]
            return SimpleNamespace(tools=tools,nextCursor=None)
        async def call_tool(self,name,arguments):
            calls.append(name)
            return result()
    @asynccontextmanager
    async def session():
        yield Session()
    backend._session=session
    with pytest.raises(CadError,match="incompatible argument contract"):
        backend.save(tmp_path / "drawing.dxf")
    assert calls==[]


def test_argument_types_preflight_all_tools_before_new_drawing(tmp_path):
    backend = make_backend({"backend":"autocad"},tmp_path)
    backend.add_text("verified",[1,2],3)
    calls=[]
    class Session:
        async def list_tools(self,cursor=None):
            tools=[advertised(name) for name in REQUIRED_TOOLS]
            next(tool for tool in tools if tool.name=="add_text").inputSchema["properties"]["height"]={"type":"string"}
            return SimpleNamespace(tools=tools,nextCursor=None)
        async def call_tool(self,name,arguments):
            calls.append(name)
            return result()
    @asynccontextmanager
    async def session():
        yield Session()
    backend._session=session
    with pytest.raises(CadError,match="no drawing tools were executed"):
        backend.save(tmp_path / "drawing.dxf")
    assert calls==[]


def test_cad_schemas_cannot_retrieve_external_references():
    from lineaforge.mcp_connections import _local_schema_references
    for url in ("https://untrusted.example/schema", "file:///C:/private.json", "http://169.254.169.254/"):
        with pytest.raises(CadError,match="external schema retrieval is disabled"):
            _local_schema_references({"properties":{"text":{"$ref":url}}})
    _local_schema_references({"properties":{"text":{"$ref":"#/$defs/Text"}}})


def test_configuration_never_accepts_shell_args_or_embedded_secrets():
    assert validate_cad_config({"backend": "autocad"})["transport"] == "stdio"
    assert validate_cad_config({"backend": "solidworks_mcp"})["backend"] == "solidworks"
    for config in ({"args": "-m lineaforge.cad_server"}, {"args": [1]},
        {"command": "python\nwhoami"}, {"headers": {"Authorization": "secret"}},
        {"timeout_seconds": True}, {"timeout_seconds": float("nan")},
        {"auth_token_env": "not a variable"}, {"remote_output_root": "relative"}):
        with pytest.raises(CadError):
            validate_cad_config({"backend": "autocad", **config})


def test_endpoint_privacy_and_no_hostname_loopback_exemption(tmp_path):
    def cfg(endpoint):
        return {"backend": "autocad", "transport": "streamable_http", "endpoint": endpoint}
    normalized = validate_cad_config(cfg("http://localhost:8791/mcp"))
    assert normalized["endpoint"] == "http://127.0.0.1:8791/mcp" and not normalized["external"]
    with pytest.raises(CadError, match="HTTPS"):
        make_backend(cfg("http://cad.example/mcp"), tmp_path, allow_external=True)
    for endpoint in ("https://user:secret@cad.example/mcp", "https://cad.example/mcp?token=secret", "file:///mcp"):
        with pytest.raises(CadError):
            make_backend(cfg(endpoint), tmp_path, allow_external=True)
    with pytest.raises(CadError, match="consent"):
        make_backend(cfg("https://cad.example/mcp"), tmp_path)
    remote = make_backend(cfg("https://cad.example/mcp"), tmp_path, allow_external=True)
    assert remote.config["external"]
    with pytest.raises(CadError, match="remote_output_root"):
        remote._cad_host_path(tmp_path / "new.dwg")


def test_local_defaults_and_backend_selection(tmp_path):
    backend = make_backend({"backend": "solidworks"}, tmp_path)
    assert backend.command == sys.executable
    assert backend.args[:4] == ["-m", "lineaforge.mcp_bridge", "--backend", "solidworks"]
    assert isinstance(make_backend({"backend": "ezdxf"}, tmp_path), EzdxfBackend)
    assert asyncio.run(probe_connection({"backend": "ezdxf"}, tmp_path))["connected"]


def test_web_blank_args_use_defaults_and_template_path_is_forwarded(tmp_path):
    from lineaforge.web_config import CadSettings
    config = CadSettings(backend="solidworks", template_path=r"C:\CAD\blank.drwdot").model_dump()
    backend = make_backend(config, tmp_path)
    assert backend.args[:4] == ["-m", "lineaforge.mcp_bridge", "--backend", "solidworks"]
    assert backend.args[-2:] == ["--template", r"C:\CAD\blank.drwdot"]
    custom = make_backend({"backend": "autocad", "command": "custom-executable", "args": []}, tmp_path)
    assert custom.args == []
    config["template_path"] = "relative.drwdot"
    with pytest.raises(CadError, match="absolute"):
        make_backend(config, tmp_path)


def test_remote_job_paths_are_unique_with_shared_artifact_directory_name(tmp_path):
    config = {"backend": "autocad", "transport": "streamable_http", "endpoint": "https://cad.example/mcp",
              "remote_output_root": r"C:\CAD\exports"}
    first_root, second_root = tmp_path / "job-1" / "artifacts", tmp_path / "job-2" / "artifacts"
    first = make_backend(config, first_root, allow_external=True)
    second = make_backend(config, second_root, allow_external=True)
    assert first._cad_host_path(first_root / "drawing.dwg") != second._cad_host_path(second_root / "drawing.dwg")


def test_remote_audit_hashes_geometry_without_recording_tags_or_secrets(tmp_path):
    config = {"backend": "autocad", "transport": "streamable_http", "endpoint": "https://cad.example/mcp",
              "remote_output_root": r"C:\CAD\exports", "auth_token_env": "LINEAFORGE_CAD_TOKEN"}
    backend = make_backend(config, tmp_path, allow_external=True)
    backend.add_text("PRIVATE-TAG-123", [1,2], 3)
    backend._audit_external_plan(r"C:\CAD\exports\drawing.dwg")
    import json
    raw = (tmp_path / "mcp_transmission.jsonl").read_text()
    value = json.loads(raw)
    assert value["external"] and value["authorized"] and value["argument_bytes"] > 0
    assert "add_text" in value["tool_names"] and len(value["plan_sha256"]) == 64
    assert "PRIVATE-TAG" not in raw and "LINEAFORGE_CAD_TOKEN" not in raw
    assert all(len(tool["argument_sha256"]) == 64 for tool in value["tools"])
    local = make_backend({"backend": "autocad"}, tmp_path / "local")
    local._audit_external_plan(str(tmp_path / "local" / "drawing.dwg"))
    assert not (tmp_path / "local" / "mcp_transmission.jsonl").exists()


@pytest.mark.parametrize("backend", ["autocad", "solidworks"])
def test_real_stdio_initialize_and_tool_discovery_without_starting_cad(tmp_path, backend):
    probe = asyncio.run(probe_connection({"backend": backend}, tmp_path))
    assert probe["connected"] and REQUIRED_TOOLS <= set(probe["tools"])
    assert "read_artifact" in probe["tools"]
    assert probe["native_cad_verified"] is False


def test_file_read_tool_cannot_escape_root_or_read_host_secrets(tmp_path):
    output = tmp_path / "drawings"
    output.mkdir()
    drawing = output / "test.dwg"
    drawing.write_bytes(b"AC1032real-test-payload")
    value = read_artifact_chunk(output, drawing, 0, 7)
    assert base64.b64decode(value["data_base64"]) == drawing.read_bytes()[:7]
    final = read_artifact_chunk(output, drawing, 7)
    assert final["sha256"] == hashlib.sha256(drawing.read_bytes()).hexdigest()
    secret = tmp_path / "secret.dwg"
    secret.write_bytes(b"private")
    for path, offset, length in ((secret, 0, 1), (output / "notes.txt", 0, 1),
                                (drawing, -1, 1), (drawing, 0, 2**30)):
        with pytest.raises(CadError):
            read_artifact_chunk(output, path, offset, length)


def test_http_bridge_requires_explicit_hosts_and_credentials_off_loopback(tmp_path):
    with pytest.raises(CadError, match="TOKEN"):
        http_app(create_bridge("autocad", tmp_path), host="0.0.0.0")
    with pytest.raises(CadError, match="allowed-host"):
        http_app(create_bridge("autocad", tmp_path), host="0.0.0.0", token="test-token")


def test_http_bridge_bearer_and_rebinding_guards(tmp_path):
    from starlette.testclient import TestClient
    app = http_app(create_bridge("autocad", tmp_path), token="test-token")
    with TestClient(app, base_url="http://127.0.0.1:8791") as client:
        assert client.post("/mcp", json={}).status_code == 401
        assert client.post("/mcp", json={}, headers={"Authorization": "Bearer test-token", "Host": "attacker.example"}).status_code == 421


def test_real_streamable_http_initialize_and_discovery(tmp_path, monkeypatch):
    import uvicorn
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.listen(64)
    app = http_app(create_bridge("autocad", tmp_path), port=port, token="http-contract-test-token")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    monkeypatch.setenv("LINEAFORGE_TEST_CAD_TOKEN", "http-contract-test-token")
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        value = asyncio.run(probe_connection({"backend": "autocad", "transport": "streamable_http",
            "endpoint": f"http://127.0.0.1:{port}/mcp", "auth_token_env": "LINEAFORGE_TEST_CAD_TOKEN"}, tmp_path))
        assert value["connected"] and REQUIRED_TOOLS <= set(value["tools"])
        assert not value["native_cad_verified"]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
    assert not thread.is_alive()


def test_frozen_bridge_uses_sibling_console_helper(tmp_path, monkeypatch):
    executable = tmp_path / "LineaForgeWeb.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    with pytest.raises(CadUnavailable, match="sibling"):
        make_backend({"backend": "autocad"}, tmp_path)
    helper = tmp_path / "LineaForgeCadBridge.exe"
    helper.write_bytes(b"test-path-only")
    backend = make_backend({"backend": "solidworks"}, tmp_path)
    assert backend.command == str(helper)
    assert backend.args == ["--backend", "solidworks", "--output-root", str(tmp_path), "--transport", "stdio"]
    explicit = make_backend({"backend": "autocad", "command": "custom-python"}, tmp_path)
    assert explicit.command == "custom-python" and explicit.args[:2] == ["-m", "lineaforge.mcp_bridge"]


def test_required_tools_checked_before_any_drawing_mutation(tmp_path):
    backend = make_backend({"backend": "autocad"}, tmp_path)
    calls = []
    class Session:
        async def list_tools(self, cursor=None):
            return SimpleNamespace(tools=[advertised("new_drawing")], nextCursor=None)
        async def call_tool(self, name, arguments):
            calls.append(name)
            return result()
    @asynccontextmanager
    async def session():
        yield Session()
    backend._session = session
    with pytest.raises(CadError, match="missing required tools"):
        backend.save(tmp_path / "drawing.dwg")
    assert calls == []


def test_native_tool_failure_is_not_reported_as_saved(tmp_path):
    backend = make_backend({"backend": "autocad"}, tmp_path)
    class Session:
        async def list_tools(self, cursor=None):
            return SimpleNamespace(tools=[advertised(name) for name in REQUIRED_TOOLS], nextCursor=None)
        async def call_tool(self, name, arguments):
            return result(error=True)
    @asynccontextmanager
    async def session():
        yield Session()
    backend._session = session
    with pytest.raises(CadUnavailable, match="new_drawing failed"):
        backend.save(tmp_path / "drawing.dwg")
    assert not (tmp_path / "drawing.dwg").exists()


def test_remote_file_is_downloaded_and_reopen_verified(tmp_path):
    payload = b"AC1032" + b"contract-test-native-placeholder" * 100
    backend = make_backend({"backend": "solidworks", "transport": "streamable_http",
        "endpoint": "https://cad.example/mcp", "remote_output_root": r"C:\CAD\exports"},
        tmp_path, allow_external=True)
    calls = []
    class Session:
        async def list_tools(self, cursor=None):
            return SimpleNamespace(tools=[advertised(name) for name in REQUIRED_TOOLS | {"read_artifact"}], nextCursor=None)
        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            if name == "read_artifact":
                return result({"offset": 0, "total_bytes": len(payload), "data_base64": base64.b64encode(payload).decode(),
                    "sha256": hashlib.sha256(payload).hexdigest()})
            return result({"saved": True, "reopened": True, "entity_count": 2})
    @asynccontextmanager
    async def session():
        yield Session()
    backend._session = session
    path = tmp_path / "job-abc" / "drawing.dwg"
    value = backend.save(path)
    assert path.read_bytes() == payload and value["native_cad_verified"]
    assert [name for name, _ in calls] == ["new_drawing", "set_units", "save_drawing", "reopen_and_enumerate", "read_artifact"]
    assert calls[-1][1]["output_path"].startswith("C:\\CAD\\exports\\job-")
    assert calls[-1][1]["output_path"].endswith("\\drawing.dwg")


def test_corrupt_download_never_becomes_completed_artifact(tmp_path):
    backend = make_backend({"backend": "autocad"}, tmp_path)
    class Session:
        async def call_tool(self, name, arguments):
            return result({"offset": 0, "total_bytes": 10, "data_base64": "not-base64"})
    path = tmp_path / "bad.dwg"
    with pytest.raises(CadError):
        asyncio.run(backend._download(Session(), "remote.dwg", path))
    assert not path.exists() and not path.with_suffix(".dwg.part").exists()


def test_output_root_and_existing_artifact_guards(tmp_path):
    backend = make_backend({"backend": "autocad"}, tmp_path / "outputs")
    with pytest.raises(CadError, match="output root"):
        backend.save(tmp_path / "escape.dwg")
    output = tmp_path / "outputs" / "existing.dwg"
    output.parent.mkdir()
    output.write_bytes(b"AC1032existing")
    with pytest.raises(CadError, match="already exists"):
        backend.save(output)
