"""
T1 hardening: run the code-interpreter child inside a Windows Job Object.

What this buys over plain subprocess timeout/kill:
  * kill-on-timeout covers the WHOLE process tree — a grandchild (a hung pip
    from install(), a stuck driver process) dies with the job instead of
    lingering after the parent is killed;
  * kill-on-close covers the normal path too — anything the code left running
    dies when the run's job handle closes;
  * a job-wide memory cap (CODE_INTERPRETER_MEMORY_MB, default 4096, 0 = off)
    stops a runaway allocation from taking the box down.

Fail-open by design: if any job-object step fails (or off Windows), execution
proceeds unguarded with a logged warning — the lane must never break because
the guard could not arm. docs/code-interpreter-unification-plan.md §5 (T1).
"""

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)

CREATE_SUSPENDED = 0x00000004

_DEFAULT_MEMORY_MB = 4096


def memory_limit_mb() -> int:
    try:
        return int(os.environ.get("CODE_INTERPRETER_MEMORY_MB", str(_DEFAULT_MEMORY_MB)))
    except (TypeError, ValueError):
        return _DEFAULT_MEMORY_MB


if sys.platform == "win32":
    import ctypes as ct
    from ctypes import wintypes as wt

    _kernel32 = ct.WinDLL("kernel32", use_last_error=True)
    _ntdll = ct.WinDLL("ntdll")

    _JobObjectExtendedLimitInformation = 9
    _JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

    class _IO_COUNTERS(ct.Structure):
        _fields_ = [(n, ct.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ct.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wt.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wt.LARGE_INTEGER),
            ("LimitFlags", wt.DWORD),
            ("MinimumWorkingSetSize", ct.c_size_t),
            ("MaximumWorkingSetSize", ct.c_size_t),
            ("ActiveProcessLimit", wt.DWORD),
            ("Affinity", ct.c_size_t),
            ("PriorityClass", wt.DWORD),
            ("SchedulingClass", wt.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ct.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ct.c_size_t),
            ("JobMemoryLimit", ct.c_size_t),
            ("PeakProcessMemoryUsed", ct.c_size_t),
            ("PeakJobMemoryUsed", ct.c_size_t),
        ]

    def _make_job(mem_mb: int):
        """Create + configure the job object; returns a handle or None."""
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            logger.warning("[jobguard] CreateJobObject failed (err %s)",
                           ct.get_last_error())
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if mem_mb and mem_mb > 0:
            info.BasicLimitInformation.LimitFlags |= _JOB_OBJECT_LIMIT_JOB_MEMORY
            info.JobMemoryLimit = int(mem_mb) * 1024 * 1024
        ok = _kernel32.SetInformationJobObject(
            job, _JobObjectExtendedLimitInformation, ct.byref(info), ct.sizeof(info))
        if not ok:
            logger.warning("[jobguard] SetInformationJobObject failed (err %s)",
                           ct.get_last_error())
            _kernel32.CloseHandle(job)
            return None
        return job

    def pid_alive(pid: int) -> bool:
        """Best-effort liveness probe (used by tests)."""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        try:
            code = wt.DWORD()
            if not _kernel32.GetExitCodeProcess(h, ct.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            _kernel32.CloseHandle(h)

    def run_guarded(cmd, cwd, env, timeout, mem_mb=None):
        """Popen inside a job object. Returns
        (returncode, stdout, stderr, timed_out). Mirrors subprocess.run
        semantics the executor expects; raises only on Popen failure."""
        mem_mb = memory_limit_mb() if mem_mb is None else mem_mb
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=CREATE_SUSPENDED,
        )
        job = None
        try:
            # Suspended-launch -> assign -> resume: the child cannot spawn
            # anything before it is inside the job, so coverage is airtight.
            job = _make_job(mem_mb)
            if job and not _kernel32.AssignProcessToJobObject(job, int(proc._handle)):
                logger.warning("[jobguard] AssignProcessToJobObject failed (err %s) — "
                               "running unguarded", ct.get_last_error())
                _kernel32.CloseHandle(job)
                job = None
        finally:
            # The child MUST resume no matter what happened above — a
            # suspended zombie would hang the run until timeout for nothing.
            if _ntdll.NtResumeProcess(int(proc._handle)) != 0:
                logger.warning("[jobguard] NtResumeProcess failed; terminating")
                proc.kill()

        try:
            out, err = proc.communicate(timeout=timeout)
            return proc.returncode, out or "", err or "", False
        except subprocess.TimeoutExpired:
            if job:
                _kernel32.TerminateJobObject(job, 1)   # whole tree, not just the child
            else:
                proc.kill()
            try:
                proc.communicate(timeout=15)
            except Exception:
                pass
            return -1, "", "", True
        finally:
            if job:
                _kernel32.CloseHandle(job)             # kill-on-close reaps stragglers

else:  # non-Windows: plain execution (dev-only path; clients are Windows)
    def pid_alive(pid: int) -> bool:  # pragma: no cover
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False

    def run_guarded(cmd, cwd, env, timeout, mem_mb=None):  # pragma: no cover
        try:
            r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=timeout)
            return r.returncode, r.stdout or "", r.stderr or "", False
        except subprocess.TimeoutExpired:
            return -1, "", "", True
