"""Fail early when a source environment is incomplete or has incompatible pins."""
import importlib.metadata
from pathlib import Path
import sys

if not (3, 12) <= sys.version_info[:2] < (3, 14):
    raise SystemExit("Python 3.12 or 3.13 is required")
requirements = Path(__file__).resolve().parents[1] / "requirements-web.txt"
errors = []
for line in requirements.read_text(encoding="utf-8").splitlines():
    if not line.strip() or line.startswith("#"):
        continue
    if ";" in line and sys.platform != "win32":
        continue
    name, version = line.split(";", 1)[0].strip().split("==")
    try:
        actual = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        actual = "missing"
    if actual != version:
        errors.append(f"{name}: expected {version}, found {actual}")
if errors:
    raise SystemExit("\n".join(errors))
import fastapi, uvicorn, fitz, ezdxf, cv2, mcp
print("Web runtime dependencies verified")
