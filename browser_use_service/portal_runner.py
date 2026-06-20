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


def _dbg(*a):
    """Stderr trace for the headless-download fallback, gated on PORTAL_DL_DEBUG. No-op otherwise."""
    if os.getenv("PORTAL_DL_DEBUG"):
        import sys
        print(">>>DL", *a, file=sys.stderr, flush=True)


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
    cannot be talked into leaving the portal's domain (prompt-injection containment).

    NOTE: headless Chrome silently drops office/binary download-navigations (.docx/.xlsx/.zip)
    it deems risky - no network request, no download event, nothing on disk (plain text/CSV is
    allowed). run_portal_fetch works around this with a click-capture + in-page-fetch fallback
    (see _make_download_capturer), so HEADLESS downloads now work and are in fact the more robust
    mode: they need no interactive desktop, unlike headed Chrome (which can't show a window under
    an NSSM service / session 0). Headed remains available as a fallback for exotic portals."""
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


# Per-session capture of the href the agent CLICKS, keyed by id(browser_session).
# Why a monkeypatch instead of a JS click listener: in HEADLESS Chrome, browser-use's click on a
# download <a> dispatches NO DOM click event (verified: a capture-phase document listener counts
# zero clicks) and fires NO network request - the download-navigation is silently dropped at the
# click stage. So nothing is observable from inside the page. But browser-use's click handler
# DOES receive the resolved DOM node, whose `.attributes['href']` is exactly the link the agent
# meant to download. We wrap that one method to record the href, then re-pull it via in-page fetch.
_CLICK_CAPTURE = {}


def _install_click_capture_patch():
    """Idempotently wrap DefaultActionWatchdog._click_element_node_impl so every element click
    records the element's href into _CLICK_CAPTURE[id(browser_session)] (only for sessions we're
    tracking). Concurrency-safe: each watchdog uses its own self.browser_session, and we key by
    that session's id, so parallel portal runs never cross-contaminate. Best-effort and fully
    transparent - it always calls the original click and never changes its behavior."""
    from browser_use.browser.watchdogs.default_action_watchdog import DefaultActionWatchdog
    if getattr(DefaultActionWatchdog, "_portal_capture_patched", False):
        return
    _orig = DefaultActionWatchdog._click_element_node_impl

    async def _patched(self, element_node):
        try:
            key = id(getattr(self, "browser_session", None))
            bucket = _CLICK_CAPTURE.get(key)
            if bucket is not None:
                attrs = getattr(element_node, "attributes", None) or {}
                href = attrs.get("href")
                if href:
                    bucket.append(href)
        except Exception:
            pass
        return await _orig(self, element_node)

    DefaultActionWatchdog._click_element_node_impl = _patched
    DefaultActionWatchdog._portal_capture_patched = True


_DL_EXTS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".txt", ".rtf",
            ".odt", ".ods", ".odp", ".zip", ".7z", ".rar", ".xml", ".json", ".tsv", ".eml", ".msg")
# Path/query hints that mark a download endpoint. Slashes are deliberate so "/downloads" (a page)
# does NOT match "/download/" (a download route).
_DL_HINTS = ("/file/", "/files/", "/download/", "/downloads/file", "/export/", "/attachment/",
             "download=", "attachment=", "export=")


def _looks_like_download(url):
    """True if `url` looks like a file download rather than an ordinary page navigation, so we
    only re-pull real downloads (and skip nav links the agent also clicked). Errs toward False.
    Checks the final path segment, then the whole path+query (catches ?file=report.docx), then
    download-route hints. (A bare query extension like ?type=xlsx with no '.' is not detected -
    accepted limitation of URL-shape heuristics with no response headers at this layer.)"""
    try:
        from urllib.parse import urlparse
        low = (url or "").lower()
        if not low.startswith("http"):
            return False
        parsed = urlparse(low)
        last = (parsed.path or "").rsplit("/", 1)[-1]
        path_query = (parsed.path or "") + "?" + (parsed.query or "")
        if any(last.endswith(e) for e in _DL_EXTS) or any(e in path_query for e in _DL_EXTS):
            return True
        return any(h in low for h in _DL_HINTS)
    except Exception:
        return False


def _host_allowed(url, start_url, allowed_domains):
    """Confine the in-page refetch to the portal's own domain. The fallback fetches whatever the
    agent clicked, so without this a malicious/compromised (or prompt-injected) portal page could
    plant a download-styled link to an internal IP (169.254.169.254, 127.0.0.1, 10.x) or an
    off-site host and have us pull it from the AUTHENTICATED browser - an SSRF / exfiltration
    bypass of the allowed_domains navigation allowlist (which only gates navigation, not fetch).
    Allow only: the portal's exact host, its registrable domain (same last-two labels; never for
    bare IPs), or an explicitly-configured extra allowed domain. A bare-IP host is allowed ONLY if
    it equals the portal's own host."""
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return False
        portal_host = (urlparse(start_url or "").hostname or "").lower()
        if host and host == portal_host:
            return True

        def _is_ip(h):
            return bool(h) and h.replace(".", "").isdigit()

        if not _is_ip(host) and not _is_ip(portal_host) and portal_host:
            def _regdom(h):
                labels = h.split(".")
                return ".".join(labels[-2:]) if len(labels) >= 2 else h
            if _regdom(host) == _regdom(portal_host):
                return True
        for d in (allowed_domains or []):
            d = (d or "").lstrip("*.").lower()
            if d and not _is_ip(host) and (host == d or host.endswith("." + d)):
                return True
        return False
    except Exception:
        return False


def _redact(url):
    """Drop the query string (signed/session tokens often live there) for safe debug logging."""
    try:
        from urllib.parse import urlsplit
        s = urlsplit(url or "")
        return (s.scheme + "://" + s.netloc + s.path) if s.scheme else (url or "")
    except Exception:
        return ""


def _make_download_capturer(headless, session, start_url, allowed_domains):
    """Re-pull whatever the agent CLICKS, via an in-page fetch, DURING the run.

    Mechanism (headless only - headed Chrome downloads natively):
      1. We register this run's `session` in _CLICK_CAPTURE and install a one-time patch on
         browser-use's element-click so every click records the clicked element's href there.
      2. on_step_end resolves each captured href to an absolute URL (against the current page,
         falling back to start_url), confirms it looks like a download AND stays within the
         portal's own domain (_host_allowed - prevents an injected off-portal/internal-IP link
         from being fetched), then re-pulls the bytes with browser-use's download_file_from_url -
         an in-page `fetch()` in the authenticated tab. A fetch reads bytes into JS; it is NOT a
         browser download, so the headless "dangerous file" block never applies. The bytes land
         in downloads_path, where the snapshot-diff harvest picks them up unchanged.

    Critically this happens MID-RUN: the Agent tears down the CDP session the moment it calls
    `done`, so a post-run fetch fails ("Root CDP client not initialized"). on_step_end always
    fires while CDP is still alive.

    Returns (on_step_start, on_step_end) hooks for Agent.run. Caller must _CLICK_CAPTURE.pop the
    session id when done (run_portal_fetch does this in its finally)."""
    state = {"fetched": set(), "seen_raw": set()}
    if headless:
        _CLICK_CAPTURE.setdefault(id(session), [])
        try:
            _install_click_capture_patch()
        except Exception as e:
            _dbg("patch install ERR:", repr(e))

    async def _cdp(agent):
        bs = getattr(agent, "browser_session", None)
        if bs is None:
            return None, None
        try:
            return bs, await bs.get_or_create_cdp_session(focus=False)
        except Exception:
            return bs, None

    async def _on_step_start(agent):
        return  # capture is via the click patch; nothing to inject

    async def _on_step_end(agent):
        if not headless:
            return
        # Only handle hrefs we haven't processed yet (by RAW value), so a transient page_url
        # failure can't make a later step re-resolve the same href against the wrong page.
        fresh = [h for h in _CLICK_CAPTURE.get(id(session), []) if h not in state["seen_raw"]]
        if not fresh:
            return
        bs, cdp = await _cdp(agent)
        if cdp is None:
            _dbg("on_step_end: no cdp session")  # infra miss - do NOT mark seen, retry next step
            return
        wd = getattr(bs, "_downloads_watchdog", None)
        if wd is None or not hasattr(wd, "download_file_from_url"):
            _dbg("on_step_end: no downloads watchdog")
            return
        target_id = getattr(bs, "agent_focus_target_id", None) or getattr(cdp, "target_id", None)
        if not target_id:
            _dbg("on_step_end: no target_id")
            return
        # Resolve relative hrefs against the current page URL, falling back to the portal start_url
        # so a failed location.href read can't strand a root-relative link unresolved.
        try:
            r = await cdp.cdp_client.send.Runtime.evaluate(
                params={"expression": "location.href", "returnByValue": True}, session_id=cdp.session_id)
            page_url = (((r or {}).get("result") or {}).get("value")) or ""
        except Exception:
            page_url = ""
        base = page_url or start_url or ""
        from urllib.parse import urljoin
        for href in fresh:
            state["seen_raw"].add(href)  # we have a live target now - count this as handled
            absu = urljoin(base, href) if base else href
            if absu in state["fetched"] or not _looks_like_download(absu):
                continue
            if not _host_allowed(absu, start_url, allowed_domains):
                _dbg("on_step_end: blocked off-portal url:", _redact(absu))
                continue
            state["fetched"].add(absu)
            try:
                p = await wd.download_file_from_url(url=absu, target_id=target_id)
                _dbg("on_step_end: download_file_from_url ->", p, "for", _redact(absu))
            except Exception as e:
                _dbg("on_step_end: download_file_from_url ERR:", repr(e), "for", _redact(absu))

    return _on_step_start, _on_step_end


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
    on_step_start, on_step_end = _make_download_capturer(headless, session, start_url, allowed_domains)
    agent = _build_agent(full_task, llm, session, sensitive_data or None)

    error = None
    final = None
    history = None
    try:
        try:
            coro = agent.run(max_steps=max_steps, on_step_start=on_step_start,
                             on_step_end=on_step_end)
        except TypeError:  # older/newer run() without these kwargs
            coro = agent.run()
        history = await asyncio.wait_for(coro, timeout=timeout)
        final = _extract_final(history)
        # NOTE (headless downloads): nothing extra to do here. When headless, on_step_end
        # has already re-pulled any clicked download via in-page fetch mid-run (it MUST happen
        # before agent.run() returns, since the Agent tears down CDP on `done`). The files are
        # already on disk; the snapshot-diff harvest below picks them up. See _make_download_capturer.
    except asyncio.TimeoutError:
        error = f"timed out after {timeout}s"
    except Exception as e:
        error = str(e)
    finally:
        _CLICK_CAPTURE.pop(id(session), None)
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

    # If the run succeeded (files landed), distill a DRAFT workflow from the agent's history so
    # the caller can offer "Save as workflow" pre-filled. Best-effort; never fail the fetch.
    draft_workflow = None
    if new_files and history is not None:
        try:
            import trace_converter
            draft_workflow = trace_converter.history_to_workflow(history, start_url=start_url, goal=task)
        except Exception:
            draft_workflow = None

    return {
        "status": "ok" if error is None else "error",
        "error": error,
        "elapsed_seconds": round(time.time() - started, 1),
        "files": new_files,
        "file_count": len(new_files),
        "final_result": final,
        "draft_workflow": draft_workflow,
    }
