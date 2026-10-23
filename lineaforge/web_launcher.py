"""Serve the compiled React app and API together on Windows or a server."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import socket
import threading
import time
import webbrowser
import httpx
from . import __version__


def existing_instance(url, token=""):
    headers = {"Authorization":"Bearer " + token} if token else {}
    try:
        with httpx.Client(trust_env=False,follow_redirects=False,timeout=3) as client:
            response = client.get(url + "/api/health",headers=headers)
            if response.status_code != 200:
                return False
            payload = response.json()
            return isinstance(payload,dict) and payload.get("product") == "lineaforge"
    except (httpx.HTTPError, OSError, ValueError) as exc:
        if os.environ.get("LINEAFORGE_DEBUG_STARTUP") == "1":
            print(f"Existing-instance probe: {type(exc).__name__}: {exc}",flush=True)
        return False


def _open_when_ready(url, token):
    for _ in range(100):
        if existing_instance(url,token):
            webbrowser.open(url)
            return
        time.sleep(.2)


def main(argv=None):
    parser = argparse.ArgumentParser(description="LineaForge React workbench + FastAPI")
    parser.add_argument("--host", default=os.environ.get("LINEAFORGE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LINEAFORGE_PORT", "8765")))
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--frontend-dir", type=Path)
    parser.add_argument("--open", action="store_true", help="Open the browser when the local server is ready")
    parser.add_argument("--check", action="store_true", help="Check the packaged application without starting a server")
    args = parser.parse_args(argv)
    os.environ["LINEAFORGE_HOST"] = args.host
    frontend = args.frontend_dir or Path(__file__).resolve().parent.parent / "web" / "dist"
    if not (frontend / "index.html").is_file():
        parser.error("React build is missing. Run npm ci and npm run build inside web, or use the Windows portable package.")
    if args.check:
        print(json.dumps({"version": __version__, "frontend": True, "api": "FastAPI", "host": args.host, "port": args.port}))
        return 0
    local = args.host in {"127.0.0.1", "localhost", "::1"}
    url = f"http://{'[' + args.host + ']' if ':' in args.host else args.host}:{args.port}"
    if local and existing_instance(url, os.environ.get("LINEAFORGE_API_TOKEN", "")):
        if args.open:
            webbrowser.open(url)
        print("LineaForge is already running at " + url)
        return 0
    # Reserve the listener before opening the durable job store. Even a failed
    # health probe or simultaneous launch cannot reset a running app's jobs.
    listener = socket.socket(socket.AF_INET6 if ":" in args.host else socket.AF_INET,socket.SOCK_STREAM)
    try:
        if hasattr(socket,"SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        else:
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:
            listener.bind((args.host,args.port))
            listener.listen(2048)
        except OSError:
            print(f"Port {args.port} is already in use or cannot be opened. Check {url} or choose another port.",flush=True)
            return 2
        from .web_api import create_app
        import uvicorn
        app = create_app(data_dir=args.data_dir, frontend_dir=frontend)
        if args.open and local:
            threading.Thread(target=_open_when_ready, args=(url, os.environ.get("LINEAFORGE_API_TOKEN", "")), daemon=True).start()
        print(f"LineaForge {__version__}: {url}\nClose this window or press Ctrl+C to stop.", flush=True)
        server = uvicorn.Server(uvicorn.Config(app,host=args.host,port=args.port,workers=1,proxy_headers=False,log_level="info"))
        server.run(sockets=[listener])
    finally:
        listener.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
