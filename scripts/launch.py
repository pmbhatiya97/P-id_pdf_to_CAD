"""Source checkout launcher, including optional project-local dependency target."""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
runtime = root / ".runtime"
if runtime.exists():
    import site
    site.addsitedir(str(runtime))
    sys.path.insert(0, str(runtime))
    for directory in (runtime/"win32", runtime/"win32/lib", runtime/"pywin32_system32"):
        sys.path.insert(0, str(directory))
    if hasattr(os, "add_dll_directory"):
        for directory in (runtime/"pywin32_system32", runtime/"PySide6"):
            if directory.exists():
                os.add_dll_directory(str(directory))
from lineaforge.cli import main
main()
