"""Two source archives: local study evidence, and sanitized website download.

Never put the supplied drawings, crops, dataset report or gold annotations into
the hosted download. The full local delivery archive retains study evidence.
"""
from pathlib import Path
import hashlib
import json
import zipfile

root = Path(__file__).resolve().parent.parent
dist = root / "dist"
dist.mkdir(exist_ok=True)
public_names = {"pyproject.toml", "requirements.lock", "README.md", ".gitignore"}
public_dirs = {"lineaforge", "scripts", "schemas", "installer", "tests"}
public_docs = {"cad_mcp.md", "mcp_tool_schemas.json", "architecture.md", "test_report.md", "limitations.md", "after_first_build.md", "core_after_first_build.md", "cad_packaging_changes.md"}
private_docs = {"dataset_inventory.json", "dataset_gold.partial.json"}


def source_files(private=False):
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in {".runtime", ".webqa", ".venv", ".git", "build", "__pycache__", ".pytest_cache", ".openai", "artifacts", "output"} for part in relative.parts) or relative.parts[0] == "dist":
            continue
        if not path.is_file():
            continue
        if len(relative.parts) == 1 and path.name in public_names:
            yield path, relative
        elif relative.parts[0] in public_dirs:
            yield path, relative
        elif relative.parts[0] == "docs" and (path.name in public_docs or (private and (path.name in private_docs or "dataset_crops" in relative.parts or "gold_overlays" in relative.parts))):
            yield path, relative
        elif relative.parts[0] == "landing" and "source-beta.zip" not in relative.parts:
            yield path, relative
        elif private and path.name == "pid_dataset_report.md":
            yield path, relative


def archive(destination, private):
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path, relative in source_files(private):
            output.write(path, str(relative))
    return {"file": str(destination), "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(), "bytes": destination.stat().st_size}


reports = [archive(dist/"LineaForge-source-with-local-study.zip", True), archive(dist/"LineaForge-source-beta.zip", False)]
website = root/"landing"/"dist"
if website.exists():
    (website/"source-beta.zip").write_bytes((dist/"LineaForge-source-beta.zip").read_bytes())
portable = dist/"LineaForge"
if portable.exists():
    # The executable includes the private catalog for local inference, and stays local.
    target = dist/"LineaForge-Windows-portable.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as output:
        for path in portable.rglob("*"):
            if path.is_file():
                output.write(path, str(path.relative_to(dist)))
    reports.append({"file": str(target), "bytes": target.stat().st_size, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
(dist/"checksums.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")
print(json.dumps(reports, indent=2))
