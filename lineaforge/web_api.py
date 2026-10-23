"""Same-origin React / FastAPI product API for local work and authenticated hosting."""
from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import hmac
import inspect
import ipaddress
import json
import os
from pathlib import Path
import shutil
from typing import Literal
from urllib.parse import urlsplit
import uuid

import fitz
import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from . import __version__, core
from .models import PidGraph
from .providers import is_loopback_url
from .web_config import ConfigStore, atomic_json
from .web_jobs import JobManager, now, safe_name

MAX_UPLOAD_BYTES = 250 * 1024 * 1024


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class JobRequest(Body):
    type: Literal["convert", "pid", "cad", "pdf_tools"]
    file_ids: list[str] = Field(default_factory=list, max_length=100)
    options: dict = Field(default_factory=dict)
    cloud_consent: bool = False


class ModelTest(Body):
    kind: Literal["vlm", "llm"]


class CadTest(Body):
    backend: Literal["ezdxf", "autocad", "solidworks"] | None = None
    cloud_consent: bool = False


class ReviewRequest(Body):
    element_id: str | None = None
    review_id: str | None = None
    action: Literal["accept", "reject", "edit"]
    updates: dict = Field(default_factory=dict)


def _validation_errors(exc):
    return [{"loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]} for item in exc.errors()]


def _inspect(path: Path, kind: str) -> dict:
    if kind == "pdf":
        with core._open_pdf(path) as document:
            count = len(document)
            info = [core._page_info(document[index]) for index in range(min(count, 100))]
            return {"pages": count, "page_info": info, "inspection_truncated": count > 100}
    if kind == "dxf":
        info = core.inspect_cad(path)
        info.pop("source", None)
        return {"pages": 1, "page_info": [], "cad_info": info}
    return {"pages": None, "page_info": [], "inspection_note": "DWG inspection requires the configured external converter or CAD application"}


def _preview(path: Path, page: int) -> bytes:
    with core._open_pdf(path) as document:
        if page < 0 or page >= len(document):
            raise ValueError("Preview page is outside this PDF")
        sheet = document[page]
        scale = min(2, 2000 / max(sheet.rect.width, sheet.rect.height))
        return sheet.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")


def create_app(data_dir: Path | None = None, frontend_dir: Path | None = None,
               deployment: bool | None = None) -> FastAPI:
    root = Path(data_dir or os.environ.get("LINEAFORGE_DATA_DIR") or (Path.home() / ".lineaforge" / "web")).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    configured_host = os.environ.get("LINEAFORGE_HOST", "127.0.0.1")
    deployed = deployment if deployment is not None else os.environ.get("LINEAFORGE_DEPLOYMENT", "").lower() in {"1", "true", "yes"} or configured_host not in {"127.0.0.1", "localhost", "::1"}
    token = os.environ.get("LINEAFORGE_API_TOKEN", "")
    if deployed and len(token) < 24:
        raise RuntimeError("Deployment requires LINEAFORGE_API_TOKEN with at least 24 characters")
    config = ConfigStore(root)
    manager = JobManager(root, config)
    inspection_slots = asyncio.Semaphore(2)
    @asynccontextmanager
    async def lifespan(app):
        yield
        manager.close()
    app = FastAPI(title="LineaForge", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.manager, app.state.config = manager, config
    origins = [item.strip().rstrip("/") for item in os.environ.get("LINEAFORGE_ALLOWED_ORIGINS", "").split(",") if item.strip()]
    if not deployed:
        origins += ["http://127.0.0.1:5173", "http://localhost:5173"]
    if "*" in origins:
        raise RuntimeError("Configure explicit LINEAFORGE_ALLOWED_ORIGINS; wildcard origins are not supported")
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if not deployed and request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"detail": "Local API requires a loopback Host header"}, status_code=403)
        if not deployed:
            try:
                local_client = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
            except ValueError:
                local_client = False
            if not local_client:
                return JSONResponse({"detail": "Remote clients require authenticated deployment mode"}, status_code=403)
        origin = request.headers.get("origin")
        same_origin = str(request.base_url).rstrip("/")
        if origin and origin.rstrip("/") != same_origin and origin.rstrip("/") not in origins:
            return JSONResponse({"detail": "Origin is not allowed"}, status_code=403)
        if request.url.path.startswith("/api/") and request.method != "OPTIONS" and token:
            supplied = request.headers.get("authorization", "")
            if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
                return JSONResponse({"detail": "A valid API bearer token is required"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_UPLOAD_BYTES + 1024 * 1024:
                    return JSONResponse({"detail": "Upload exceeds the 250 MB limit"}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": str(exc).strip("'")}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        if isinstance(exc, ValidationError):
            return JSONResponse({"detail": _validation_errors(exc)}, status_code=422)
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(PermissionError)
    async def forbidden(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.exception_handler(RequestValidationError)
    async def request_invalid(request, exc):
        return JSONResponse({"detail": _validation_errors(exc)}, status_code=422)

    @app.get("/api/health")
    def health():
        return {"product": "lineaforge", "status": "ok", "version": __version__, "mode": "deployment" if deployed else "local"}

    @app.get("/api/config")
    def get_config():
        return config.public()

    @app.put("/api/config")
    def set_config(body: dict):
        return config.update(body)

    @app.get("/api/agents")
    def agents():
        from .agentic import AGENT_CATALOG
        enabled = set(config.private()["agents"]["enabled_roles"])
        return {"agents": [{**item, "enabled": item["id"] in enabled or item.get("required", False)} for item in AGENT_CATALOG]}

    @app.post("/api/models/test")
    async def test_model(body: ModelTest):
        settings = config.private()["models"][body.kind]
        url = settings["base_url"].rstrip("/")
        if settings["mode"] == "local" and not is_loopback_url(url):
            return {"ok": False, "message": "Local mode requires a literal loopback URL, such as http://127.0.0.1:8000/v1"}
        if not settings["model"].strip():
            return {"ok": False, "message": "Enter the model name before testing the connection"}
        headers = {"Authorization": "Bearer " + settings["api_key"]} if settings["api_key"] else {}
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=15) as client:
                response = await client.get(url + "/models", headers=headers)
                response.raise_for_status()
                payload = response.json()
            models = [str(item.get("id", "")) for item in payload.get("data", []) if isinstance(item, dict)]
            present = settings["model"] in models
            return {"ok": present, "models": models[:200], "message": "Connected and configured model is listed. Image understanding and structured outputs still need a drawing job to verify." if present else "Connected, but the configured model is not listed by this endpoint"}
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            return {"ok": False, "message": "Connection failed. Check the endpoint, credentials, model server, and its /models support."}

    @app.post("/api/cad/test")
    async def test_cad(body: CadTest):
        from .mcp_connections import probe_connection
        settings = config.private()["cad"]
        if body.backend:
            settings["backend"] = body.backend
        try:
            # Probes discover capabilities; they do not create a native CAD drawing.
            if inspect.iscoroutinefunction(probe_connection):
                result = await probe_connection(settings, root / "cad-probes", allow_external=body.cloud_consent)
            else:
                result = await run_in_threadpool(probe_connection, settings, root / "cad-probes", allow_external=body.cloud_consent)
            connected = bool(result.get("connected", result.get("ok", False)))
            return {**result, "ok": connected, "message": result.get("message", "MCP connected and required tools discovered; native drawing creation still needs validation" if connected else "MCP connection test failed")}
        except Exception as exc:
            return {"ok": False, "message": str(exc)[:1000]}

    @app.get("/api/files")
    def files():
        return {"files": manager.files()}

    @app.post("/api/files", status_code=201)
    async def upload(file: UploadFile = File(...)):
        name = safe_name(file.filename or "drawing")
        kind = Path(name).suffix.lower().lstrip(".")
        if kind not in {"pdf", "dxf", "dwg"}:
            raise HTTPException(415, "Upload a PDF, DXF, or DWG drawing")
        file_id = uuid.uuid4().hex
        directory = manager.file_dir(file_id)
        directory.mkdir()
        source = directory / ("source." + kind)
        size = 0
        try:
            with source.open("wb") as stream:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "Upload exceeds the 250 MB limit")
                    stream.write(chunk)
            if not size:
                raise HTTPException(400, "The uploaded drawing is empty")
            info = {"id": file_id, "name": name, "kind": kind, "size": size, "created_at": now(), "pages": None, "page_info": []}
            async with inspection_slots:
                try:
                    info.update(await run_in_threadpool(_inspect, source, kind))
                except Exception:
                    info["inspection_error"] = "This drawing could not be inspected. Check whether it is corrupt or encrypted; conversion will report a job error."
            atomic_json(directory / "file.json", info)
            return info
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        finally:
            await file.close()

    @app.get("/api/files/{file_id}/preview")
    async def preview(file_id: str, page: int = 0):
        source = manager.file_path(file_id)
        if source.suffix.lower() != ".pdf":
            raise HTTPException(415, "Preview requires a PDF. Convert this drawing to PDF first.")
        async with inspection_slots:
            try:
                data = await run_in_threadpool(_preview, source, page)
            except core.ConversionError as exc:
                raise HTTPException(400, "The PDF cannot be opened for preview") from exc
        return Response(data, media_type="image/png")

    @app.get("/api/jobs")
    def jobs():
        return {"jobs": manager.list()}

    @app.post("/api/jobs", status_code=202)
    def start_job(body: JobRequest):
        return manager.submit(body.type, body.file_ids, body.options, body.cloud_consent)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return manager.get(job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        return manager.cancel(job_id)

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    def retry(job_id: str):
        return manager.retry(job_id)

    @app.get("/api/jobs/{job_id}/graph")
    def graph(job_id: str):
        return manager.graph(job_id).model_dump()

    @app.put("/api/jobs/{job_id}/graph")
    def put_graph(job_id: str, body: PidGraph):
        return manager.put_graph(job_id, body).model_dump()

    @app.post("/api/jobs/{job_id}/review")
    def review(job_id: str, body: ReviewRequest):
        return manager.review(job_id, body.action, body.element_id, body.review_id, body.updates).model_dump()

    @app.get("/api/jobs/{job_id}/artifacts/{artifact_id}")
    def artifact(job_id: str, artifact_id: str):
        path, info = manager.artifact_path(job_id, artifact_id)
        return FileResponse(path, filename=Path(info["name"]).name, media_type="application/octet-stream")

    frontend = Path(frontend_dir or os.environ.get("LINEAFORGE_FRONTEND_DIR") or (Path(__file__).resolve().parents[1] / "web" / "dist"))
    if frontend.is_dir() and (frontend / "index.html").is_file():
        # React uses local view state; static serving never exposes the job/data directory.
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app
