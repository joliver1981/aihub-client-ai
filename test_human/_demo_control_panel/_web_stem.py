"""Shared rule for naming a demo's generated web playbook (playbooks/<stem>.html).

The control panel maps a demo's `doc` to a served HTML file by this stem, and
export_playbooks.py writes the HTML under the same stem — so they must agree.

- .docx masters (C:\\temp\\AIHub_Demo\\*.docx): stem = the file's basename
  (unique across the set), the historical behavior.
- .md scenarios (test_human/21_The_Agent_Competency/NN_*/SCENARIO.md): every
  file is named SCENARIO.md, so the basename collides. Use the PARENT FOLDER
  name instead (01_Document_Ingest_Pipeline, …) which is unique per scenario.
"""
import os


def web_stem(doc):
    base, ext = os.path.splitext(os.path.basename(doc or ""))
    if ext.lower() in (".md", ".markdown"):
        return os.path.basename(os.path.dirname(doc)) or base
    return base
