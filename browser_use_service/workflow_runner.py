"""
workflow_runner.py - execute a SAVED portal workflow as an ordered list of typed blocks
over ONE shared headed-Chrome session, weaving deterministic steps and scoped-LLM steps.

This is the "Workflow" mode that sits beside the existing pure-LLM "Auto-mode"
(portal_runner.run_portal_fetch). Both ride the same browser-use 0.12.x BrowserSession over
CDP; auto-mode is simply the degenerate one-block case (a single `agent` step doing the whole
task). Nothing here replaces portal_runner - it adds a deterministic+intelligent alternative.

The model (see also command_center/tools/portal_workflows.py for the persisted schema):

  workflow = {
    "name": "Acme - latest invoice",
    "start_url": "https://portal.acme-vendor.com/login",
    "goal": "Log in and download the most recent invoice.",   # used for the agent fallback
    "steps": [
      {"type": "goto",  "url": "https://portal.acme-vendor.com/login"},
      {"type": "login", "username_anchor": {...}, "password_anchor": {...},
                        "submit_anchor": {...}},               # secrets pulled server-side
      {"type": "click", "anchor": {"text": "Invoices", "role": "link"}},
      {"type": "wait",  "until": {"text": "Invoice", "role": null}, "timeout": 10},
      {"type": "agent", "prompt": "On this page, download the most recent invoice by date.",
                        "max_steps": 8},                       # the INTELLIGENT step
      {"type": "verify","downloaded": true}
    ]
  }

An `anchor` is a stable, replay-safe locator tried in priority order (NOT a brittle element
index): {"css","xpath","text","role","name"}. Deterministic blocks are executed in-page over
CDP Runtime.evaluate so they never touch the LLM. The `agent` block hands the SAME live session
to a scoped browser-use Agent for the judgment that can't be hard-coded ("the latest invoice").

Escalation ladder (worst case == today's auto-mode):
  deterministic step ok -> next
  deterministic step fails to resolve -> scoped-LLM HEAL of that one step (status "healed")
  heal fails / agent step fails -> FULL agent.run(goal) takes over the rest (status "fallback")

Credentials: deterministic login/fill type the real secret straight into the page over CDP
(server-side, never shown to any LLM). The `agent` block instead gets browser-use `sensitive_data`
placeholders so the model substitutes without ever seeing the value. Both paths resolve the
secret from the encrypted store by KEY NAME upstream; raw values live only in this process.
"""
import asyncio
import json
import time
import uuid

# Reuse auto-mode building blocks: same LLM factory and the same download-dir snapshot helper,
# so workflow runs and auto-mode behave identically where they overlap.
from portal_runner import _build_llm, _snapshot
import run_registry
import cobrowse


# ---------------------------------------------------------------------------
# In-page locator/action template. ANCHOR/VALUE/ACTION are spliced in per call.
# The returned el is the first matching element; the chosen ACTION runs on it.
# ---------------------------------------------------------------------------
_FINDER_TEMPLATE = """
(function(){
  var ANCHOR = __ANCHOR__;
  var VAL = __VALUE__;
  function visible(el){
    if(!el) return false;
    var r = el.getBoundingClientRect();
    var s = window.getComputedStyle(el);
    return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none';
  }
  function norm(s){ return (s||'').replace(/\\s+/g,' ').trim().toLowerCase(); }
  function txt(e){ return e.innerText || e.value || e.getAttribute('aria-label') || e.getAttribute('title') || ''; }
  function byText(text, role){
    var sel = role==='link' ? 'a,[role=link]'
            : role==='button' ? 'button,[role=button],input[type=submit],input[type=button],a'
            : 'a,button,[role=button],input[type=submit],input[type=button],label,span,div,td,th,li';
    var els = Array.prototype.slice.call(document.querySelectorAll(sel));
    var t = norm(text);
    var exact = els.filter(function(e){ return visible(e) && norm(txt(e))===t; });
    if(exact.length) return exact[0];
    var part = els.filter(function(e){ return visible(e) && norm(txt(e)).indexOf(t)>=0; });
    return part.length ? part[0] : null;
  }
  function byName(name){
    try{ var byId = document.getElementById(name); if(byId) return byId; }catch(e){}
    try{ return document.querySelector('[name="'+name+'"],[aria-label="'+name+'"],[placeholder="'+name+'"]'); }
    catch(e){ return null; }
  }
  function byXpath(xp){
    try{ var r = document.evaluate(xp, document, null, 9, null); return r.singleNodeValue; }
    catch(e){ return null; }
  }
  function setValue(el, value){
    var tag = el.tagName;
    var proto = tag==='TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    var desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if(desc && desc.set){ desc.set.call(el, value); } else { el.value = value; }
    el.dispatchEvent(new Event('input',  {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
  }
  var el = null;
  if(ANCHOR.css){   try{ el = document.querySelector(ANCHOR.css); }catch(e){} }
  if(!el && ANCHOR.xpath){ el = byXpath(ANCHOR.xpath); }
  if(!el && ANCHOR.text){  el = byText(ANCHOR.text, ANCHOR.role); }
  if(!el && ANCHOR.name){  el = byName(ANCHOR.name); }
  try { __ACTION__ } catch(e){ return 'ERR:' + (e && e.message ? e.message : e); }
})()
"""

# The three deterministic actions spliced into __ACTION__ above.
_ACTIONS = {
    'click':  "if(!el){return 'NOTFOUND';} try{el.scrollIntoView({block:'center'});}catch(e){} el.click(); return 'OK';",
    'fill':   "if(!el){return 'NOTFOUND';} try{el.focus();}catch(e){} setValue(el, VAL); return 'OK';",
    'exists': "return el ? 'OK' : 'NOTFOUND';",
}

# Generic fallbacks used by `login` when the workflow didn't author explicit anchors.
_DEFAULT_USERNAME = {'css': "input[type=email], input[name*='user' i], input[id*='user' i], input[name*='email' i], input[type=text]"}
_DEFAULT_PASSWORD = {'css': 'input[type=password]'}
_DEFAULT_SUBMITS = [
    {'css': 'button[type=submit], input[type=submit]'},
    {'text': 'log in', 'role': 'button'},
    {'text': 'sign in', 'role': 'button'},
    {'text': 'continue', 'role': 'button'},
    {'text': 'submit', 'role': 'button'},
]


def _action_js(anchor, action, value=''):
    a = json.dumps(anchor or {})
    v = json.dumps(value if value is not None else '')
    return (_FINDER_TEMPLATE
            .replace('__ANCHOR__', a)
            .replace('__VALUE__', v)
            .replace('__ACTION__', _ACTIONS[action]))


def _exc_text(res):
    exc = res.get('exceptionDetails') if isinstance(res, dict) else None
    if not exc:
        return None
    txt = exc.get('text') or 'exception'
    obj = exc.get('exception') or {}
    return f"{txt}: {obj.get('description') or obj.get('value') or ''}".strip()


async def _eval(session, js):
    """Run JS in the focused page over CDP and return its by-value result (or raise)."""
    cdp = await session.get_or_create_cdp_session()
    res = await cdp.cdp_client.send.Runtime.evaluate(
        params={'expression': js, 'returnByValue': True, 'awaitPromise': True},
        session_id=cdp.session_id,
    )
    err = _exc_text(res)
    if err:
        raise RuntimeError(err)
    return (res.get('result') or {}).get('value')


async def _wait_ready(session, timeout=12.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if await _eval(session, 'document.readyState') == 'complete':
                return True
        except Exception:
            pass
        await asyncio.sleep(0.25)
    return False


async def _do(session, anchor, action, value=''):
    """Perform one in-page action; returns 'OK' / 'NOTFOUND' / 'ERR:..'."""
    return await _eval(session, _action_js(anchor, action, value))


async def _click_any(session, anchors):
    """Try a list of anchors, return the first that clicks OK (else 'NOTFOUND')."""
    for a in anchors:
        if not a:
            continue
        if await _do(session, a, 'click') == 'OK':
            return 'OK'
    return 'NOTFOUND'


def _describe(anchor):
    a = anchor or {}
    return a.get('text') or a.get('name') or a.get('css') or a.get('xpath') or 'the target element'


async def _step_goto(session, step):
    url = step.get('url')
    if not url:
        return ('failed', 'goto: missing url')
    await session.navigate_to(url)
    await _wait_ready(session)
    return ('ok', url)


async def _step_click(session, step):
    anchor = step.get('anchor') or {}
    r = await _do(session, anchor, 'click')
    if r == 'OK':
        await _wait_ready(session, timeout=6.0)
        return ('ok', _describe(anchor))
    return ('failed', f"click {_describe(anchor)}: {r}")


async def _step_fill(session, step, creds):
    anchor = step.get('anchor') or {}
    value = step.get('value')
    if value is None and step.get('secret'):
        value = creds.get(step['secret'])
    if value is None:
        return ('failed', 'fill: no value/secret resolved')
    r = await _do(session, anchor, 'fill', value)
    return ('ok', _describe(anchor)) if r == 'OK' else ('failed', f"fill {_describe(anchor)}: {r}")


async def _step_wait(session, step):
    until = step.get('until')
    timeout = float(step.get('timeout', 10))
    if not until:
        await asyncio.sleep(min(timeout, 30))
        return ('ok', f"waited {min(timeout, 30)}s")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if await _do(session, until, 'exists') == 'OK':
            return ('ok', f"found {_describe(until)}")
        await asyncio.sleep(0.4)
    return ('failed', f"wait: {_describe(until)} never appeared")


async def _step_login(session, step, creds):
    if step.get('url'):
        await session.navigate_to(step['url'])
        await _wait_ready(session)
    if not (creds.get('username') and creds.get('password')):
        return ('failed', 'login: credentials not provided')
    u = await _do(session, step.get('username_anchor') or _DEFAULT_USERNAME, 'fill', creds['username'])
    if u != 'OK':
        return ('failed', f"login: username field {u}")
    p = await _do(session, step.get('password_anchor') or _DEFAULT_PASSWORD, 'fill', creds['password'])
    if p != 'OK':
        return ('failed', f"login: password field {p}")
    if creds.get('totp') and step.get('totp_anchor'):
        await _do(session, step['totp_anchor'], 'fill', creds['totp'])
    submits = ([step['submit_anchor']] if step.get('submit_anchor') else []) + _DEFAULT_SUBMITS
    s = await _click_any(session, submits)
    if s != 'OK':
        return ('failed', 'login: could not find a submit control')
    await _wait_ready(session)
    return ('ok', 'logged in')


async def _run_agent(session, llm, task, sensitive_data, max_steps, timeout):
    """Run a scoped browser-use Agent on the ALREADY-OPEN session (no re-login, no re-nav)."""
    from browser_use import Agent
    kwargs = dict(task=task, llm=llm, browser_session=session)
    if sensitive_data:
        kwargs['sensitive_data'] = sensitive_data
    agent = Agent(**kwargs)
    try:
        coro = agent.run(max_steps=max_steps)
    except TypeError:
        coro = agent.run()
    return await asyncio.wait_for(coro, timeout=timeout)


async def _step_agent(session, step, llm, sensitive_data, default_max_steps, default_timeout):
    prompt = step.get('prompt') or step.get('task')
    if not prompt:
        return ('failed', 'agent: missing prompt')
    task = (
        "On the CURRENT already-open page (you are already logged in - do NOT navigate away or log in again): "
        f"{prompt}"
        ". Downloaded files are saved automatically by the system - do NOT choose a save location. As soon as the goal is achieved, call done and stop."
    )
    try:
        await _run_agent(
            session, llm, task, sensitive_data,
            int(step.get('max_steps') or default_max_steps),
            int(step.get('timeout') or default_timeout),
        )
        return ('ok', 'agent step completed')
    except asyncio.TimeoutError:
        return ('failed', 'agent: timed out')
    except Exception as e:
        return ('failed', f"agent: {e}")


async def _settle_downloads(download_dir, before, timeout=15.0):
    """Bounded wait for the download set to CHANGE vs `before` and any in-flight partial files
    (.crdownload/.partial) to clear, then return the new/modified file paths. A click that
    triggers a download returns immediately while the browser writes the file asynchronously,
    so both `verify {downloaded}` and the final harvest must wait for the bytes to land."""
    import glob as _glob
    import os as _os
    deadline = time.time() + timeout
    after = _snapshot(download_dir)
    while time.time() < deadline:
        partials = (_glob.glob(_os.path.join(download_dir, '**', '*.crdownload'), recursive=True)
                    + _glob.glob(_os.path.join(download_dir, '**', '*.partial'), recursive=True))
        if after != before and not partials:
            break
        await asyncio.sleep(0.5)
        after = _snapshot(download_dir)
    return [_os.path.join(download_dir, rel)
            for rel, fp in after.items()
            if rel not in before or before[rel] != fp]


async def _step_verify(session, step, before, download_dir):
    if step.get('downloaded'):
        new = await _settle_downloads(download_dir, before, timeout=float(step.get('timeout', 15)))
        return ('ok', 'file downloaded') if new else ('failed', 'verify: no new file downloaded')
    if step.get('text') or step.get('anchor'):
        anchor = step.get('anchor') or {'text': step.get('text')}
        r = await _do(session, anchor, 'exists')
        return ('ok', f"present: {_describe(anchor)}") if r == 'OK' else ('failed', f"verify: missing {_describe(anchor)}")
    return ('ok', 'verify: nothing to check')


async def _heal_step(session, step, llm, sensitive_data, timeout):
    """Scoped-LLM repair of ONE failed deterministic step (status -> 'healed' on success)."""
    t = step.get('type')
    if t == 'login':
        task = "On the current page, log in using the provided credentials. Use the placeholders portal_username and portal_password for the username and password fields, then submit. Call done when the page is logged in."
        sd = sensitive_data
    elif t == 'click':
        task = f"On the current page, click {_describe(step.get('anchor'))}. Call done once clicked."
        sd = None
    elif t == 'fill':
        if step.get('secret'):
            ph = {'username': 'portal_username', 'password': 'portal_password', 'totp': 'portal_totp'}.get(step['secret'], '')
            task = f"On the current page, type the placeholder {ph} into the field for {_describe(step.get('anchor'))}. Call done when filled."
            sd = sensitive_data
        else:
            val = json.dumps(step.get('value') or '')
            task = f"On the current page, type {val} into the field for {_describe(step.get('anchor'))}. Call done when filled."
            sd = None
    elif t == 'goto':
        return ('failed', 'goto cannot be healed')
    else:
        return ('failed', f"{t} not healable")
    try:
        await _run_agent(session, llm, task, sd, max_steps=6, timeout=min(timeout, 120))
        return ('healed', f"healed {t} via scoped LLM")
    except Exception as e:
        return ('failed', f"heal failed: {e}")


import os as _os
_TAKEOVER_MAX = int(_os.getenv('PORTAL_TAKEOVER_MAX_SECONDS', '1800'))
_HUMAN_STEP_MAX = _TAKEOVER_MAX

# Verification-step take-over default window (longer than a generic human step: the operator
# has to read the email, open the link, and key in a code).
_VERIFY_TAKEOVER_SECONDS = int(_os.getenv('PORTAL_VERIFY_TAKEOVER_SECONDS', '900'))


async def _step_human(run, step, default_timeout=None):
    """Pause for a person (e.g. enter a 2FA code) and wait for an operator to take over and
    resume. The run registers as `awaiting_human` and pings the owner; the operator resumes via
    the co-browse view. Completes ONLY on a genuine status transition (release() -> RUNNING), so a
    stray event edge can't advance the workflow before the human finished. Times out -> failed.

    `default_timeout` (the workflow's per-run takeover window) is used when the step itself doesn't
    author a `timeout`; falls back to 300s. Always clamped to _HUMAN_STEP_MAX."""
    reason = step.get('reason') or 'A person needs to complete this step (e.g. a 2FA code).'
    if run is None:
        return ('failed', 'human step requires a run context')
    _dflt = float(default_timeout or 300)
    timeout = min(float(step.get('timeout', _dflt) or _dflt), _HUMAN_STEP_MAX)
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
    deadline = time.time() + timeout
    while run.status == run_registry.AWAITING_HUMAN:
        remaining = deadline - time.time()
        if remaining <= 0:
            return ('failed', 'human step timed out (nobody took over)')
        run.resume_evt.clear()
        await run_registry.await_release(run, timeout=remaining)
    return ('ok', 'resumed by operator')


_VERIFY_HUMAN_ATTEMPTS = 3

import logging as _logging
_log = _logging.getLogger('browser_use_service')


# Re-checked at ENTRY to each poll: are we STILL on a 2FA/verification gate? (verify-path URL
# OR a visible one-time-code field). Used to PROVE a take-over actually cleared 2FA.
_TWOFA_STILL_BLOCKING_JS = """(function(){
  var u=(location.href||'').toLowerCase();
  var urlGate=/(login-?2fa|verify|verification|otp|mfa|two-?factor|2-?step|challenge)/.test(u);
  function vis(el){if(!el)return false;var r=el.getBoundingClientRect();var s=getComputedStyle(el);
    return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';}
  var inputs=Array.prototype.slice.call(document.querySelectorAll('input,textarea'));
  var codeField=inputs.some(function(el){if(!vis(el))return false;
    var hay=[el.name,el.id,el.getAttribute('aria-label'),el.getAttribute('placeholder'),
             el.getAttribute('autocomplete'),el.getAttribute('data-testid')].join(' ').toLowerCase();
    return (el.getAttribute('autocomplete')||'').toLowerCase()==='one-time-code'
       || /otp|one-?time|2fa|mfa|verification|verify|passcode|auth-?code|sms-?code/.test(hay);});
  return JSON.stringify({url_gate:urlGate, code_field:codeField});
})()"""


async def _twofa_cleared(session, timeout=12.0):
    """Poll until the page is NO LONGER on a 2FA/verification gate (URL not a verify path AND no
    visible one-time-code field), or timeout. Returns True if cleared. Used to PROVE a take-over
    actually got past 2FA before the workflow trusts the next step."""
    deadline = time.time() + timeout
    while True:
        await _wait_ready(session, timeout=2.0)
        try:
            raw = await _eval(session, _TWOFA_STILL_BLOCKING_JS)
            st = json.loads(raw) if isinstance(raw, str) else (raw or {})
            if not (st.get('url_gate') or st.get('code_field')):
                return True
        except Exception:
            pass
        if time.time() >= deadline:
            return False
        await asyncio.sleep(0.5)


async def _step_verify_code(session, step, creds, run, oversight=True, takeover_timeout=None):
    """Enter a 2FA / one-time / verification code. UNATTENDED when a TOTP secret is configured
    (generate the live code AT ENTRY TIME with pyotp and type it via the robust CDP setter shared
    with auto-mode), so a scheduled replay passes 2FA on its own. With no TOTP it degrades to a
    `human` pause that EMAILS the owner a take-over link (so an attended OR scheduled replay still
    works — a person enters the code in the live view, exactly like chat). The code is generated
    here, not at request time, so it can't expire while earlier steps run."""
    _floor = int(takeover_timeout or 0) or _VERIFY_TAKEOVER_SECONDS
    hstep = {**step, 'timeout': max(int(step.get('timeout') or 0), _floor)}

    # ---- Path A: TOTP secret available -> generate + type the code ourselves (unattended). ----
    secret = (creds or {}).get('totp_secret')
    code = ''
    if secret:
        try:
            import pyotp
            code = pyotp.TOTP(secret).now()
        except Exception:
            code = ''
    if not code:
        code = (creds or {}).get('totp') or ''
    if code:
        try:
            from portal_runner import _ENTER_CODE_JS
            raw = await _eval(session, _ENTER_CODE_JS.replace('__CODE__', code))
            try:
                res = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except Exception:
                res = {}
            if (res.get('submitted') or res.get('found')) and await _twofa_cleared(session, timeout=12.0):
                return ('ok', f"2FA cleared automatically ({res.get('mode') or 'single'})")
        except Exception:
            code = ''

    # ---- Path B: no usable TOTP -> hand to a human (and let agent oversight finish if stuck). ----
    if not oversight:
        # First, plain human pause (the operator enters + submits the code).
        status, detail = await _step_human(run, hstep)
        if status != 'ok':
            return (status, detail)
        if await _twofa_cleared(session, timeout=12.0):
            return ('ok', '2FA completed by operator')
        _log.info('verify_code: handed back but still on the 2FA gate -> agent oversight')
        return ('needs_oversight', 'verification code entered during take-over but not submitted')

    for attempt in range(_VERIFY_HUMAN_ATTEMPTS):
        s = dict(hstep)
        if attempt > 0:
            s['reason'] = "The verification isn't finished yet — please ENTER the code AND click the verify/sign-in button so the page moves PAST the 2FA screen, then hand back."
        status, detail = await _step_human(run, s)
        if status != 'ok':
            return (status, detail)
        if await _twofa_cleared(session, timeout=12.0):
            return ('ok', '2FA completed by operator')
        _log.info('verify_code: handed back but still on the 2FA gate (attempt %d/%d)',
                  attempt + 1, _VERIFY_HUMAN_ATTEMPTS)
    return ('failed', '2FA/verification was not completed during take-over (page never left the gate)')


def _resolve_upload_files(step, inputs):
    """Resolve the absolute file path(s) an `upload` step should push. Order: explicit path(s)
    authored on the step, else a named input channel from the run `inputs` (default 'files')."""
    inputs = inputs or {}
    raw = step.get('path') or step.get('file') or step.get('files')
    if not raw:
        key = step.get('input') or 'files'
        raw = inputs.get(key)
        if not raw and key == 'files':
            raw = inputs.get('file')
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw if x]
    return []


def _upload_finder_js(anchor):
    """JS that EVALUATES TO the target <input type=file> element (so Runtime.evaluate hands back an
    objectId for DOM.setFileInputFiles). Matches by CSS, then xpath, then name; if the match isn't
    a file input it looks inside it; finally falls back to the page's only file input. File inputs
    are frequently hidden, so (unlike click/fill) visibility is NOT required here."""
    a = json.dumps(anchor or {})
    return """
(function(){
  var ANCHOR = __ANCHOR__;
  function isFile(el){ return !!el && el.tagName==='INPUT' && (el.type||'').toLowerCase()==='file'; }
  function byXpath(xp){ try{ var r=document.evaluate(xp, document, null, 9, null); return r.singleNodeValue; }catch(e){ return null; } }
  var el = null;
  if(ANCHOR.css){   try{ el = document.querySelector(ANCHOR.css); }catch(e){} }
  if(!el && ANCHOR.xpath){ el = byXpath(ANCHOR.xpath); }
  if(!el && ANCHOR.name){ try{ el = document.querySelector('input[name="'+ANCHOR.name+'"]') || document.getElementById(ANCHOR.name); }catch(e){} }
  if(el && !isFile(el)){ var fi = el.querySelector && el.querySelector('input[type=file]'); if(fi) el = fi; }
  if(!isFile(el)){
    var all = Array.prototype.slice.call(document.querySelectorAll('input[type=file]'));
    if(all.length===1){ el = all[0]; }
  }
  return isFile(el) ? el : null;
})()
""".replace('__ANCHOR__', a)


async def _step_upload(session, step, inputs):
    """Upload local file(s) into a portal <input type=file> via CDP DOM.setFileInputFiles — a page
    can't be made to set a file input's value through JS (browsers block it). The files come from
    the run `inputs` (a workflow's Portal node supplies them) or a literal step path, and must exist
    on THIS host (the service shares the download/scratch filesystem with the workflow executor)."""
    anchor = step.get('anchor') or {}
    files = _resolve_upload_files(step, inputs)
    if not files:
        return ('failed', "upload: no file(s) provided (the workflow's Portal node must pass a file)")
    missing = [f for f in files if not _os.path.isfile(f)]
    if missing:
        return ('failed', f"upload: file(s) not found on server: {missing[:3]}")
    try:
        cdp = await session.get_or_create_cdp_session()
        res = await cdp.cdp_client.send.Runtime.evaluate(
            params={'expression': _upload_finder_js(anchor), 'returnByValue': False},
            session_id=cdp.session_id,
        )
        result = res.get('result') or {}
        object_id = result.get('objectId')
        if not object_id or result.get('subtype') == 'null':
            return ('failed', f"upload: no file input found ({_describe(anchor)})")
        try:
            await cdp.cdp_client.send.DOM.enable(params={}, session_id=cdp.session_id)
        except Exception:
            pass
        await cdp.cdp_client.send.DOM.setFileInputFiles(
            params={'files': files, 'objectId': object_id},
            session_id=cdp.session_id,
        )
        return ('ok', f"uploaded {len(files)} file(s)")
    except Exception as e:
        return ('failed', f"upload: {e}")


async def run_workflow(workflow, creds, download_dir, llm_model,
                       headless=False, allowed_domains=None, timeout=600, max_steps=8,
                       agent_fallback=True, totp_secret=None, run_id=None, user_id=None,
                       inputs=None):
    """Execute `workflow["steps"]` over one shared session; return a manifest of step results
    and any harvested downloads. `creds` = {username,password,totp?} resolved server-side.

    The run registers in run_registry so the co-browse live view / Run Monitor can attach (a
    `human` step or an operator takeover pauses it until resumed)."""
    import os
    started = time.time()

    import browser_use_config as _cfg
    if len(run_registry.RUNS) >= _cfg.MAX_SESSIONS:
        return dict(
            status='error',
            error=f"too many concurrent portal runs (cap {_cfg.MAX_SESSIONS}); try again shortly",
            elapsed_seconds=0.0, files=[], file_count=0, steps=[],
            final_result='rejected: concurrency cap',
        )

    os.makedirs(download_dir, exist_ok=True)
    before = _snapshot(download_dir)

    run = run_registry.RunState(
        run_id or uuid.uuid4().hex,
        user_id=user_id, portal=workflow.get('name'), kind='workflow',
    )
    run.base_steps = list(workflow.get('steps') or [])
    run.start_url = workflow.get('start_url')
    run.goal = workflow.get('goal') or workflow.get('task')
    run.portal_slug = workflow.get('portal_slug')
    run_registry.register(run)

    # browser-use sensitive_data: the agent block / heal substitutes placeholders, never seeing values.
    sensitive_data = {}
    if creds.get('username') and creds.get('password'):
        sensitive_data['portal_username'] = creds['username']
        sensitive_data['portal_password'] = creds['password']
    if creds.get('totp'):
        sensitive_data['portal_totp'] = creds['totp']

    steps = workflow.get('steps') or []
    goal = workflow.get('goal') or workflow.get('task')

    # agent oversight: when fallback is allowed AND the workflow didn't opt out, a stuck step can be
    # handed to a scoped agent that finishes the goal.
    oversight = bool(agent_fallback) and bool(workflow.get('agent_oversight', True))

    # Per-run take-over window for human/verify_code steps (clamped to _TAKEOVER_MAX).
    try:
        wf_takeover = int(workflow.get('takeover_timeout') or 0) or None
    except (TypeError, ValueError):
        wf_takeover = None
    takeover_bound = min(wf_takeover, _TAKEOVER_MAX) if wf_takeover else _TAKEOVER_MAX
    results = []
    error = None
    session = None

    try:
        llm = _build_llm(llm_model)
        from browser_use import BrowserSession
        sess_kwargs = dict(
            headless=headless, downloads_path=download_dir,
            accept_downloads=True, keep_alive=True,
        )
        if getattr(_cfg, 'CHROME_EXECUTABLE', None):
            sess_kwargs['executable_path'] = _cfg.CHROME_EXECUTABLE
        if allowed_domains:
            sess_kwargs['allowed_domains'] = allowed_domains
        session = BrowserSession(**sess_kwargs)
        await session.start()
        run.session = session

        # Open the start_url unless the first step is a goto/login that already carries a url.
        if workflow.get('start_url') and not (
            steps and steps[0].get('type') in ('goto', 'login') and steps[0].get('url')
        ):
            await session.navigate_to(workflow['start_url'])
            await _wait_ready(session)

        for i, step in enumerate(steps):
            t = step.get('type')
            s0 = time.time()

            # An operator can pause/take over at any step boundary; wait for handback.
            if run.paused.is_set():
                run.status = run_registry.TAKEN_OVER
                run.pause_index = i
                try:
                    await cobrowse.broadcast_status(run)
                except Exception:
                    pass
                if not await run_registry.await_release(run, timeout=takeover_bound):
                    error = 'takeover abandoned (no handback within limit)'
                    break

            if t in ('human', 'verify_code'):
                run.pause_index = i

            try:
                if t == 'human':
                    status, detail = await _step_human(run, step, default_timeout=wf_takeover)
                elif t == 'verify_code':
                    status, detail = await _step_verify_code(
                        session, step, creds, run, oversight,
                        takeover_timeout=wf_takeover,
                    )
                elif t == 'goto':
                    status, detail = await _step_goto(session, step)
                elif t == 'click':
                    status, detail = await _step_click(session, step)
                elif t == 'fill':
                    status, detail = await _step_fill(session, step, creds)
                elif t == 'wait':
                    status, detail = await _step_wait(session, step)
                elif t == 'login':
                    status, detail = await _step_login(session, step, creds)
                elif t == 'agent':
                    status, detail = await _step_agent(
                        session, step, llm, sensitive_data or None,
                        max_steps, min(timeout, 300),
                    )
                elif t == 'verify':
                    status, detail = await _step_verify(session, step, before, download_dir)
                elif t == 'upload':
                    status, detail = await _step_upload(session, step, inputs)
                else:
                    detail = f"unknown step type: {t}"
                    status = 'failed'
            except Exception as e:
                status, detail = 'failed', f"{t}: {e}"

            # Heal a failed deterministic step with a scoped LLM (click/fill/login only).
            if status == 'failed' and t in ('click', 'fill', 'login'):
                hstatus, hdetail = await _heal_step(
                    session, step, llm, sensitive_data or None, timeout)
                if hstatus == 'healed':
                    status, detail = 'healed', hdetail

            # human/verify_code/upload never escalate to a full-goal agent takeover.
            wants_agent = (status == 'needs_oversight') or (
                status == 'failed' and t not in ('human', 'verify_code', 'upload'))

            results.append(dict(
                index=i, type=t,
                status='ok' if status == 'needs_oversight' else status,
                detail=detail,
                elapsed_seconds=round(time.time() - s0, 1),
            ))

            if status == 'failed' and t in ('human', 'verify_code'):
                error = f"step {i} ({t}) failed: {detail}"
                break

            if not wants_agent:
                continue

            if oversight and goal:
                fb0 = time.time()
                try:
                    await _run_agent(
                        session, llm,
                        "You are already logged in. If a verification/2FA code is already entered on the page, submit it (click the verify/sign-in button). Then accomplish: "
                        f"{goal}"
                        ". Navigate as needed; downloaded files are saved automatically, so do not choose a save location. Call done when complete.",
                        sensitive_data or None, max_steps=max_steps, timeout=min(timeout, 300),
                    )
                    results.append(dict(
                        index=i + 1, type='oversight', status='ok',
                        detail='agent oversight took over and completed the task',
                        elapsed_seconds=round(time.time() - fb0, 1),
                    ))
                    error = None
                except Exception as e:
                    error = f"step {i} ({t}) stuck; agent oversight errored: {e}"
                    results.append(dict(
                        index=i + 1, type='oversight', status='failed',
                        detail=str(e), elapsed_seconds=round(time.time() - fb0, 1),
                    ))
                break
            else:
                why = ("needs agent oversight but it's turned off for this workflow"
                       if status == 'needs_oversight' else f"failed: {detail}")
                error = f"step {i} ({t}) {why}"
                break
    except asyncio.TimeoutError:
        error = f"workflow timed out after {timeout}s"
    except Exception as e:
        error = str(e)
    finally:
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
        await _close_session(session)
        run_registry.unregister(run.run_id)

    # Harvest any downloads that landed during the run.
    new_files = await _settle_downloads(download_dir, before, timeout=8.0)

    ok = error is None and all(r['status'] in ('ok', 'healed') for r in results)
    return dict(
        status='ok' if ok else ('partial' if new_files else 'error'),
        error=error,
        elapsed_seconds=round(time.time() - started, 1),
        files=new_files,
        file_count=len(new_files),
        steps=results,
        final_result=_summarize(results, new_files),
    )


def _summarize(results, files):
    done = sum(1 for r in results if r['status'] in ('ok', 'healed'))
    healed = sum(1 for r in results if r['status'] == 'healed')
    parts = [f"{done}/{len(results)} steps ok"]
    if healed:
        parts.append(f"{healed} healed")
    if any(r['type'] == 'fallback' for r in results):
        parts.append('agent fallback used')
    parts.append(f"{len(files)} file(s) downloaded")
    return '; '.join(parts)


async def _close_session(session):
    for name in ('kill', 'stop', 'close'):
        fn = getattr(session, name, None)
        if not fn:
            continue
        try:
            res = fn()
            if asyncio.iscoroutine(res):
                await res
            return
        except Exception:
            pass
