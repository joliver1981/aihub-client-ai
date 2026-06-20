"""
portal_runner.py - drive browser-use to log into a portal and download files.

Pinned to the installed browser-use 0.12.x API (verified via introspect_browser_use.py):
    from browser_use import Agent, ChatOpenAI, BrowserSession
    session = BrowserSession(headless=..., downloads_path=..., accept_downloads=True)
    agent   = Agent(task=..., llm=ChatOpenAI(model=...), browser_session=session, sensitive_data=...)
    history = await agent.run(max_steps=...)

Two robustness choices:
1. Harvest by DIFFING the download dir (snapshot before/after) - we own download_dir, so we
   report whatever new/changed files appear regardless of browser-use internals.
2. Credentials go through browser-use `sensitive_data` (placeholder -> value), so secrets are
   typed into the page but never shown to the LLM or written to logs.

browser-use 0.12.x talks to Chrome over CDP (cdp-use), NOT Playwright. A local Chrome/Chromium
must be available on the host; set executable_path/channel on BrowserSession if it isn't found.
"""
import asyncio
import os
import time


_PARTIAL_SUFFIXES = (".crdownload", ".partial", ".part", ".tmp")


def _snapshot(d):
    """Recursive fingerprint of a download dir: relpath -> (size, mtime_ns). Skips in-flight
    partial-download files so a half-written download isn't counted as a finished one."""
    snap = {}
    for root, _dirs, files in os.walk(d):
        for n in files:
            if n.lower().endswith(_PARTIAL_SUFFIXES):
                continue
            p = os.path.join(root, n)
            try:
                st = os.stat(p)
                snap[os.path.relpath(p, d)] = (st.st_size, st.st_mtime_ns)
            except OSError:
                continue
    return snap


def _build_llm(model):
    """Build the browser-use provider wrapper for `model`. browser-use ships ChatAnthropic and
    ChatOpenAI (0.12.x); both read their RAW api key from os.environ, so populate it first
    (AI Hub stores LLM keys encrypted — see browser_use_config.ensure_llm_api_key)."""
    try:
        import browser_use_config as _cfg
        _cfg.ensure_llm_api_key(model)
    except Exception:
        pass  # fail-soft: let the provider SDK surface a missing-key error
    m = (model or "").lower()
    if m.startswith("claude") or m.startswith("anthropic"):
        from browser_use import ChatAnthropic
        return ChatAnthropic(model=model)
    from browser_use import ChatOpenAI
    return ChatOpenAI(model=model)


def _build_session(download_dir, headless, allowed_domains=None):
    """0.12.x BrowserSession takes headless / downloads_path / accept_downloads directly.
    `allowed_domains` (when given) hard-limits navigation at the browser layer — the agent
    cannot be talked into leaving the portal's domain (prompt-injection containment)."""
    from browser_use import BrowserSession
    kwargs = dict(headless=headless, downloads_path=download_dir, accept_downloads=True)
    if allowed_domains:
        kwargs["allowed_domains"] = allowed_domains
    return BrowserSession(**kwargs)


def _build_agent(task, llm, session, sensitive_data):
    from browser_use import Agent
    kwargs = dict(task=task, llm=llm, browser_session=session)
    if sensitive_data:
        kwargs["sensitive_data"] = sensitive_data
    return Agent(**kwargs)


def _extract_final(history):
    """Pull a human-readable final result from the browser-use history object if present."""
    for attr in ("final_result", "final_answer"):
        val = getattr(history, attr, None)
        if callable(val):
            try:
                return val()
            except Exception:
                pass
        elif val is not None:
            return val
    return str(history) if history is not None else None


async def run_portal_fetch(task, start_url, creds, download_dir, llm_model,
                           headless=True, max_steps=50, timeout=300, allowed_domains=None):
    """Open `start_url`, log in (if creds given), pursue `task`, return a manifest of any
    files that landed in `download_dir`. `allowed_domains`, when set, confines the browser to
    those domains (navigation allowlist enforced outside the LLM)."""
    os.makedirs(download_dir, exist_ok=True)
    before = _snapshot(download_dir)
    started = time.time()

    # Compose the agent task: open portal -> log in with placeholders -> user goal.
    sensitive_data = {}
    login_hint = ""
    if creds.get("username") and creds.get("password"):
        sensitive_data["portal_username"] = creds["username"]
        sensitive_data["portal_password"] = creds["password"]
        login_hint = " Log in using the placeholders portal_username and portal_password."
    if creds.get("totp_secret"):
        try:
            import pyotp
            sensitive_data["portal_totp"] = pyotp.TOTP(creds["totp_secret"]).now()
            login_hint += " If asked for a 2FA/one-time code, use portal_totp."
        except Exception:
            pass

    full_task = (
        f"Go to {start_url}.{login_hint} Then: {task}. "
        "IMPORTANT: downloaded files are saved AUTOMATICALLY by the system to the correct "
        "folder. Do NOT choose a save location, do NOT type a file path, and IGNORE any "
        "instruction to save to a specific local folder (e.g. C:\\tmp) - just trigger the "
        "download from the page itself. As soon as a file has downloaded once the task is "
        "complete - call done and stop; do NOT click the download again or repeat steps. Only "
        "report a file as downloaded if the browser actually downloaded it."
    )

    llm = _build_llm(llm_model)
    session = _build_session(download_dir, headless, allowed_domains)
    agent = _build_agent(full_task, llm, session, sensitive_data or None)

    error = None
    final = None
    try:
        try:
            coro = agent.run(max_steps=max_steps)
        except TypeError:  # older/newer run() without max_steps
            coro = agent.run()
        history = await asyncio.wait_for(coro, timeout=timeout)
        final = _extract_final(history)
    except asyncio.TimeoutError:
        error = f"timed out after {timeout}s"
    except Exception as e:
        error = str(e)
    finally:
        # best-effort close so Chrome doesn't linger between requests
        try:
            close = getattr(session, "close", None) or getattr(session, "stop", None)
            if close:
                res = close()
                if asyncio.iscoroutine(res):
                    await res
        except Exception:
            pass

    # A one-shot diff can miss a download still writing when agent.run() returns. Wait (bounded)
    # for the file set to change and any partial-download files to clear before diffing. If
    # nothing ever downloads (e.g. the link opened inline), this just times out and the harvest
    # is correctly empty.
    import glob as _glob
    after = _snapshot(download_dir)
    for _ in range(5):
        partials = (_glob.glob(os.path.join(download_dir, "**", "*.crdownload"), recursive=True)
                    + _glob.glob(os.path.join(download_dir, "**", "*.partial"), recursive=True))
        if after != before and not partials:
            break
        await asyncio.sleep(1)
        after = _snapshot(download_dir)
    new_files = [
        os.path.join(download_dir, rel)
        for rel, fp in after.items()
        if rel not in before or before[rel] != fp
    ]

    return {
        "status": "ok" if error is None else "error",
        "error": error,
        "elapsed_seconds": round(time.time() - started, 1),
        "files": new_files,
        "file_count": len(new_files),
        "final_result": final,
    }
