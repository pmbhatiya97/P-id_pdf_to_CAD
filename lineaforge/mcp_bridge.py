"""Run the LineaForge AutoCAD/SOLIDWORKS bridge over stdio or Streamable HTTP."""
from __future__ import annotations

import argparse
import asyncio
import base64
import functools
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import time
import weakref

from .cad import CadError, _prepare_windows_runtime
from .mcp_connections import CHUNK_BYTES, MAX_ARTIFACT_BYTES


def read_artifact_chunk(output_root, output_path, offset=0, length=CHUNK_BYTES):
    """Expose only generated CAD exports, not arbitrary CAD-host files."""
    root, path = Path(output_root).resolve(), Path(output_path).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in (".dwg", ".dxf"):
        raise CadError("Artifact must be a DWG/DXF inside the configured bridge output root")
    if not path.is_file():
        raise CadError("CAD artifact does not exist")
    size = path.stat().st_size
    if not 0 < size <= MAX_ARTIFACT_BYTES:
        raise CadError("CAD artifact is empty or exceeds 512 MiB")
    if isinstance(offset, bool) or isinstance(length, bool) or not isinstance(offset, int) or not isinstance(length, int):
        raise CadError("Artifact offset and length must be integers")
    if not 0 <= offset < size or not 1 <= length <= CHUNK_BYTES:
        raise CadError("Artifact offset or chunk length is invalid")
    with path.open("rb") as source:
        source.seek(offset)
        chunk = source.read(length)
    checksum = None
    if offset + len(chunk) == size:
        with path.open("rb") as source:
            checksum = hashlib.file_digest(source, "sha256").hexdigest()
    return {"offset": offset, "total_bytes": size,
            "data_base64": base64.b64encode(chunk).decode("ascii"), "sha256": checksum}


def create_bridge(backend, output_root, template=None):
    if backend == "autocad":
        from .cad_server import create_server
        server = create_server(output_root)
    elif backend == "solidworks":
        from .solidworks_server import create_server
        server = create_server(output_root, template)
    else:
        raise CadError("Bridge backend must be autocad or solidworks")

    @server.tool()
    def read_artifact(output_path: str, offset: int = 0, length: int = CHUNK_BYTES) -> dict:
        """Fetch a bounded chunk of a generated DWG/DXF, with its final SHA-256 checksum."""
        return read_artifact_chunk(output_root, output_path, offset, length)

    return server


def _install_session_lease(server):
    """Prevent HTTP sessions from interleaving one CAD application's document.

    The pinned MCP SDK retains registered tool metadata. Wrapping its tool
    functions keeps those exact argument schemas and injects no model arguments.
    Completed/error sessions release the lease; abandoned leases expire after
    five minutes of inactivity or as soon as their MCP session is destroyed.
    """
    owner, touched = None, 0.0
    for tool in server._tool_manager.list_tools():
        if tool.name == "read_artifact":
            continue
        original, operation = tool.fn, tool.name
        def wrap(original, operation):
            @functools.wraps(original)
            def guarded(**arguments):
                nonlocal owner, touched
                session = server.get_context().session
                active = owner() if owner else None
                if active is not None and active is not session and time.monotonic() - touched < 300:
                    raise CadError("CAD bridge is busy with another drawing job. Retry after that job completes.")
                if operation == "new_drawing":
                    owner, touched = weakref.ref(session), time.monotonic()
                elif active is not session:
                    raise CadError("Start a new drawing in this MCP session before sending drawing tools")
                touched = time.monotonic()
                try:
                    result = original(**arguments)
                except Exception:
                    owner = None
                    raise
                if operation == "reopen_and_enumerate":
                    owner = None
                return result
            return guarded
        tool.fn = wrap(original, operation)


class BearerTokenMiddleware:
    def __init__(self, app, token):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            supplied = headers.get(b"authorization", b"").decode("latin-1")
            if not hmac.compare_digest(supplied.encode("utf-8"), ("Bearer " + self.token).encode("utf-8")):
                from starlette.responses import JSONResponse
                await JSONResponse({"error": "CAD bridge bearer token is required"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def http_app(server, *, host="127.0.0.1", port=8791, allowed_hosts=None, token=None):
    from mcp.server.transport_security import TransportSecuritySettings
    # Binding a LAN/public interface is an explicit operator choice and requires
    # a secret. TLS termination belongs at the trusted reverse proxy/VPN.
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host == "localhost"
    if not local and not token:
        raise CadError("A non-loopback CAD bridge requires LINEAFORGE_CAD_TOKEN")
    hosts = list(allowed_hosts or [])
    if not hosts:
        if not local:
            raise CadError("Set --allowed-host for every trusted reverse-proxy/VPN hostname")
        hosts = [f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"]
    if "*" in hosts or any("/" in h or "@" in h for h in hosts):
        raise CadError("CAD bridge allowed hosts must be explicit host[:port] values")
    server.settings.host, server.settings.port = host, port
    server.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=hosts,
        allowed_origins=["http://" + h for h in hosts] + ["https://" + h for h in hosts])
    _install_session_lease(server)
    app = server.streamable_http_app()
    if token:
        app.add_middleware(BearerTokenMiddleware, token=token)
    return app


def main():
    _prepare_windows_runtime()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("autocad", "solidworks"), default="autocad")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--allowed-host", action="append", default=[])
    parser.add_argument("--write-schema", type=Path)
    args = parser.parse_args()
    server = create_bridge(args.backend, args.output_root, args.template)
    if args.write_schema:
        args.write_schema.parent.mkdir(parents=True, exist_ok=True)
        args.write_schema.write_text(json.dumps([t.model_dump(mode="json") for t in asyncio.run(server.list_tools())], indent=2), encoding="utf-8")
    elif args.transport == "stdio":
        server.run(transport="stdio")
    else:
        import uvicorn
        app = http_app(server, host=args.host, port=args.port, allowed_hosts=args.allowed_host,
                       token=os.environ.get("LINEAFORGE_CAD_TOKEN"))
        uvicorn.run(app, host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
