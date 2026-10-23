"""Validated, durable web settings. Model credentials never leave this module in public views."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ModelSettings(SettingsModel):
    base_url: str = "http://127.0.0.1:8000/v1"
    model: str = ""
    mode: Literal["local", "cloud"] = "local"
    api_key: str = ""

    @field_validator("base_url")
    @classmethod
    def url(cls, value):
        value = value.strip().rstrip("/")
        parts = urlsplit(value)
        _ = parts.port  # Validate malformed/out-of-range ports before httpx sees the URL.
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("Use an HTTP(S) model base URL without credentials, query, or fragment")
        return value


class ModelsSettings(SettingsModel):
    vlm: ModelSettings = Field(default_factory=ModelSettings)
    llm: ModelSettings = Field(default_factory=ModelSettings)


class CadSettings(SettingsModel):
    backend: Literal["ezdxf", "autocad", "solidworks"] = "ezdxf"
    transport: Literal["stdio", "streamable_http"] = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list, max_length=100)
    endpoint: str = ""
    auth_token_env: str = ""
    remote_output_root: str = ""
    template_path: str = ""
    timeout_seconds: float = Field(default=120, ge=1, le=3600)
    converter_path: str = ""
    converter_kind: Literal["oda", "libredwg"] = "oda"
    version: str = "R2018"


class AgentSettings(SettingsModel):
    enabled_roles: list[str] = Field(default_factory=list, max_length=100)
    assignments: dict[str, Literal["vlm", "llm"]] = Field(default_factory=dict)
    prompt_overrides: dict[str, str] = Field(default_factory=dict)
    max_parallel: int = Field(default=4, ge=1, le=16)
    max_calls: int = Field(default=1500, ge=1, le=10000)
    max_seconds: int = Field(default=7200, ge=1, le=86400)
    estimated_cost_per_call: float = Field(default=.02, ge=0, le=100)
    max_estimated_cost: float = Field(default=30, ge=0, le=10000)


class PipelineConfig(SettingsModel):
    dpi: int = Field(default=300, ge=300, le=600)
    tile_size: int = Field(default=1536, ge=256, le=4096)
    overlap: float = Field(default=.2, ge=.05, le=.5)
    confidence_threshold: float = Field(default=.85, ge=0, le=1)
    keep_markups: bool = True


class WebSettings(SettingsModel):
    models: ModelsSettings = Field(default_factory=ModelsSettings)
    cad: CadSettings = Field(default_factory=CadSettings)
    agents: AgentSettings = Field(default_factory=AgentSettings)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    # Antivirus and OneDrive can briefly retain Windows file handles after close.
    for attempt in range(6):
        try:
            os.replace(temporary, path)
            break
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(.01 * 2 ** attempt)


def _merge(original: dict, patch: dict) -> dict:
    result = dict(original)
    for key, value in patch.items():
        result[key] = _merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


class ConfigStore:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "config.json"
        self.lock = threading.RLock()
        self.value = WebSettings()
        if self.path.exists():
            self.value = WebSettings.model_validate_json(self.path.read_text(encoding="utf-8"))
        else:
            # Enable the catalog by default; a saved empty list intentionally disables optional roles.
            try:
                from .agentic import AGENT_CATALOG
                self.value.agents.enabled_roles = [item["id"] for item in AGENT_CATALOG if item["id"] != "repair"]
            except ImportError:
                pass

    def private(self) -> dict:
        with self.lock:
            return self.value.model_dump()

    def public(self) -> dict:
        value = self.private()
        for model in value["models"].values():
            model["api_key_set"] = bool(model.pop("api_key"))
        return value

    def update(self, patch: dict) -> dict:
        # Make a separate copy: failed validation never changes persisted settings.
        patch = json.loads(json.dumps(patch, allow_nan=False))
        models = patch.get("models", {})
        if not isinstance(models, dict):
            raise ValueError("models must be an object")
        for model in models.values():
            if not isinstance(model, dict):
                raise ValueError("model settings must be objects")
            model.pop("api_key_set", None)
            clear = model.pop("clear_api_key", False)
            if clear:
                model["api_key"] = ""
            elif not model.get("api_key"):
                model.pop("api_key", None)
        with self.lock:
            validated = WebSettings.model_validate(_merge(self.private(), patch))
            try:
                from .agentic import AGENT_CATALOG
            except ImportError:
                AGENT_CATALOG = []
            if AGENT_CATALOG:
                roles = {item["id"] for item in AGENT_CATALOG}
                enabled = set(validated.agents.enabled_roles)
                unknown = enabled - roles
                missing = {item["id"] for item in AGENT_CATALOG if item.get("required")} - enabled
                if unknown:
                    raise ValueError("Unknown agent roles: " + ", ".join(sorted(unknown)))
                if missing:
                    raise ValueError("Required agents cannot be disabled: " + ", ".join(sorted(missing)))
                if set(validated.agents.assignments) - roles or set(validated.agents.prompt_overrides) - roles:
                    raise ValueError("Agent assignments and prompts must use catalog role IDs")
            atomic_json(self.path, validated.model_dump())
            self.value = validated
            return self.public()
