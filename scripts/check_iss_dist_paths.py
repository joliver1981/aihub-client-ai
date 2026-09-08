"""Cross-check: does every dist\\ path the Inno installer expects exist on disk?

Run before compiling the installer. Exit 1 if anything the .iss references is
missing, so a build that lost a service is caught before it ships.
"""
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ISS = REPO / "AIHub_Setup_Script_v7_OneDir_Dev.iss"

text = ISS.read_text(encoding="utf-8", errors="replace")
names = sorted(set(re.findall(r"dist\\([A-Za-z0-9_\-]+)", text)))

print("path the .iss references           on disk?")
print("-" * 46)
missing = []
for n in names:
    ok = (REPO / "dist" / n).is_dir()
    print("  dist\\%-28s %s" % (n, "yes" if ok else "NO"))
    if not ok:
        missing.append(n)

print()
if missing:
    print("MISSING (%d): %s" % (len(missing), ", ".join(missing)))
    print("The installer will fail to compile, or ship without these.")
    sys.exit(1)
print("All installer-referenced dist trees are present.")
sys.exit(0)
