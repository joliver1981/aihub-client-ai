"""
code_exec — shared code-interpreter execution backend.

One executor, three thin tool bindings (GeneralAgent run_python_code, Command
Center run_python, The Agent): each surface's own model writes the Python; this
package decides WHICH interpreter runs it, WHAT environment the child process
sees (denylist secret-scrub), and provides the script preamble (matplotlib
headless, bundle DLLs, aihub_runtime SDK path, default-open install() helper).

Surface-specific concerns stay in the surfaces: file STAGING (each surface has
its own upload store) and artifact HARVEST/registration (each has its own
artifact store). This package only offers the generic workdir snapshot/diff.

Design: docs/code-interpreter-unification-plan.md
"""

from code_exec.interpreter import (  # noqa: F401
    NOT_CONFIGURED_MSG,
    bundle_python,
    resolve_interpreter,
)
from code_exec.envbuild import build_child_env, dotenv_key_names  # noqa: F401
from code_exec.executor import new_files, run_script, snapshot  # noqa: F401
from code_exec.preamble import (  # noqa: F401
    adhoc_package_dir,
    build_preamble,
    policy_files,
)
from code_exec.workbooks import hidden_sheet_manifest  # noqa: F401
