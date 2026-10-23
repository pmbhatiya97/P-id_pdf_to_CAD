"""Configured, job-scoped MCP CAD clients. Drawing content never selects executables."""
from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import sys
import time
from urllib.parse import urlsplit, urlunsplit

from .cad import AutoCadMcpBackend, CadError, CadUnavailable, EzdxfBackend, _points

REQUIRED_TOOLS = frozenset({"new_drawing", "set_units", "create_layer", "define_block",
    "insert_block", "draw_polyline", "add_text", "save_drawing", "reopen_and_enumerate"})
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
CLIENT_TOOL_ARGUMENTS = {
    "new_drawing": set(), "set_units": {"units"},
    "create_layer": {"name","color","linetype","lineweight"},
    "define_block": {"name","geometry","attribute_tags"},
    "insert_block": {"name","position","attributes","layer","scale","rotation","source_id"},
    "draw_polyline": {"points","layer","closed","source_id"},
    "add_text": {"text","position","height","layer","rotation","source_id"},
    "save_drawing": {"output_path"}, "reopen_and_enumerate": {"output_path"},
}
CHUNK_BYTES = 1024 * 1024


def _local_schema_references(value):
    """A CAD server cannot make validation retrieve URLs or local files."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"$ref", "$dynamicRef", "$recursiveRef"} and isinstance(item, str) and not item.startswith("#"):
                raise CadError("CAD tool schemas must use local JSON references; external schema retrieval is disabled")
            _local_schema_references(item)
    elif isinstance(value, list):
        for item in value:
            _local_schema_references(item)


def _endpoint(value: str) -> tuple[str, bool]:
    """Only literal loopback addresses get the local/no-consent exemption."""
    try:
        p = urlsplit(value)
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or p.query or p.fragment:
            raise ValueError
        port = p.port
        host = p.hostname.lower()
        if host == "localhost":
            # Avoid resolving a nominally local hostname through DNS.
            host = "127.0.0.1"
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        if not local and p.scheme != "https":
            raise CadError("Remote CAD MCP endpoints must use HTTPS")
        netloc = f"[{host}]" if ":" in host else host
        if port is not None:
            netloc += f":{port}"
        return urlunsplit((p.scheme, netloc, p.path or "/mcp", "", "")), not local
    except (ValueError, TypeError) as exc:
        raise CadError("MCP endpoint must be an HTTP(S) URL without embedded credentials, query, or fragment") from exc


def validate_cad_config(config: dict | None) -> dict:
    """Normalize trusted operator settings, without launching processes or connecting."""
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise CadError("CAD configuration must be an object")
    backend = str(config.get("backend", "ezdxf")).lower()
    backend = {"autocad_mcp": "autocad", "solidworks_mcp": "solidworks", "dxf": "ezdxf"}.get(backend, backend)
    if backend not in ("ezdxf", "autocad", "solidworks"):
        raise CadError("CAD backend must be ezdxf, autocad, or solidworks")
    transport = str(config.get("transport", "stdio")).replace("-", "_")
    if transport not in ("stdio", "streamable_http"):
        raise CadError("MCP transport must be stdio or streamable_http")
    raw_timeout = config.get("timeout_seconds", 300)
    if isinstance(raw_timeout, bool):
        raise CadError("MCP timeout must be 5 to 7200 seconds")
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError) as exc:
        raise CadError("MCP timeout must be 5 to 7200 seconds") from exc
    if not math.isfinite(timeout) or not 5 <= timeout <= 7200:
        raise CadError("MCP timeout must be 5 to 7200 seconds")
    command = config.get("command") or None
    if command is not None and (not isinstance(command, str) or any(c in command for c in "\x00\n\r")):
        raise CadError("MCP command must be a local executable path or executable name")
    args = config.get("args")
    if args is not None and (not isinstance(args, list) or any(not isinstance(a, str) or "\x00" in a for a in args)):
        raise CadError("MCP args must be an array of strings, not a shell command")
    if not command and args == []:
        args = None
    token_env = config.get("auth_token_env") or ""
    if not isinstance(token_env, str) or (token_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token_env)):
        raise CadError("MCP auth_token_env must name an environment variable")
    # Raw secrets must never enter persisted settings or be echoed by a test call.
    if any(config.get(key) for key in ("token", "api_key", "headers", "authorization", "env")):
        raise CadError("Configure MCP secrets using auth_token_env in the server environment")
    endpoint, external = "", False
    if transport == "streamable_http":
        endpoint, external = _endpoint(str(config.get("endpoint") or ""))
    remote_root = config.get("remote_output_root") or ""
    if not isinstance(remote_root, str) or "\x00" in remote_root:
        raise CadError("remote_output_root must be a path on the CAD host")
    if remote_root and not (Path(remote_root).is_absolute() or PureWindowsPath(remote_root).is_absolute()):
        raise CadError("remote_output_root must be an absolute path on the CAD host")
    template_path = config.get("template_path") or ""
    if not isinstance(template_path, str) or any(c in template_path for c in "\x00\n\r"):
        raise CadError("template_path must be a drawing-template path on the CAD host")
    if template_path and (not template_path.lower().endswith(".drwdot") or not (
            Path(template_path).is_absolute() or PureWindowsPath(template_path).is_absolute())):
        raise CadError("template_path must be an absolute SOLIDWORKS .drwdot path")
    return {"backend": backend, "transport": transport, "command": command,
            "args": args, "endpoint": endpoint, "timeout_seconds": timeout,
            "auth_token_env": token_env, "remote_output_root": remote_root,
            "external": external, "template_path": template_path}


def _consent(config, allow_external):
    if config["external"] and not allow_external:
        raise CadError("This CAD MCP endpoint is external to this computer. Enable external-data consent for this job before sending geometry.")


def _decode_result(result, tool):
    blocks = [c.text for c in result.content if getattr(c, "type", "") == "text"]
    if result.isError:
        # The server error explains missing licenses/configuration; cap it to avoid
        # accidentally exposing whole drawing payloads in connection diagnostics.
        raise CadUnavailable(f"CAD tool {tool} failed: {' '.join(blocks)[:800]}")
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    for block in reversed(blocks):
        try:
            value = json.loads(block)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    raise CadError(f"CAD tool {tool} did not return a structured result")


def _connection_error(exc):
    if isinstance(exc, BaseExceptionGroup):
        errors = [_connection_error(child) for child in exc.exceptions]
        return next((e for e in errors if isinstance(e, CadError)), CadUnavailable("CAD MCP connection failed"))
    if isinstance(exc, CadError):
        return exc
    if isinstance(exc, TimeoutError):
        return CadUnavailable("CAD MCP operation exceeded its configured timeout")
    # Endpoint URLs and tokens can appear in HTTP library exceptions. Do not echo.
    return CadUnavailable(f"CAD MCP connection failed ({type(exc).__name__}). Check the executable, endpoint, credentials, and CAD host logs.")


class ConfigurableMcpBackend(AutoCadMcpBackend):
    """The established CAD contract over stdio or MCP Streamable HTTP.

    Each save initializes exactly one session, discovers capabilities, executes
    the queued drawing, saves, and reopens it. HTTP clients never follow redirects.
    """

    def __init__(self, config, output_root, *, allow_external=False):
        self.config = validate_cad_config(config)
        _consent(self.config, allow_external)
        self.output_root = Path(output_root).resolve()
        self.timeout_seconds = self.config["timeout_seconds"]
        backend = self.config["backend"]
        frozen_helper = Path(sys.executable).resolve().with_name("LineaForgeCadBridge.exe")
        bundled_helper = bool(getattr(sys, "frozen", False) and not self.config["command"])
        if bundled_helper and self.config["transport"] == "stdio" and not frozen_helper.is_file():
            raise CadUnavailable("The Windows app requires the sibling LineaForgeCadBridge.exe helper for native CAD. Extract the complete portable folder or configure a source Python executable.")
        self.command = self.config["command"] or (str(frozen_helper) if bundled_helper else sys.executable)
        self.args = self.config["args"]
        if self.args is None:
            self.args = ([] if bundled_helper else ["-m", "lineaforge.mcp_bridge"]) + [
                "--backend", backend, "--output-root", str(self.output_root), "--transport", "stdio"]
            if backend == "solidworks" and self.config["template_path"]:
                self.args += ["--template", self.config["template_path"]]
        self.commands = [("new_drawing", {}), ("set_units", {"units": "mm"})]

    def draw_circle(self, center, radius, *, layer="0", source_id=""):
        return self._queue("draw_circle", dict(center=_points([center])[0], radius=float(radius), layer=layer, source_id=source_id))

    def draw_arc(self, center, radius, start_angle, end_angle, *, layer="0", source_id=""):
        return self._queue("draw_arc", dict(center=_points([center])[0], radius=float(radius), start_angle=float(start_angle), end_angle=float(end_angle), layer=layer, source_id=source_id))

    @asynccontextmanager
    async def _session(self):
        from .cad import _prepare_windows_runtime
        _prepare_windows_runtime()
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as exc:
            raise CadUnavailable("Install the pinned mcp dependency to connect native CAD") from exc
        if self.config["transport"] == "stdio":
            project = Path(__file__).resolve().parent.parent
            paths = [str(project), str(project / ".webruntime-secure"), str(project / ".webruntime"), str(project / ".runtime")]
            env = {"PYTHONPATH": os.pathsep.join(paths)}
            for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                        "LINEAFORGE_SW_DRAWING_TEMPLATE"):
                if key in os.environ:
                    env[key] = os.environ[key]
            transport = stdio_client(StdioServerParameters(command=self.command, args=self.args, env=env))
        else:
            import httpx
            headers = {}
            env_name = self.config["auth_token_env"]
            if env_name:
                token = os.environ.get(env_name, "")
                if not token:
                    raise CadUnavailable(f"CAD MCP credential environment variable {env_name} is not set")
                headers["Authorization"] = f"Bearer {token}"
            def client_factory(headers=None, timeout=None, auth=None):
                return httpx.AsyncClient(headers=headers, timeout=timeout, auth=auth,
                                         follow_redirects=False, trust_env=False)
            transport = streamablehttp_client(self.config["endpoint"], headers=headers,
                timeout=self.timeout_seconds, sse_read_timeout=self.timeout_seconds,
                httpx_client_factory=client_factory)
        async with transport as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                yield session

    async def _tools(self, session):
        names, cursor, seen_cursors, schemas = set(), None, set(), {}
        for _ in range(100):
            result = await session.list_tools(cursor=cursor)
            names.update(tool.name for tool in result.tools)
            schemas.update({tool.name: getattr(tool,"inputSchema",None) for tool in result.tools})
            cursor = result.nextCursor
            if not cursor:
                break
            if cursor in seen_cursors:
                raise CadError("CAD MCP server repeated a tools pagination cursor")
            seen_cursors.add(cursor)
        else:
            raise CadError("CAD MCP server returned excessive tools pages")
        missing = REQUIRED_TOOLS - names
        if missing:
            raise CadError("CAD MCP server is missing required tools: " + ", ".join(sorted(missing)))
        for name, supplied in CLIENT_TOOL_ARGUMENTS.items():
            schema = schemas.get(name)
            if not isinstance(schema, dict):
                raise CadError(f"CAD MCP tool {name} did not advertise an input schema")
            _local_schema_references(schema)
            declared = set(schema.get("properties", {}))
            required = set(schema.get("required", []))
            if supplied - declared or required - supplied:
                raise CadError(f"CAD MCP tool {name} has an incompatible argument contract; use a matching LineaForge bridge or adapter")
        self.tool_schemas = schemas
        return names

    def _validate_arguments(self, name, arguments):
        from jsonschema import Draft202012Validator
        schema = self.tool_schemas.get(name)
        if not isinstance(schema, dict):
            raise CadError(f"CAD MCP tool {name} has no usable input schema")
        _local_schema_references(schema)
        try:
            Draft202012Validator.check_schema(schema)
            error = next(Draft202012Validator(schema).iter_errors(arguments), None)
        except Exception as exc:
            raise CadError(f"CAD MCP tool {name} advertised an invalid input schema") from exc
        if error is not None:
            location = "/".join(str(part) for part in error.absolute_path) or "arguments"
            raise CadError(f"CAD MCP tool {name} rejected the planned argument schema at {location}; no drawing tools were executed")

    async def probe(self):
        try:
            async with asyncio.timeout(min(self.timeout_seconds, 30)):
                async with self._session() as session:
                    names = await self._tools(session)
            return {"connected": True, "backend": self.config["backend"],
                    "transport": self.config["transport"], "tools": sorted(names),
                    "native_cad_verified": False,
                    "message": "MCP tools connected. Run a drawing job on the licensed CAD host to verify native generation."}
        except Exception as exc:
            raise _connection_error(exc) from exc

    def _cad_host_path(self, local_path):
        root = self.config["remote_output_root"]
        if root:
            # Use an independent job subdirectory, preserving Windows syntax even
            # when the web API runs in a Linux deployment.
            root_path = PureWindowsPath(root) if PureWindowsPath(root).is_absolute() else Path(root)
            job_folder = "job-" + hashlib.sha256(str(self.output_root).encode("utf-8")).hexdigest()[:24]
            return str(root_path / job_folder / local_path.name)
        if self.config["external"]:
            raise CadError("Set remote_output_root to the bridge output folder on the remote CAD host")
        return str(local_path)

    async def _download(self, session, host_path, local_path):
        temporary = local_path.with_suffix(local_path.suffix + ".part")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        offset, digest, declared_total = 0, hashlib.sha256(), None
        try:
            with temporary.open("wb") as target:
                while declared_total is None or offset < declared_total:
                    value = _decode_result(await session.call_tool("read_artifact", arguments={"output_path": host_path,
                        "offset": offset, "length": CHUNK_BYTES}), "read_artifact")
                    total = value.get("total_bytes")
                    if isinstance(total, bool) or not isinstance(total, int) or not 0 < total <= MAX_ARTIFACT_BYTES:
                        raise CadError("CAD artifact size is invalid or exceeds 512 MiB")
                    if declared_total is not None and total != declared_total:
                        raise CadError("CAD artifact changed during download")
                    declared_total = total
                    if value.get("offset") != offset:
                        raise CadError("CAD artifact returned an unexpected chunk offset")
                    try:
                        chunk = base64.b64decode(value["data_base64"], validate=True)
                    except (KeyError, ValueError, TypeError) as exc:
                        raise CadError("CAD artifact is not valid base64") from exc
                    if not chunk or len(chunk) > CHUNK_BYTES or offset + len(chunk) > total:
                        raise CadError("CAD artifact returned an invalid chunk length")
                    target.write(chunk)
                    digest.update(chunk)
                    offset += len(chunk)
                checksum = value.get("sha256")
                if checksum and checksum != digest.hexdigest():
                    raise CadError("CAD artifact checksum did not match")
            _validate_file(temporary, local_path.suffix)
            temporary.replace(local_path)
        finally:
            temporary.unlink(missing_ok=True)

    async def _execute(self, path):
        host_path = self._cad_host_path(path)
        async with asyncio.timeout(self.timeout_seconds):
            async with self._session() as session:
                tools = await self._tools(session)
                needed = {name for name, _ in self.commands}
                if needed - tools:
                    raise CadError("CAD server lacks drawing primitives: " + ", ".join(sorted(needed - tools)))
                for name, arguments in [*self.commands, ("save_drawing", {"output_path": host_path}),
                                        ("reopen_and_enumerate", {"output_path": host_path})]:
                    self._validate_arguments(name, arguments)
                self._audit_external_plan(host_path)
                for name, arguments in self.commands:
                    _decode_result(await session.call_tool(name, arguments=arguments), name)
                saved = _decode_result(await session.call_tool("save_drawing", arguments={"output_path": host_path}), "save_drawing")
                if saved.get("saved") is False:
                    raise CadError("CAD host did not save the drawing")
                reopened = _decode_result(await session.call_tool("reopen_and_enumerate", arguments={"output_path": host_path}), "reopen_and_enumerate")
                if reopened.get("reopened") is not True:
                    raise CadError("CAD host did not verify reopening the saved drawing")
                # For remote hosts a same-named stale local file must never stand
                # in for the output. Require an explicit safe artifact tool.
                if self.config["external"] or self.config["remote_output_root"] or not path.is_file():
                    if "read_artifact" not in tools:
                        raise CadError("Drawing saved on the CAD host, but it does not expose read_artifact. Install the LineaForge bridge to retrieve the verified file.")
                    await self._download(session, host_path, path)
                _validate_file(path, path.suffix)
                return {**saved, **reopened, "output_path": str(path), "backend": self.config["backend"] + "_mcp",
                        "format": path.suffix[1:].upper(), "tool_calls": len(self.commands) + 2,
                        "artifact_downloaded": host_path != str(path), "native_cad_verified": True}

    def _audit_external_plan(self, host_path):
        """Log hashes/sizes of outgoing geometry without source text or secrets."""
        if not self.config["external"]:
            return
        plan = [*self.commands, ("save_drawing", {"output_path": host_path}),
                ("reopen_and_enumerate", {"output_path": host_path})]
        records = []
        for name, arguments in plan:
            payload = json.dumps(arguments, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
            records.append({"tool": name, "argument_sha256": hashlib.sha256(payload).hexdigest(),
                            "argument_bytes": len(payload)})
        encoded_plan = json.dumps(plan, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
        audit = {"time": time.time(), "destination": self.config["endpoint"], "external": True,
                 "authorized": True, "outcome": "attempt", "tool_names": [name for name, _ in plan],
                 "plan_sha256": hashlib.sha256(encoded_plan).hexdigest(), "tools": records,
                 "argument_bytes": sum(record["argument_bytes"] for record in records)}
        self.output_root.mkdir(parents=True, exist_ok=True)
        with (self.output_root / "mcp_transmission.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(audit, ensure_ascii=False) + "\n")

    def save(self, output_path):
        path = Path(output_path).resolve()
        if not path.is_relative_to(self.output_root) or path == self.output_root:
            raise CadError("CAD output must stay inside the configured output root")
        if path.suffix.lower() not in (".dxf", ".dwg"):
            raise CadError("Native CAD output must be .dwg or .dxf")
        if path.exists():
            raise CadError("CAD output already exists; use a new job output path")
        def execute():
            try:
                return asyncio.run(self._execute(path))
            except Exception as exc:
                raise _connection_error(exc) from exc
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return execute()
        with ThreadPoolExecutor(max_workers=1) as worker:
            return worker.submit(execute).result()


def _validate_file(path, suffix):
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise CadError("CAD output file is missing or empty")
    with path.open("rb") as source:
        header = source.read(256)
    if suffix.lower() == ".dwg" and not re.match(rb"AC10\d{2}", header):
        raise CadError("CAD host did not produce a native DWG file")
    if suffix.lower() == ".dxf" and not (header.startswith(b"AutoCAD Binary DXF") or re.search(rb"\b(SECTION|HEADER)\b", header)):
        raise CadError("CAD host did not produce a DXF file")


def make_backend(cad_config, output_root, allow_external=False):
    config = validate_cad_config(cad_config)
    if config["backend"] == "ezdxf":
        return EzdxfBackend()
    return ConfigurableMcpBackend(config, output_root, allow_external=allow_external)


async def probe_connection(cad_config, output_root, allow_external=False):
    config = validate_cad_config(cad_config)
    if config["backend"] == "ezdxf":
        return {"connected": True, "backend": "ezdxf", "tools": [], "native_cad_verified": False,
                "message": "Editable DXF generation is available locally. Native DWG requires a licensed CAD bridge."}
    return await ConfigurableMcpBackend(config, output_root, allow_external=allow_external).probe()
