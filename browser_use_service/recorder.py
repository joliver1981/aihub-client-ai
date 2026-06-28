"""
recorder.py - turn an operator's takeover (clicks + typing) into editable workflow steps.

While a person drives the live page (Phase B input), the recorder resolves the element under
each click via in-page `document.elementFromPoint(x,y)` (over CDP Runtime.evaluate) and builds a
stable anchor (css/text/role) using the SAME model as the deterministic runner. A click on a
field starts a `fill`; subsequent typing accumulates its value; a click elsewhere flushes it and
records a `click`. Sensitive fields (password inputs, or anything typed during an `awaiting_human`
pause such as a 2FA code) are recorded as a `human` pause-marker - never the literal value.

On save, the recorded steps merge with the run's base steps and adjacent `human` markers collapse,
producing a draft that lands in the builder (same `?load=` deep-link as "Save as workflow").

No import of workflow_runner/run_registry here (kept dependency-free to avoid an import cycle:
run_registry lazily constructs a Recorder).
"""
import asyncio
import json
import logging

log = logging.getLogger("browser_use_service")

_NODE_JS = """
(function(){
  var e = document.elementFromPoint(__X__, __Y__);
  if(!e) return null;
  var tag = (e.tagName||'').toLowerCase();
  var attrs = {};
  for (var i=0;i<e.attributes.length;i++){ attrs[e.attributes[i].name]=e.attributes[i].value; }
  var type = (attrs.type||'').toLowerCase();
  var isInput = tag==='input' || tag==='textarea' || e.isContentEditable===true;
  var isPassword = tag==='input' && type==='password';
  var role = tag==='a' ? 'link'
           : (tag==='button' || type==='submit' || type==='button' || e.getAttribute('role')==='button') ? 'button'
           : null;
  var anchor = {};
  if(attrs.id) anchor.css='[id="'+attrs.id+'"]';
  else if(attrs.name) anchor.css='[name="'+attrs.name+'"]';
  else if(attrs.placeholder) anchor.css='[placeholder="'+attrs.placeholder+'"]';
  var txt = (e.innerText||e.getAttribute('aria-label')||attrs.value||'').replace(/\\s+/g,' ').trim();
  if(txt && txt.length<=60) anchor.text=txt;
  if(role) anchor.role=role;
  return {anchor:anchor, isInput:isInput, isPassword:isPassword};
})()
"""


async def _eval(cdp, js):
    try:
        res = await asyncio.wait_for(
            cdp.cdp_client.send.Runtime.evaluate(
                params={"expression": js, "returnByValue": True, "awaitPromise": True},
                session_id=cdp.session_id,
            ),
            timeout=5,
        )
    except Exception:
        return None
    if not isinstance(res, dict) or res.get("exceptionDetails"):
        return None
    return (res.get("result") or {}).get("value")


def collapse(steps):
    """Drop adjacent duplicate `human` markers and adjacent identical clicks (operator double
    actions). Keeps the draft tidy after merging base + recorded steps."""
    out = []
    for s in (steps or []):
        if out and s.get("type") == "human" and out[-1].get("type") == "human":
            continue
        if out and s.get("type") == "click" and out[-1].get("type") == "click" \
                and s.get("anchor") == out[-1].get("anchor"):
            continue
        out.append(s)
    return out


class Recorder:
    def __init__(self, sensitive_context=False, reason="Enter the value for this field (e.g. a 2FA code)"):
        self.sensitive_context = sensitive_context
        self.reason = reason
        self.steps = []
        self._pending = None

    async def on_click(self, cdp, x, y):
        info = await _eval(cdp, _NODE_JS.replace("__X__", str(int(x))).replace("__Y__", str(int(y))))
        if not isinstance(info, dict):
            return
        anchor = info.get("anchor") or {}
        if info.get("isInput"):
            self._flush()
            self._pending = {"anchor": anchor, "value": "", "password": bool(info.get("isPassword"))}
            return
        self._flush()
        if anchor:
            self.steps.append({"type": "click", "anchor": anchor})

    def on_text(self, text):
        if self._pending is not None:
            self._pending["value"] += str(text or "")

    def on_key(self, key):
        if key in ("Enter", "Tab"):
            self._flush()

    def _flush(self):
        p = self._pending
        self._pending = None
        if not p or not p["value"]:
            return
        if self.sensitive_context or p["password"]:
            self.steps.append({"type": "human", "reason": self.reason})
            return
        self.steps.append({"type": "fill", "anchor": p["anchor"], "value": p["value"]})

    def finalize(self):
        self._flush()
        return collapse(self.steps)
