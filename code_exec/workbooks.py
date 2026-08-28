"""
Workbook sheet-visibility manifest — shared by every code-interpreter surface.

pandas/openpyxl read hidden sheets like any other, so the model is TOLD which
staged sheets are hidden instead of having to infer it — warn-don't-skip
doctrine (james, 2026-08-27): the data stays usable, disclosure becomes
mandatory. Reads xl/workbook.xml with the stdlib only (some service envs have
no openpyxl). Best-effort: any inspection error skips that file; legacy .xls
(BIFF, not a zip) is skipped.
"""

import os


def hidden_sheet_manifest(workdir: str, staged_names: list) -> str:
    """Return a model-facing note listing hidden/veryHidden sheets in the
    staged .xlsx/.xlsm workbooks, or "" when there are none."""
    import zipfile
    import xml.etree.ElementTree as ET
    notes = []
    for name in staged_names:
        if not str(name).lower().endswith((".xlsx", ".xlsm")):
            continue
        try:
            with zipfile.ZipFile(os.path.join(workdir, name)) as zf:
                root = ET.fromstring(zf.read("xl/workbook.xml"))
            hidden = [(el.get("name"), el.get("state")) for el in root.iter()
                      if el.tag.split("}")[-1] == "sheet"
                      and el.get("state") in ("hidden", "veryHidden")]
        except Exception:
            continue
        if hidden:
            listing = ", ".join(f"\"{s}\" ({st})" for s, st in hidden)
            notes.append(f"{name}: {listing}")
    if not notes:
        return ""
    return ("\n\n[Workbook sheet visibility] These staged workbooks contain "
            "HIDDEN sheet(s), which pandas/openpyxl read like any other. If "
            "the answer uses their data, it MUST disclose that it came from a "
            "hidden sheet:\n  " + "\n  ".join(notes))
