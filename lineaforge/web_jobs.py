"""Bounded background jobs, durable metadata and opaque artifact references."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from . import core, pdf_tools
from .models import PidGraph
from .web_config import ConfigStore, atomic_json


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def opaque_id(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise KeyError("Unknown identifier")
    return value


def safe_name(name: str) -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^\w. ()-]", "_", name, flags=re.UNICODE).strip(" .")[:180]
    return name or "drawing"


class JobManager:
    def __init__(self, root: Path, config: ConfigStore):
        self.root, self.config = root.resolve(), config
        self.files_dir, self.jobs_dir = self.root / "files", self.root / "jobs"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.native_cad_lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lineaforge-job")
        for metadata in self.jobs_dir.glob("*/job.json"):
            try:
                data = json.loads(metadata.read_text(encoding="utf-8"))
                opaque_id(data["id"])
                if data["status"] in {"queued", "running"}:
                    data.update(status="failed", message="Application restarted during this job", error="Interrupted by application restart; retry to run again.")
                    atomic_json(metadata, data)
                self.jobs[data["id"]] = data
            except (ValueError, KeyError, OSError):
                continue

    def close(self):
        for event in self.cancel_events.values():
            event.set()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def file_dir(self, file_id: str) -> Path:
        return self.files_dir / opaque_id(file_id)

    def file(self, file_id: str) -> dict:
        path = self.file_dir(file_id) / "file.json"
        if not path.is_file():
            raise KeyError("File not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def file_path(self, file_id: str) -> Path:
        metadata = self.file(file_id)
        path = (self.file_dir(file_id) / ("source." + metadata["kind"])).resolve()
        if not path.is_relative_to(self.files_dir) or not path.is_file():
            raise KeyError("File not found")
        return path

    def files(self) -> list[dict]:
        items = []
        for item in self.files_dir.glob("*/file.json"):
            try:
                data = json.loads(item.read_text(encoding="utf-8"))
                opaque_id(data["id"])
                items.append(data)
            except (ValueError, KeyError, OSError):
                continue
        return sorted(items, key=lambda item: item.get("created_at", ""), reverse=True)

    def _job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / opaque_id(job_id)

    def _save(self, job: dict):
        atomic_json(self._job_dir(job["id"]) / "job.json", job)

    def get(self, job_id: str) -> dict:
        with self.lock:
            opaque_id(job_id)
            if job_id not in self.jobs:
                raise KeyError("Job not found")
            return json.loads(json.dumps(self.jobs[job_id]))

    def list(self) -> list[dict]:
        with self.lock:
            return [self.get(job["id"]) for job in sorted(self.jobs.values(), key=lambda item: item["created_at"], reverse=True)]

    def event(self, job_id: str, message: str, progress=None, agent="system", status="running"):
        with self.lock:
            job = self.jobs[job_id]
            value = job["progress"] if progress is None else max(0, min(100, float(progress)))
            job["progress"] = max(job["progress"], value)
            # Never include model response payloads or credentials in event messages.
            message = str(message)[:2000]
            job["message"] = message
            job["updated_at"] = now()
            seq = job["events"][-1]["seq"] + 1 if job["events"] else 1
            job["events"].append({"seq": seq, "time": now(), "agent": str(agent), "status": str(status), "message": message, "progress": job["progress"]})
            job["events"] = job["events"][-1000:]
            self._save(job)

    def submit(self, kind: str, file_ids: list[str], options: dict, cloud_consent=False) -> dict:
        if kind not in {"convert", "pid", "cad", "pdf_tools"}:
            raise ValueError("Unknown job type")
        if kind != "cad" and not file_ids:
            raise ValueError("Choose an uploaded drawing first")
        if len(file_ids) > 100:
            raise ValueError("A job supports at most 100 uploaded files")
        for file_id in file_ids:
            self.file_path(file_id)
        if kind in {"convert", "pid"} and len(file_ids) != 1:
            raise ValueError("Choose one drawing for this job")
        config = self.config.private()
        if kind in {"pid", "cad"} and not cloud_consent and any(config["models"][model]["mode"] == "cloud" for model in {"vlm", "llm"}):
            raise PermissionError("Cloud models require explicit consent for this drawing job")
        if kind == "cad":
            graph = self.graph(str(options.get("source_job_id", "")))
            self.check_cad_gate(graph)
        job_id = uuid.uuid4().hex
        job = {"id": job_id, "type": kind, "status": "queued", "progress": 0,
               "message": "Queued", "created_at": now(), "updated_at": now(),
               "file_ids": file_ids, "options": options, "cloud_consent": bool(cloud_consent),
               "events": [], "result": None, "artifacts": [], "error": None}
        with self.lock:
            # Limit the queue as well as worker count.
            if sum(item["status"] in {"queued", "running"} for item in self.jobs.values()) >= 20:
                raise ValueError("The job queue is full; wait for a running job to finish")
            self.jobs[job_id] = job
            self.cancel_events[job_id] = threading.Event()
            (self._job_dir(job_id) / "artifacts").mkdir(parents=True)
            if kind == "cad":
                # Capture exactly the approved graph at submission, before later
                # edits or queued work can change what the operator authorized.
                atomic_json(self._job_dir(job_id) / "artifacts" / "reviewed_graph.json", graph.model_dump())
            self._save(job)
            self.executor.submit(self._run, job_id, config)
            return self.get(job_id)

    def cancel(self, job_id):
        with self.lock:
            job = self.get(job_id)
            if job["status"] not in {"queued", "running"}:
                return job
            self.cancel_events[job_id].set()
            self.event(job_id, "Cancellation requested; an in-flight model or CAD request may finish before stopping")
            return self.get(job_id)

    def retry(self, job_id):
        job = self.get(job_id)
        if job["status"] not in {"failed", "cancelled"}:
            raise ValueError("Only failed or cancelled jobs can be retried")
        # A new job must obtain fresh consent; saved configuration may now point elsewhere.
        return self.submit(job["type"], job["file_ids"], job["options"], False)

    def graph(self, job_id) -> PidGraph:
        self.get(job_id)
        path = self._job_dir(job_id) / "artifacts" / "graph.json"
        if not path.exists():
            raise KeyError("This job has no P&ID graph")
        return PidGraph.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def check_cad_gate(graph: PidGraph):
        if not graph.elements or not any(item.status == "ACCEPTED" for item in graph.elements):
            raise ValueError("CAD generation requires a nonempty reviewed graph")
        if any(item.status not in {"ACCEPTED", "REJECTED"} for item in graph.elements) or any(item.status == "NEEDS_HUMAN" for item in graph.review):
            raise ValueError("Review all elements and resolve every finding before creating CAD")
        if any(item.status == "ACCEPTED" and (item.tag == "UNREADABLE" or item.text.strip().upper() == "UNREADABLE") for item in graph.elements):
            raise ValueError("Unreadable placeholders must be replaced with verified evidence or rejected before CAD")

    def put_graph(self, job_id: str, graph: PidGraph):
        with self.lock:
            existing = self.graph(job_id)  # Arbitrary jobs cannot acquire a graph.
            job = self.jobs[job_id]
            if job["status"] in {"queued", "running"}:
                raise ValueError("Wait for extraction to finish before editing its graph")
            prior = {element.id: element for element in existing.elements}
            for key in ("schema_version", "page_id", "width_px", "height_px", "width_mm", "height_mm"):
                if getattr(graph, key) != getattr(existing, key):
                    raise ValueError("Source page identity and coordinate calibration cannot be changed by a graph edit")
            for element in graph.elements:
                previous = prior.get(element.id)
                if previous and (element.tile_id != previous.tile_id or element.source_agent != previous.source_agent):
                    raise ValueError("Existing element source provenance cannot be changed")
                if previous is None and (element.source_agent != "human" or element.tile_id != "manual"):
                    raise ValueError("New manual elements must use source_agent=human and tile_id=manual")
                if previous and previous.status == "UNREADABLE" and element.status == "ACCEPTED":
                    changed = any(getattr(previous, key) != getattr(element, key) for key in ("tag", "text", "points"))
                    if not changed or element.tag == "UNREADABLE" or element.text.strip().upper() == "UNREADABLE":
                        raise ValueError("Unreadable elements require verified content edits or rejection")
            output = self._job_dir(job_id) / "artifacts"
            from .pipeline import export_graph
            staging = output / (".review-" + uuid.uuid4().hex)
            export_graph(graph, staging)
            for artifact in staging.iterdir():
                for attempt in range(6):
                    try:
                        os.replace(artifact, output / artifact.name)
                        break
                    except PermissionError:
                        if attempt == 5:
                            raise
                        time.sleep(.01 * 2 ** attempt)
            staging.rmdir()
            atomic_json(output / "graph.json", graph.model_dump())
            prior_values = {element.id: element.model_dump() for element in existing.elements}
            updated_values = {element.id: element.model_dump() for element in graph.elements}
            decision = {"time": now(), "actor": "operator", "action": "reviewed_graph_edit",
                        "graph_sha256": hashlib.sha256(graph.model_dump_json().encode()).hexdigest(),
                        "changed_element_ids": [key for key, value in updated_values.items() if prior_values.get(key) != value],
                        "removed_element_ids": sorted(set(prior_values) - set(updated_values))}
            with (output / "review_audit.jsonl").open("a", encoding="utf-8") as journal:
                journal.write(json.dumps(decision) + "\n")
            try:
                self.check_cad_gate(graph)
                job["status"] = "completed"
                job["message"] = "All graph elements and findings reviewed; ready for CAD"
            except ValueError:
                job["status"] = "needs_review"
                job["message"] = "Graph saved; review is required"
            job["artifacts"] = self._artifacts(job_id, output)
            if isinstance(job.get("result"), dict) and "graph" in job["result"]:
                job["result"]["graph"] = self._public_result(graph.model_dump())
                job["result"]["review"] = self._public_result([item.model_dump() for item in graph.review])
                job["result"]["status"] = "REVIEWED" if job["status"] == "completed" else "NEEDS_REVIEW"
            self._save(job)
            return graph

    def review(self, job_id: str, action: str, element_id=None, review_id=None, updates=None):
        if action not in {"accept", "reject", "edit"}:
            raise ValueError("Unknown review action")
        with self.lock:
            graph = self.graph(job_id).model_dump()
            review = next((item for item in graph["review"] if item["id"] == review_id), None) if review_id else None
            if review_id and review is None:
                raise KeyError("Review finding not found")
            element_id = element_id or (review or {}).get("element_id")
            element = next((item for item in graph["elements"] if item["id"] == element_id), None) if element_id else None
            if element_id and element is None:
                raise KeyError("Graph element not found")
            if element is None and review is None:
                raise ValueError("Choose an element or review finding")
            if element is not None:
                if action == "accept" and element["status"] == "UNREADABLE":
                    raise ValueError("Unreadable evidence must be edited with verified content or rejected; it cannot simply be accepted")
                if action == "edit":
                    updates = dict(updates or {})
                    if not updates:
                        raise ValueError("Supply the verified changes")
                    if "id" in updates or "tile_id" in updates or "source_agent" in updates:
                        raise ValueError("Source identity cannot be changed by a review edit")
                    if element["status"] == "UNREADABLE" and not any(updates.get(key) for key in ("tag", "text", "points")):
                        raise ValueError("Unreadable evidence needs verified text, tag, or geometry before acceptance")
                    element.update(updates)
                    if element.get("tag") == "UNREADABLE" and "tag" not in updates:
                        element["tag"] = None
                element["status"] = "REJECTED" if action == "reject" else "ACCEPTED"
            if review:
                review["status"] = "REJECTED" if action == "reject" else "ACCEPTED"
            return self.put_graph(job_id, PidGraph.model_validate(graph))

    def artifact_path(self, job_id: str, artifact_id: str) -> tuple[Path, dict]:
        job = self.get(job_id)
        opaque_id(artifact_id)
        artifact = next((item for item in job["artifacts"] if item["id"] == artifact_id), None)
        if not artifact:
            raise KeyError("Artifact not found")
        root = (self._job_dir(job_id) / "artifacts").resolve()
        path = (root / artifact["relative_path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise KeyError("Artifact not found")
        return path, artifact

    def _artifacts(self, job_id, output: Path):
        artifacts = []
        existing = {item["relative_path"]: item["id"] for item in self.jobs.get(job_id, {}).get("artifacts", [])}
        for path in sorted(output.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(output.resolve()) and not any(part.startswith(".") for part in path.relative_to(output).parts):
                artifact_id = existing.get(path.relative_to(output).as_posix()) or uuid.uuid4().hex
                artifacts.append({"id": artifact_id, "name": path.relative_to(output).as_posix(),
                                  "relative_path": path.relative_to(output).as_posix(), "size": path.stat().st_size,
                                  "kind": path.suffix.lstrip("."), "url": f"/api/jobs/{job_id}/artifacts/{artifact_id}"})
        return artifacts

    def _public_result(self, value):
        if isinstance(value, dict):
            return {key: self._public_result(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._public_result(item) for item in value]
        if isinstance(value, Path):
            return value.name
        if isinstance(value, str):
            return value.replace(str(self.root).replace("\\", "\\\\"), "[workspace]").replace(str(self.root), "[workspace]")
        return value

    @contextmanager
    def _cad_slot(self, job_id, backend, cancel):
        if backend == "ezdxf":
            yield
            return
        self.event(job_id, "Waiting for the native CAD workspace", agent="cad_builder")
        while not self.native_cad_lock.acquire(timeout=.2):
            core._check_cancel(cancel)
        try:
            core._check_cancel(cancel)
            yield
        finally:
            self.native_cad_lock.release()

    def _run(self, job_id: str, config: dict):
        job = self.get(job_id)
        cancel = self.cancel_events[job_id]
        output = self._job_dir(job_id) / "artifacts"
        def progress(value, message):
            core._check_cancel(cancel)
            self.event(job_id, message, value)
        def agent_progress(event):
            core._check_cancel(cancel)
            if isinstance(event, dict):
                self.event(job_id, event.get("message", event.get("task", "Agent working")), event.get("progress"), event.get("agent", "orchestrator"), event.get("status", "running"))
            else:
                self.event(job_id, str(event), agent="orchestrator")
        try:
            core._check_cancel(cancel)
            with self.lock:
                self.jobs[job_id]["status"] = "running"
                self._save(self.jobs[job_id])
            self.event(job_id, "Starting job", 1)
            sources = [self.file_path(file_id) for file_id in job["file_ids"]]
            options = dict(job["options"])
            kind = job["type"]
            status = "completed"
            converter = core.ConverterConfig(converter_path=config["cad"].get("converter_path") or None,
                converter_kind=config["cad"].get("converter_kind", "oda"), version=str(options.get("version", config["cad"].get("version", "R2018"))), timeout=config["cad"]["timeout_seconds"])
            if kind == "convert":
                target = str(options.get("output_format", options.get("target", "dxf" if sources[0].suffix.lower() == ".pdf" else "pdf"))).lower().lstrip(".")
                if target not in {"pdf", "dxf", "dwg"}:
                    raise ValueError("Choose PDF, DXF, or DWG output")
                allowed = {key: options[key] for key in ("pages", "scale", "paper", "orientation", "area", "monochrome", "lineweights", "raster", "password") if key in options}
                result = core.convert_file(sources[0], output / ("drawing." + target), converter, progress=progress, cancel=cancel, **allowed)
            elif kind == "pdf_tools":
                operation = options.get("operation", "merge")
                common = {"progress": progress, "cancel": cancel}
                if operation == "merge":
                    result = pdf_tools.merge(sources, output / "merged.pdf", **common)
                elif operation == "split":
                    result = pdf_tools.split(sources[0], output / "parts", ranges=options.get("ranges"), every_n=options.get("every_n", 1), **common)
                elif operation == "compress":
                    result = pdf_tools.compress(sources[0], output / "compressed.pdf", quality=options.get("quality", options.get("level", "balanced")), **common)
                elif operation == "organize":
                    result = pdf_tools.organize(sources[0], output / "organized.pdf", order=options.get("order", "all"), rotations=options.get("rotations", {}), **common)
                else:
                    raise ValueError("Unknown PDF operation")
            elif kind == "pid":
                if sources[0].suffix.lower() != ".pdf":
                    raise ValueError("P&ID extraction currently requires a PDF drawing")
                from .agentic import run_agentic_job
                if "apply_noise_removal" in options:
                    if not isinstance(options["apply_noise_removal"], bool):
                        raise ValueError("apply_noise_removal must be true or false")
                    config["pipeline"]["apply_noise_removal"] = options["apply_noise_removal"]
                result = run_agentic_job(sources[0], config, output, page=int(options.get("page", 0)), cloud_consent=job["cloud_consent"], progress=agent_progress, cancel=cancel)
                graph = result.graph if hasattr(result, "graph") else PidGraph.model_validate(result["graph"])
                atomic_json(output / "graph.json", graph.model_dump())
                result = result.model_dump() if hasattr(result, "model_dump") else result
                status = "completed" if str(result.get("status", "")).startswith("SKIPPED") else "needs_review"
            elif kind == "cad":
                snapshot = output / "reviewed_graph.json"
                graph = PidGraph.model_validate_json(snapshot.read_text(encoding="utf-8"))
                self.check_cad_gate(graph)
                from .cad import build_graph_cad
                from .mcp_connections import make_backend
                cad_config = dict(config["cad"])
                cad_config["backend"] = options.get("backend", cad_config["backend"])
                progress(10, "Connecting to CAD backend")
                suffix = str(options.get("output_format", "dwg" if cad_config["backend"] == "autocad" else "dxf")).lower().lstrip(".")
                if suffix not in {"dxf", "dwg"} or (cad_config["backend"] == "ezdxf" and suffix != "dxf"):
                    raise ValueError("Choose DXF for the offline backend, or DXF/DWG for a native CAD backend")
                with self._cad_slot(job_id, cad_config["backend"], cancel):
                    backend = make_backend(cad_config, output, allow_external=job["cloud_consent"])
                    result = build_graph_cad(graph, backend, output / ("drawing." + suffix), mode=options.get("mode", "fidelity"))
                    result["reviewed_graph_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
                    core._check_cancel(cancel)
                    from .agentic_qa import verify_built_cad
                    progress(75, "Reopening CAD and verifying source evidence")
                    result["qa"] = verify_built_cad(graph, output / ("drawing." + suffix),
                        self._job_dir(str(options["source_job_id"])) / "artifacts", output / "cad-qa", config,
                        cloud_consent=job["cloud_consent"], progress=agent_progress, cancel=cancel)
                status = "needs_review"  # structural checks never establish engineering fidelity
                atomic_json(output / "cad_report.json", self._public_result(result))
            core._check_cancel(cancel)
            with self.lock:
                self.jobs[job_id].update(status=status, progress=100, result=self._public_result(result), artifacts=self._artifacts(job_id, output), updated_at=now())
                self.event(job_id, "Complete; human review is required" if status == "needs_review" else "Complete", 100, status=status)
        except Exception as exc:
            cancelled = cancel.is_set() or isinstance(exc, core.ConversionCancelled)
            # Provider errors deliberately carry no raw response or credentials.
            message = self._public_result(str(exc))[:2000]
            for provider in config["models"].values():
                if provider.get("api_key"):
                    message = message.replace(provider["api_key"], "[redacted]")
            with self.lock:
                try:
                    evidence = self._artifacts(job_id, output)
                except OSError:
                    evidence = []
                self.jobs[job_id].update(status="cancelled" if cancelled else "failed", error=None if cancelled else message, updated_at=now(), artifacts=evidence)
                self.event(job_id, "Cancelled" if cancelled else message, status="cancelled" if cancelled else "failed")
