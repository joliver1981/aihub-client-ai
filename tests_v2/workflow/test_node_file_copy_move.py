"""File node copy/move — overwrite behavior (allowOverwrite, default True).

These run against the live engine on localhost:5001, which shares this
machine's filesystem, so each test builds its own temp sandbox.
"""
from __future__ import annotations

import os
import shutil
import tempfile

import pytest


@pytest.fixture
def sandbox():
    d = tempfile.mkdtemp(prefix="aihub_file_node_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _make_file(path, content):
    with open(path, "w") as f:
        f.write(content)
    return path


def _file_op_node(make_node, operation, file_path, destination_path,
                  allow_overwrite=None):
    config = {
        "operation": operation,
        "filePath": file_path,
        "destinationPath": destination_path,
    }
    if allow_overwrite is not None:
        config["allowOverwrite"] = allow_overwrite
    return make_node("node-0", "File", config=config,
                     label=f"{operation}_file", is_start=True)


def _run_op(save_workflow, run_workflow, make_node, name, **kwargs):
    node = _file_op_node(make_node, **kwargs)
    wid = save_workflow(name, nodes=[node])
    return run_workflow(wid, timeout=30)


def test_move_overwrites_existing_file_by_default(sandbox, save_workflow,
                                                  run_workflow, make_node):
    src = _make_file(os.path.join(sandbox, "src.txt"), "NEW")
    dest = _make_file(os.path.join(sandbox, "dest.txt"), "OLD")
    result = _run_op(save_workflow, run_workflow, make_node,
                     "move_overwrite_default",
                     operation="move", file_path=src, destination_path=dest)
    assert result["status"] == "completed", f"steps={result['steps']}"
    assert not os.path.exists(src), "source should be gone after move"
    with open(dest) as f:
        assert f.read() == "NEW"


def test_move_to_directory_overwrites_existing_by_default(sandbox, save_workflow,
                                                          run_workflow, make_node):
    """The client scenario: destination is a folder already containing a
    same-named file. Previously failed with 'Destination path already exists'."""
    src = _make_file(os.path.join(sandbox, "report.txt"), "NEW")
    dest_dir = os.path.join(sandbox, "out")
    os.makedirs(dest_dir)
    _make_file(os.path.join(dest_dir, "report.txt"), "OLD")
    result = _run_op(save_workflow, run_workflow, make_node,
                     "move_to_dir_overwrite",
                     operation="move", file_path=src, destination_path=dest_dir)
    assert result["status"] == "completed", f"steps={result['steps']}"
    assert not os.path.exists(src)
    with open(os.path.join(dest_dir, "report.txt")) as f:
        assert f.read() == "NEW"


def test_move_overwrite_disabled_fails_and_preserves_files(sandbox, save_workflow,
                                                           run_workflow, make_node):
    src = _make_file(os.path.join(sandbox, "src.txt"), "NEW")
    dest = _make_file(os.path.join(sandbox, "dest.txt"), "OLD")
    result = _run_op(save_workflow, run_workflow, make_node,
                     "move_no_overwrite",
                     operation="move", file_path=src, destination_path=dest,
                     allow_overwrite=False)
    assert result["status"] in {"failed", "error", "errored"}, (
        f"expected failure with overwrite disabled, got {result['status']}"
    )
    with open(src) as f:
        assert f.read() == "NEW", "source must be untouched on refusal"
    with open(dest) as f:
        assert f.read() == "OLD", "destination must be untouched on refusal"


def test_copy_overwrites_existing_file_by_default(sandbox, save_workflow,
                                                  run_workflow, make_node):
    src = _make_file(os.path.join(sandbox, "src.txt"), "NEW")
    dest = _make_file(os.path.join(sandbox, "dest.txt"), "OLD")
    result = _run_op(save_workflow, run_workflow, make_node,
                     "copy_overwrite_default",
                     operation="copy", file_path=src, destination_path=dest)
    assert result["status"] == "completed", f"steps={result['steps']}"
    assert os.path.exists(src), "source should remain after copy"
    with open(dest) as f:
        assert f.read() == "NEW"


def test_copy_overwrite_disabled_fails_and_preserves_destination(sandbox,
                                                                 save_workflow,
                                                                 run_workflow,
                                                                 make_node):
    src = _make_file(os.path.join(sandbox, "src.txt"), "NEW")
    dest_dir = os.path.join(sandbox, "out")
    os.makedirs(dest_dir)
    _make_file(os.path.join(dest_dir, "src.txt"), "OLD")
    result = _run_op(save_workflow, run_workflow, make_node,
                     "copy_no_overwrite",
                     operation="copy", file_path=src, destination_path=dest_dir,
                     allow_overwrite=False)
    assert result["status"] in {"failed", "error", "errored"}, (
        f"expected failure with overwrite disabled, got {result['status']}"
    )
    with open(os.path.join(dest_dir, "src.txt")) as f:
        assert f.read() == "OLD", "destination must be untouched on refusal"
