"""
cobrowse.py - live-view (Phase A) screencast plumbing for the "Take over" feature.

Streams the server-side headed/headless Chrome of an in-flight run to attached co-browse viewers
over CDP `Page.startScreencast`. Frames render off-screen even when headless, so this works under
an NSSM service with no desktop. Phase A is READ-ONLY: frames flow out; input forwarding is Phase B.

The cdp_use EventRegistry awaits async callbacks and keeps ONE handler per (method) per CDP client;
each run owns its own BrowserSession/CDP client, so per-run registration is naturally isolated.
"""
import asyncio
import logging

log = logging.getLogger("browser_use_service")

_FRAME_PARAMS = {
    "format": "jpeg", "quality": 55, "maxWidth": 1280, "maxHeight": 800,
    "everyNthFrame": 1,
}


async def start_screencast(run):
    """Begin streaming `run`'s page. Idempotent. Registers a frame handler that fans each frame
    out to run.viewers (and caches it), then ACKs so Chrome keeps sending."""
    if run.screencasting or run.session is None:
        return
    cdp = await run.session.get_or_create_cdp_session()
    run.cdp = cdp

    async def on_frame(event, session_id=None):
        data = event.get("data")
        meta = event.get("metadata")
        run.last_frame = {"data": data, "metadata": meta}
        for ws in list(run.viewers):
            try:
                await ws.send_json({"t": "frame", "data": data, "metadata": meta})
            except Exception:
                run.viewers.discard(ws)
        run._pending_ack_sid = event.get("sessionId")
        if not run._ack_running:
            run._ack_running = True
            asyncio.create_task(_ack_loop(run, cdp))

    try:
        cdp.cdp_client.register.Page.screencastFrame(on_frame)

        await asyncio.wait_for(cdp.cdp_client.send.Page.enable(session_id=cdp.session_id), timeout=8)
        await asyncio.wait_for(
            cdp.cdp_client.send.Page.startScreencast(params=_FRAME_PARAMS, session_id=cdp.session_id),
            timeout=8,
        )
        run.screencasting = True
        log.info("cobrowse: screencast started run=%s", run.run_id)
    except Exception as e:
        log.warning("cobrowse: startScreencast failed run=%s: %s", run.run_id, e)


async def _ack_loop(run, cdp):
    """Acks the latest screencast frame, paced to ~12 fps, OUTSIDE the message-handler task.
    Screencast is ack-gated (one frame outstanding), so pacing the ack paces Chrome's next frame
    and bounds the CDP load competing with operator input/recording."""
    try:
        while run.screencasting and run._pending_ack_sid is not None:
            sid = run._pending_ack_sid
            run._pending_ack_sid = None
            try:
                await asyncio.wait_for(
                    cdp.cdp_client.send.Page.screencastFrameAck(
                        params={"sessionId": sid}, session_id=cdp.session_id
                    ),
                    timeout=5,
                )
            except Exception:
                break
            await asyncio.sleep(0.08)
    finally:
        run._ack_running = False


async def stop_screencast(run):
    """Stop streaming (best-effort). Called when the last viewer leaves or the run ends."""
    if not run.screencasting:
        return
    run.screencasting = False
    try:
        if run.cdp:
            await asyncio.wait_for(
                run.cdp.cdp_client.send.Page.stopScreencast(session_id=run.cdp.session_id),
                timeout=3,
            )
        log.info("cobrowse: screencast stopped run=%s", run.run_id)
    except Exception:
        pass


_CDP_MOUSE = {
    "down": "mousePressed", "up": "mouseReleased", "move": "mouseMoved",
    "wheel": "mouseWheel",
}
_BUTTONS = {"left", "right", "middle", "back", "forward", "none"}

_SPECIAL_KEYS = {
    "Enter": (13, "Enter"), "Backspace": (8, "Backspace"), "Tab": (9, "Tab"),
    "Delete": (46, "Delete"), "Escape": (27, "Escape"), "Home": (36, "Home"), "End": (35, "End"),
    "ArrowLeft": (37, "ArrowLeft"), "ArrowUp": (38, "ArrowUp"),
    "ArrowRight": (39, "ArrowRight"), "ArrowDown": (40, "ArrowDown"),
}


async def dispatch_input(run, msg):
    """Forward one operator input message to the live page over CDP. Coordinates arrive already
    mapped to page CSS pixels by the client. Each CDP call is bounded so a stall can't hang the
    WS receive loop. Mouse: down/up/move/wheel. Keyboard: printable -> insertText; special keys
    (Enter/Tab/Backspace/arrows) -> dispatchKeyEvent."""
    cdp = getattr(run, "cdp", None)
    if cdp is None:
        return
    t = msg.get("t")

    rec = getattr(run, "recorder", None)
    if rec is not None:
        try:
            if t == "mouse" and msg.get("kind") == "down":
                await rec.on_click(cdp, float(msg.get("x", 0)), float(msg.get("y", 0)))
            elif t == "text":
                rec.on_text(msg.get("text", ""))
            elif t == "key" and msg.get("kind") == "down":
                rec.on_key(msg.get("key", ""))
        except Exception:
            pass

    try:
        if t == "mouse":
            kind = _CDP_MOUSE.get(msg.get("kind"))
            if not kind:
                return
            params = {
                "type": kind, "x": float(msg.get("x", 0)), "y": float(msg.get("y", 0)),
                "modifiers": int(msg.get("modifiers", 0)),
            }
            if kind in ("mousePressed", "mouseReleased"):
                b = msg.get("button", "left")
                params["button"] = b if b in _BUTTONS else "left"
                params["clickCount"] = int(msg.get("clickCount", 1))
            elif kind == "mouseMoved":
                params["button"] = "none"
            elif kind == "mouseWheel":
                params["button"] = "none"
                params["deltaX"] = float(msg.get("dx", 0))
                params["deltaY"] = float(msg.get("dy", 0))
            await asyncio.wait_for(
                cdp.cdp_client.send.Input.dispatchMouseEvent(params=params, session_id=cdp.session_id),
                timeout=5,
            )
        elif t == "text":
            text = str(msg.get("text", ""))
            if text:
                await asyncio.wait_for(
                    cdp.cdp_client.send.Input.insertText(params={"text": text}, session_id=cdp.session_id),
                    timeout=5,
                )
        elif t == "key":
            key = msg.get("key", "")
            kind = "keyDown" if msg.get("kind") == "down" else "keyUp"
            vk, code = _SPECIAL_KEYS.get(key, (0, key))
            params = {
                "type": kind, "key": key, "code": code,
                "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk,
                "modifiers": int(msg.get("modifiers", 0)),
            }
            if key == "Enter":
                params["text"] = "\r"
            await asyncio.wait_for(
                cdp.cdp_client.send.Input.dispatchKeyEvent(params=params, session_id=cdp.session_id),
                timeout=5,
            )
    except Exception as e:
        log.debug("cobrowse: dispatch_input err run=%s: %s", getattr(run, "run_id", "?"), e)


async def broadcast_status(run):
    """Push the current run status to every attached viewer so the co-browse banner + input gate
    react to awaiting_human / taken_over / done transitions (otherwise the page only learns status
    on attach). Best-effort."""
    if run is None:
        return
    msg = {"t": "status"}
    try:
        msg.update(run.to_dict())
    except Exception:
        pass
    for ws in list(run.viewers):
        try:
            await ws.send_json(msg)
        except Exception:
            run.viewers.discard(ws)


async def add_viewer(run, ws):
    """Attach a co-browse WebSocket; start streaming on the first viewer. Sends the cached last
    frame immediately so the operator sees the page without waiting for the next repaint."""
    run.viewers.add(ws)
    if not run.screencasting:
        await start_screencast(run)
        return
    if run.last_frame:
        try:
            await ws.send_json({"t": "frame", **run.last_frame})
        except Exception:
            run.viewers.discard(ws)


async def remove_viewer(run, ws):
    run.viewers.discard(ws)
    if not run.viewers:
        await stop_screencast(run)
