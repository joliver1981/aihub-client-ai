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
import logging
import os
import time
import uuid

import cobrowse
import run_registry

log = logging.getLogger("browser_use_service")


# How long an auto-run will wait, blocked, for a human to take over (2FA/CAPTCHA) before giving
# up. Both the takeover tool and the on_step_start resume-wait share this ceiling.
_AUTO_HUMAN_MAX = int(os.getenv("PORTAL_TAKEOVER_MAX_SECONDS", "1800"))
_AUTO_TAKEOVER_MAX = int(os.getenv("PORTAL_TAKEOVER_MAX_SECONDS", "1800"))

# A mini-LLM backstop that hands off to a human when a page looks like a blocking 2FA/CAPTCHA
# gate the agent can't clear itself. On by default; gated so it can be turned off.
_TAKEOVER_BACKSTOP = os.getenv("BROWSER_USE_2FA_BACKSTOP", "true").lower() == "true"
_MINI_MODEL = os.getenv("BROWSER_USE_MINI_MODEL", "claude-haiku-4-5-20251001")


# Newer Claude models reject the `temperature` sampling param outright (HTTP 400). Gate it.
# Local copy of config.anthropic_sampling_kwargs — this service runs in its own env.
_ANTHROPIC_NO_SAMPLING_MARKERS = ("opus-4-7", "opus-4-8", "sonnet-5", "fable-5", "mythos-5", "mythos-preview")


def _anthropic_sampling_kwargs(model, temperature=None):
    """{'temperature': t} when the Claude model accepts it, else {} (newer models reject it)."""
    if temperature is None:
        return {}
    m = (model or "").lower()
    supported = not any(marker in m for marker in _ANTHROPIC_NO_SAMPLING_MARKERS)
    if supported:
        return {"temperature": temperature}
    return {}


_GATE_SYSTEM = (
    "You are a strict classifier for a web-automation agent. Decide if the CURRENT page is BLOCKING the agent because it requires a secret value the agent does NOT have and CANNOT obtain from the page itself.\nReply with ONE word: BLOCKED or OK.\nBLOCKED = the page demands a verification / one-time / 2FA / MFA code, a CAPTCHA, or a security challenge, AND the required value is NOT visible anywhere on the page (it would come from a phone, email, or authenticator the agent cannot access).\nOK = anything else, INCLUDING: a normal content page; a username/password login form; OR a verification page where the code IS shown somewhere on the page (the agent can read it and proceed itself)."
)


def _classify_blocking_gate(url, page_text):
    """Mini-LLM (Haiku) judgment: is THIS page blocking the agent for a value it can't get (so a
    human is needed)? Returns True/False. NEVER raises - any failure returns False (don't hand off,
    = today's behavior). Mirrors agent_knowledge_integration._haiku_call_with_fallback. Cheap:
    one ~4-token Haiku call. Distinguishes a visible/self-solvable code (OK) from a hidden one
    (BLOCKED), so it handles 'code given', 'no code', and 'code on screen' all in one place."""
    try:
        import browser_use_config as _cfg
        _cfg.ensure_llm_api_key(_MINI_MODEL)
        from anthropic import Anthropic
        client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        content = f"URL: {url}\n\nVISIBLE PAGE TEXT:\n{(page_text or '')[:2500]}"
        msg = client.messages.create(
            model=_MINI_MODEL, max_tokens=4,
            **_anthropic_sampling_kwargs(_MINI_MODEL, 0.0),
            system=_GATE_SYSTEM,
            messages=[{"role": "user", "content": content}])
        verdict = (getattr(msg, "content", None) and msg.content[0].text or "").strip().upper()
        return "BLOCKED" in verdict and "OK" not in verdict
    except Exception as e:
        log.debug("gate classifier unavailable: %s", e)
        return False


# In-page JS that finds the verification-code field(s), types the code, and submits the form -
# handles split one-char OTP boxes, autocomplete=one-time-code, name/id heuristics, and shadow DOM.
# We do this over CDP rather than through browser-use's <secret> input because an on-screen code
# isn't a sensitive_data placeholder (and split boxes need per-char typing browser-use can't do).
_ENTER_CODE_JS = '(function(){var CODE=String("__CODE__").replace(/\\D/g,"");if(!CODE){return JSON.stringify({found:false,reason:"no code provided"});}\nfunction collect(root,out){if(!root)return;var nl;try{nl=root.querySelectorAll("input,textarea");}catch(e){nl=[];}\nfor(var i=0;i<nl.length;i++)out.push(nl[i]);\nvar all;try{all=root.querySelectorAll("*");}catch(e){all=[];}\nfor(var j=0;j<all.length;j++){var el=all[j];if(el&&el.shadowRoot){collect(el.shadowRoot,out);}}}\nvar inputs=[];collect(document,inputs);\nfunction visible(el){if(!el)return false;if(el.disabled||el.readOnly)return false;if(el.type==="hidden")return false;\nvar s;try{s=getComputedStyle(el);}catch(e){s=null;}\nif(s&&(s.display==="none"||s.visibility==="hidden"))return false;\nvar r=el.getBoundingClientRect();return (r.width>0&&r.height>0);}\nfunction attr(el,n){return ((el.getAttribute&&el.getAttribute(n))||"").toLowerCase();}\nfunction isTexty(el){var t=(el.type||"text").toLowerCase();return t==="text"||t==="tel"||t==="number"||t==="";}\nvar nativeSetter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,"value").set;\nvar nativeTextareaSetter=(window.HTMLTextAreaElement&&Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,"value").set)||nativeSetter;\nfunction setVal(el,val){var setter=(el.tagName==="TEXTAREA")?nativeTextareaSetter:nativeSetter;try{setter.call(el,val);}catch(e){el.value=val;}\nel.dispatchEvent(new Event("input",{bubbles:true}));\nel.dispatchEvent(new Event("change",{bubbles:true}));\ntry{el.dispatchEvent(new KeyboardEvent("keyup",{bubbles:true}));}catch(e){}}\nfunction focusEl(el){try{el.focus();}catch(e){}}\nfunction submit(node,form){\ntry{if(form&&typeof form.requestSubmit==="function"){form.requestSubmit();return true;}}catch(e){}\ntry{var btn=(form||document).querySelector("button[type=submit],input[type=submit],#verify,button#verify,[data-testid*=verify],[data-testid*=submit]");\nif(!btn&&form){var bs=form.querySelectorAll("button");btn=bs.length?bs[bs.length-1]:null;}\nif(btn){btn.click();return true;}}catch(e){}\ntry{if(form&&typeof form.submit==="function"){form.submit();return true;}}catch(e){}\nreturn false;}\ntry{\nvar visTexty=inputs.filter(function(el){return visible(el)&&isTexty(el);});\nvar boxes=visTexty.filter(function(el){var ml=parseInt(el.getAttribute("maxlength"),10);return (ml===1)||(attr(el,"autocomplete")==="one-time-code"&&el.value!==undefined&&parseInt(el.getAttribute("maxlength"),10)===1);});\nif(boxes.length>=CODE.length&&CODE.length>1&&boxes.length<=12){\nboxes.sort(function(a,b){var ra=a.getBoundingClientRect(),rb=b.getBoundingClientRect();return (ra.top-rb.top)||(ra.left-rb.left);});\nfor(var k=0;k<CODE.length;k++){focusEl(boxes[k]);setVal(boxes[k],CODE[k]);}\nfocusEl(boxes[Math.min(CODE.length,boxes.length)-1]);\nvar f0=boxes[0].form;var submitted0=submit(boxes[0],f0);\nreturn JSON.stringify({found:true,mode:"split",boxes:boxes.length,tag:"input",submitted:submitted0});}\nfunction pick(){\nvar a=inputs.filter(function(el){return visible(el)&&attr(el,"autocomplete")==="one-time-code";});\nif(a.length)return a[0];\nvar rx=/(^|[^a-z])(otp|one-?time|2fa|mfa|verification|verify|code|pin|token|passcode|auth-?code|sms-?code)([^a-z]|$)/;\nvar b=inputs.filter(function(el){if(!visible(el)||!isTexty(el))return false;\nvar hay=[attr(el,"name"),attr(el,"id"),attr(el,"aria-label"),attr(el,"placeholder"),attr(el,"autocomplete"),attr(el,"data-testid")].join(" ");\nreturn rx.test(hay);});\nif(b.length)return b[0];\nvar c=visTexty.filter(function(el){var im=attr(el,"inputmode");var pat=attr(el,"pattern");var ml=parseInt(el.getAttribute("maxlength"),10);\nreturn im==="numeric"||/\\[0-9\\]|\\\\d/.test(pat)||(ml>=3&&ml<=10);});\nif(c.length>=1)return c[0];\nif(visTexty.length===1)return visTexty[0];\nreturn null;}\nvar el=pick();\nif(!el){return JSON.stringify({found:false,reason:"no candidate code input",inputs:inputs.length});}\nfocusEl(el);setVal(el,CODE);\nvar form=el.form||(el.closest&&el.closest("form"));\nvar submitted=submit(el,form);\nreturn JSON.stringify({found:true,mode:"single",tag:(el.tagName||"").toLowerCase(),name:(el.getAttribute&&el.getAttribute("name"))||null,id:el.id||null,submitted:submitted});\n}catch(err){return JSON.stringify({found:false,error:String(err&&err.message||err)});}})()'


async def _do_submit_verification_code(code, session, creds, run):
    """Type a verification / 2FA / one-time code into the page and submit it over CDP, bypassing
    browser-use's input/<secret> layer (which CANNOT type an on-screen code — see _ENTER_CODE_JS).
    Module-level so it's unit-testable with a fake session. Returns an agent-facing status string;
    NEVER raises. Resolves a TOTP from creds when the agent supplies no literal code; refuses to
    guess (points at request_human_takeover) when no code is obtainable."""
    rid = getattr(run, "run_id", "?")
    digits = "".join(ch for ch in (code or "") if ch.isdigit())
    if not digits and (creds or {}).get("totp_secret"):
        try:
            import pyotp
            digits = pyotp.TOTP(creds["totp_secret"]).now()
        except Exception as e:
            log.info("AUTO run=%s submit_verification_code: TOTP compute failed: %s", rid, e)
    if not digits:
        return "No verification code is available. Do NOT guess. If a code is visible on the page, call submit_verification_code again with those exact digits; otherwise call request_human_takeover so a person can complete this step."

    try:
        cdp = await session.get_or_create_cdp_session(focus=True)
    except Exception as e:
        log.info("AUTO run=%s submit_verification_code: CDP unavailable: %s", rid, e)
        return "Could not reach the browser to enter the code (CDP unavailable). Call request_human_takeover so a person can complete this step."

    expr = _ENTER_CODE_JS.replace("__CODE__", digits)
    try:
        r = await cdp.cdp_client.send.Runtime.evaluate(
            params={"expression": expr, "returnByValue": True}, session_id=cdp.session_id)
        raw = ((r.get("result") or {}).get("value"))
    except Exception as e:
        log.info("AUTO run=%s submit_verification_code: evaluate failed: %s", rid, e)
        return "Failed to enter the code in the page. Call request_human_takeover so a person can complete this step."

    import json as _json
    try:
        res = _json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        res = {}

    if res.get("found"):
        mode = res.get("mode", "single")
        where = res.get("id") or res.get("name") or ("%s boxes" % res.get("boxes") if mode == "split" else "the code field")
        log.info("AUTO run=%s submit_verification_code: entered %d digits into %s (mode=%s submitted=%s)",
                 rid, len(digits), where, mode, res.get("submitted"))
        if res.get("submitted"):
            return f"Entered the {len(digits)}-digit code into {where} ({mode}) and submitted the form. Wait for the page to advance, then continue the task. If the page says the code was wrong or asks again, call request_human_takeover - do NOT retry with a guess."
        return f"Entered the {len(digits)}-digit code into {where} ({mode}) but could not find a submit button. The code is in the field - find and click the verify/submit/continue button yourself; if there is none, call request_human_takeover."
    reason = res.get("reason") or res.get("error") or "no matching code field was found"
    log.info("AUTO run=%s submit_verification_code: NOT entered (%s)", rid, reason)
    return f"Could not enter the code: {reason}. The verification field was not found on this page. If you can see a code input, continue manually; otherwise call request_human_takeover so a person can complete this step."


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
    import browser_use_config as _cfg
    kwargs = dict(headless=headless, downloads_path=download_dir, accept_downloads=True)
    # Point browser-use at the bundled Chrome when the client ship configured one (the glob it
    # uses to auto-discover Chrome misses the packaged chrome-win64 - see browser_use_config).
    if getattr(_cfg, "CHROME_EXECUTABLE", None):
        kwargs["executable_path"] = _cfg.CHROME_EXECUTABLE
    if allowed_domains:
        kwargs["allowed_domains"] = allowed_domains
    return BrowserSession(**kwargs)


def _build_agent(task, llm, session, sensitive_data, tools=None, available_file_paths=None):
    from browser_use import Agent
    kwargs = dict(task=task, llm=llm, browser_session=session)
    if sensitive_data:
        kwargs["sensitive_data"] = sensitive_data
    if tools is not None:
        kwargs["tools"] = tools
    if available_file_paths:
        # browser-use exposes these paths to the agent's upload action.
        kwargs["available_file_paths"] = list(available_file_paths)
    try:
        return Agent(**kwargs)
    except TypeError:
        # Older browser-use without available_file_paths - drop it and retry.
        kwargs.pop("available_file_paths", None)
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


async def _run_with_active_timeout(coro, run, timeout):
    """Like asyncio.wait_for, but the timeout measures only ACTIVE work: time the run spends blocked
    waiting for a human to take over (tracked in run.active_seconds) is NOT counted, so a long
    2FA/CAPTCHA handoff never cancels the run mid-takeover. Raises asyncio.TimeoutError on a real
    (active-time) timeout."""
    task = asyncio.ensure_future(coro)
    while True:
        done, _ = await asyncio.wait({task}, timeout=2)
        if task in done:
            return task.result()
        if run.active_seconds() > timeout:
            task.cancel()
            try:
                await task
            except BaseException:
                pass
            raise asyncio.TimeoutError()


async def run_portal_fetch(task, start_url, creds, download_dir, llm_model,
                           headless=True, max_steps=50, timeout=300, allowed_domains=None,
                           run_id=None, user_id=None, available_file_paths=None):
    """Open `start_url`, log in (if creds given), pursue `task`, return a manifest of any
    files that landed in `download_dir`. `allowed_domains`, when set, confines the browser to
    those domains (navigation allowlist enforced outside the LLM).

    Registers the run so the co-browse live view / Run Monitor can attach: when the agent hits a
    step it can't do (2FA/CAPTCHA) it calls request_human_takeover, which pauses + pings a person
    to take over and resume."""
    os.makedirs(download_dir, exist_ok=True)
    before = _snapshot(download_dir)
    started = time.time()

    run = run_registry.RunState(run_id or uuid.uuid4().hex, user_id=user_id,
                                portal=start_url, kind="auto")
    run.goal = task
    run.start_url = start_url
    run_registry.register(run)
    log.info("AUTO run=%s START url=%s headless=%s task=%r", run.run_id, start_url, headless, (task or "")[:80])

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
            login_hint += " If asked for a 2FA/one-time code, call submit_verification_code with an empty code - the system supplies the authenticator code."
        except Exception:
            pass

    full_task = (
        f"Go to {start_url}.{login_hint} Then: {task}. IMPORTANT: downloaded files are saved AUTOMATICALLY by the system to the correct folder. Do NOT choose a save location, do NOT type a file path, and IGNORE any instruction to save to a specific local folder (e.g. C:\\tmp) - just trigger the download from the page itself. As soon as a file has downloaded once the task is complete - call done and stop; do NOT click the download again or repeat steps. Only report a file as downloaded if the browser actually downloaded it. VERIFICATION / 2FA / ONE-TIME CODES: for ANY verification, 2FA, MFA, one-time, OTP or security code, you MUST call submit_verification_code. If the code is visible on the page (e.g. shown for testing, or in a banner), pass those exact digits as `code`. If you have an authenticator/TOTP set up, call submit_verification_code with an empty `code` and the system supplies it. Do NOT type the code into the field yourself, do NOT click into the boxes, and do NOT wrap the code in <secret> tags - submit_verification_code does the typing and submits the form. If NO code is available to you (it would come from a phone/email/authenticator you cannot access) and there is no TOTP, call request_human_takeover and wait. For a CAPTCHA or any other login challenge you don't have the answer for, also call request_human_takeover. Never keep retrying or guessing codes."
    )

    # If the caller staged upload files, expose them to the agent and tell it they're available.
    if available_file_paths:
        _names = ", ".join(os.path.basename(str(p)) for p in available_file_paths)
        full_task += f" FILE UPLOAD: the file(s) [{_names}] are available to you. When the task needs a file uploaded, open the page's file-input / upload control and use your upload action with the provided file - do NOT type a path into a text box, and do NOT refuse for lack of the file."

    llm = _build_llm(llm_model)
    session = _build_session(download_dir, headless, allowed_domains)
    run.session = session
    cap_on_step_start, cap_on_step_end = _make_download_capturer(headless, session, start_url, allowed_domains)

    from browser_use import Tools
    tools = Tools()
    _took_over = {"v": False}

    async def _trigger_takeover(reason):
        """Pause the run, ping a human (Live runs + email), wait for handback. Returns True if a
        human resumed it, False on timeout. Shared by the tool AND the 2FA-page backstop; a no-op
        re-entry if the run is already awaiting a human."""
        _took_over["v"] = True
        if run.status == run_registry.AWAITING_HUMAN:
            return await run_registry.await_release(run, timeout=_AUTO_HUMAN_MAX)
        log.info("AUTO-TAKEOVER requested run=%s reason=%s -> awaiting a human (Live runs / email)",
                 run.run_id, reason)
        run_registry.request_human(run, reason)
        try:
            await cobrowse.broadcast_status(run)
        except Exception:
            pass
        try:
            import notifications
            notifications.notify_takeover(run)
        except Exception:
            pass
        ok = await run_registry.await_release(run, timeout=_AUTO_HUMAN_MAX)
        log.info("AUTO-TAKEOVER %s run=%s", "resumed by operator" if ok else "TIMED OUT (nobody took over)", run.run_id)
        return ok

    @tools.action("Call ONLY when you cannot complete a step yourself: a two-step verification / one-time passcode, a CAPTCHA, or a login challenge you lack the answer for. A human operator takes over the browser, completes that step, and hands back; then you continue the task.")
    async def request_human_takeover(reason: str = "A 2-step verification or login challenge needs a human.") -> str:
        ok = await _trigger_takeover(reason)
        if ok:
            return "A human completed the step and handed back. The page is now past it. Continue the task (e.g. download the requested file); call done once the file downloaded."
        return "No human took over in time. Stop now and call done; tell the user the step needs a human."

    @tools.action("Submit a verification / 2FA / MFA / one-time / OTP code into the page. ALWAYS use this for ANY verification or one-time code instead of typing into the field or wrapping it in secret tags. If the code is VISIBLE on the page (e.g. shown for testing or in a banner), pass those exact digits as `code`. If an authenticator/TOTP is configured, call with `code` empty and the system fills it in. If NO code is available to you, leave `code` empty - the tool will tell you to hand off rather than guess. The tool does the typing AND submits the form.")
    async def submit_verification_code(code: str = "") -> str:
        return await _do_submit_verification_code(code, session, creds, run)

    async def on_step_start(agent):
        if run.paused.is_set():
            run_registry.TAKEN_OVER
            run.status = run_registry.TAKEN_OVER
            try:
                await cobrowse.broadcast_status(run)
            except Exception:
                pass
            await run_registry.await_release(run, timeout=_AUTO_TAKEOVER_MAX)
        await cap_on_step_start(agent)

    _has_totp = bool(creds.get("totp_secret"))
    _PAGE_JS = "(function(){try{return {u:location.href, t:(document.body?document.body.innerText:'')};}catch(e){return {u:'',t:''};}})()"

    async def on_step_end(agent):
        try:
            await cap_on_step_end(agent)
            # 2FA backstop: only when we have NO TOTP to auto-fill (else submit_verification_code
            # handles it) and we're not already waiting on a human.
            if _has_totp or not _TAKEOVER_BACKSTOP or run.status == run_registry.AWAITING_HUMAN:
                return
            bs = getattr(agent, "browser_session", None)
            cdp = await bs.get_or_create_cdp_session(focus=False) if bs is not None else None
            if cdp is None:
                return
            r = await cdp.cdp_client.send.Runtime.evaluate(
                params={"expression": _PAGE_JS, "returnByValue": True}, session_id=cdp.session_id)
            v = (r.get("result") or {}).get("value") or {}
            if await asyncio.to_thread(_classify_blocking_gate, v.get("u", ""), v.get("t", "")):
                log.info("AUTO run=%s mini-LLM judged page a blocking gate -> handing off to a human", run.run_id)
                await _trigger_takeover("This page needs a verification code/credential the automation can't obtain.")
                return
            return
        except Exception as _e:
            _dbg("gate check err:", repr(_e))
            return

    agent = _build_agent(full_task, llm, session, sensitive_data or None, tools=tools,
                         available_file_paths=available_file_paths)

    error = None
    final = None
    history = None
    try:
        try:
            coro = agent.run(max_steps=max_steps, on_step_start=on_step_start,
                             on_step_end=on_step_end)
        except TypeError:  # older/newer run() without these kwargs
            coro = agent.run()
        history = await _run_with_active_timeout(coro, run, timeout)
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
        run.status = run_registry.ERROR if error else run_registry.DONE
        try:
            await cobrowse.broadcast_status(run)
        except Exception:
            pass
        try:
            await cobrowse.stop_screencast(run)
        except Exception:
            pass
        run.viewers.clear()
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

    # If the run succeeded (files landed, or this was an upload task), distill a DRAFT workflow
    # from the agent's history so the caller can offer "Save as workflow" pre-filled. Best-effort.
    draft_workflow = None
    if history is not None and (new_files or available_file_paths):
        try:
            import trace_converter
            draft_workflow = trace_converter.history_to_workflow(
                history, start_url=start_url, goal=task, human_takeover=_took_over["v"],
                upload_files=available_file_paths)
        except Exception:
            draft_workflow = None

    def _safe(fn, default=None):
        try:
            return fn()
        except Exception:
            return default

    # Compact trace log so an auto run can be debugged from the service log alone.
    _h = history if history is not None else getattr(agent, "history", None)
    if _h is not None:
        try:
            actions = _safe(_h.action_names, []) or []
            urls = _safe(getattr(_h, "urls", lambda: []), []) or []
            errs = [str(e)[:200] for e in (_safe(_h.errors, []) or []) if e]
            log.info("AUTO run=%s TRACE steps=%d actions=%s", run.run_id, len(actions), actions[:40])
            log.info("AUTO run=%s TRACE pages=%s", run.run_id, [u for u in urls if u][-12:])
            if errs:
                log.info("AUTO run=%s TRACE errors=%s", run.run_id, errs[:6])
            log.info("AUTO run=%s TRACE final=%r", run.run_id, str(final)[:300] if final else None)
        except Exception as _te:
            log.info("AUTO run=%s TRACE unavailable: %s", run.run_id, _te)

    log.info("AUTO run=%s DONE status=%s files=%d error=%s elapsed=%ss",
             run.run_id, "ok" if error is None else "error", len(new_files), error,
             round(time.time() - started, 1))

    manifest = {
        "status": "ok" if error is None else "error",
        "error": error,
        "elapsed_seconds": round(time.time() - started, 1),
        "files": new_files,
        "file_count": len(new_files),
        "final_result": final,
        "draft_workflow": draft_workflow,
        # An upload task is "successful" without producing a download; flag it so the caller
        # doesn't treat an empty file list as a failure.
        "is_upload": bool(available_file_paths),
        "expects_download": not bool(available_file_paths),
    }
    try:
        run_registry.store_result(run.run_id, manifest)
    finally:
        run_registry.unregister(run.run_id)
    return manifest
