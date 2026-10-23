"""Fail a portable build if a third-party ICU shadows the Windows Qt API."""
from pathlib import Path
import sys
import pefile

internal = Path(sys.argv[1]).resolve()
qt = internal/"Qt6Core.dll"
icu = internal/"icuuc.dll"
if not qt.is_file():
    raise SystemExit("Qt6Core.dll missing from portable build")
if icu.exists():
    caller = pefile.PE(str(qt), fast_load=True)
    caller.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
    required = {i.name for lib in caller.DIRECTORY_ENTRY_IMPORT if lib.dll.lower() == b"icuuc.dll" for i in lib.imports if i.name}
    library = pefile.PE(str(icu), fast_load=True)
    library.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
    provided = {e.name for e in library.DIRECTORY_ENTRY_EXPORT.symbols}
    missing = required-provided
    if missing:
        raise SystemExit("Conflicting bundled ICU: " + ", ".join(sorted(x.decode() for x in missing)) + ". Remove third-party Poppler directories from the build PATH and rebuild.")
print("Qt runtime ICU guard passed: OS ICU is used" if not icu.exists() else "Qt runtime ICU imports verified")
