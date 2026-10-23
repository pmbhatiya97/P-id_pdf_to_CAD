"""Package only product sources; user drawings, settings, logs and credentials stay local."""
from pathlib import Path
import hashlib
import json
import zipfile

root = Path(__file__).resolve().parent.parent
dist = root / "dist"
dist.mkdir(exist_ok=True)
ROOT_FILES = {"README.md", "pyproject.toml", "requirements-web.txt", "requirements.lock", ".gitignore",
              "Start-LineaForge.cmd", "Run-LineaForge.cmd", "Dockerfile", "compose.yaml", ".dockerignore"}
DIRECTORIES = {"lineaforge", "scripts", "tests", "schemas", "web"}
EXCLUDE = {"node_modules", "__pycache__", ".pytest_cache", ".git", ".openai", "dist"}
DOCS = {"web_product.md", "web_cad_setup.md", "web_test_report.md", "web_after_first_build.md",
        "architecture.md", "limitations.md", "cad_mcp.md"}


def sources():
    for name in sorted(ROOT_FILES):
        path = root / name
        if path.is_file():
            yield path, Path(name)
    for directory in sorted(DIRECTORIES):
        for path in sorted((root / directory).rglob("*")):
            relative = path.relative_to(root)
            if path.is_file() and not any(p in EXCLUDE for p in relative.parts[1:]):
                yield path, relative
    for name in sorted(DOCS):
        path = root / "docs" / name
        if path.is_file():
            yield path, path.relative_to(root)


def evidence():
    for name in ("web-tests.txt", "frozen-web-smoke.json", "web-a11y-scores.json"):
        path = root / "artifacts" / name
        if path.is_file():
            yield path, Path("verification") / name


def archive(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as output:
        for source, relative in files:
            if relative.parts[0] == "verification" and source.suffix == ".txt":
                output.writestr(str(relative), source.read_text(encoding="utf-8").replace(str(root), "[workspace]"))
            else:
                output.write(source, str(relative))
    return {"file": path.name, "bytes": path.stat().st_size,
            "sha256": hashlib.file_digest(path.open("rb"), "sha256").hexdigest()}


if __name__ == "__main__":
    records = [archive(dist / "LineaForge-Web-source.zip", [*sources(), *evidence()])]
    marker = dist / "web-current.txt"
    portable = Path(marker.read_text(encoding="utf-8-sig").strip()) if marker.exists() else dist / "LineaForgeWeb"
    if not portable.is_absolute():
        portable = root / portable
    if not portable.resolve().is_relative_to(dist.resolve()):
        raise ValueError("Portable build must be inside the project dist directory")
    if (portable / "LineaForgeWeb.exe").is_file():
        records.append(archive(dist / "LineaForge-Web-Windows.zip",
            ((p, Path("LineaForgeWeb") / p.relative_to(portable)) for p in sorted(portable.rglob("*")) if p.is_file())))
    (dist / "web-checksums.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(json.dumps(records, indent=2))
