"""
The Agent — image generation (pass 4 of the CC-gap plan, 2026-09-02).

generate_image calls OpenAI's images endpoint with raw httpx (the `openai`
package is not in the aihub-agent env — same posture as the web-search and
relay calls), saves the PNG under the platform's data area and hands it back
as the chat's inline image + download link (pass 2 rendering).

Key resolution mirrors Command Center's api_keys_config without importing it
(it pulls in Flask): BYOK (data/byok_config.json + the USER_OPENAI_API_KEY
local secret) beats OPENAI_API_KEY in the environment beats the decrypted
OPENAI_API_KEY_ENCRYPTED. Model and parameter shapes follow CC's
image_params: the same CC_IMAGE_MODEL flag drives both agents, and
CC_IMAGE_GENERATION_ENABLED turns the capability off platform-wide.
"""

import base64
import json
import os
import time
import uuid
from typing import Any, Optional

import httpx

from claude_agent_sdk import tool

from agent_config import APP_ROOT, logger
from platform_tools import CURRENT_USER, _text

OPENAI_IMAGES_URL = os.getenv("AGENT_OPENAI_IMAGES_URL", "https://api.openai.com/v1/images/generations")
DEFAULT_MODEL = os.getenv("CC_IMAGE_MODEL", "gpt-image-2")

_SIZES = {
    "dall-e-3": {"square": "1024x1024", "portrait": "1024x1792", "landscape": "1792x1024"},
    "dall-e-2": {"square": "1024x1024", "portrait": "1024x1024", "landscape": "1024x1024"},
    "gpt-image": {"square": "1024x1024", "portrait": "1024x1536", "landscape": "1536x1024"},
}
_VALID = {
    "dall-e-3": {"1024x1024", "1024x1792", "1792x1024"},
    "dall-e-2": {"256x256", "512x512", "1024x1024"},
    "gpt-image": {"1024x1024", "1024x1536", "1536x1024", "auto"},
}


def enabled() -> bool:
    return (os.getenv("CC_IMAGE_GENERATION_ENABLED", "true").lower() == "true"
            and os.getenv("AGENT_IMAGE_TOOLS", "true").lower() == "true")


def model_family(model: str) -> str:
    m = (model or "").strip().lower()
    if m.startswith("gpt-image"):
        return "gpt-image"
    if m == "dall-e-2":
        return "dall-e-2"
    return "dall-e-3"


def normalize_size(model: str, size: str) -> str:
    fam = model_family(model)
    s = (size or "").strip().lower()
    if s in _VALID[fam]:
        return s
    try:
        w, h = (int(x) for x in s.split("x"))
        orient = "portrait" if h > w else "landscape" if w > h else "square"
    except Exception:
        orient = {"portrait": "portrait", "landscape": "landscape", "wide": "landscape",
                  "tall": "portrait"}.get(s, "square")
    return _SIZES[fam][orient]


def build_request(model: str, prompt: str, size: str) -> dict:
    """The JSON body for /v1/images/generations — CC's image_params rules:
    dall-e models need response_format=b64_json; gpt-image models reject it
    (they always return b64_json)."""
    body = {"model": model, "prompt": prompt, "n": 1, "size": normalize_size(model, size)}
    if model_family(model) != "gpt-image":
        body["response_format"] = "b64_json"
    return body


def resolve_openai_key() -> tuple:
    """(key, source) — BYOK > env > encrypted; ('', 'none') when nothing is set."""
    try:
        path = os.path.join(os.getenv("AIHUB_DATA_DIR") or os.path.join(APP_ROOT, "data"),
                            "byok_config.json")
        with open(path, "r", encoding="utf-8") as fh:
            byok_on = bool(json.load(fh).get("byok_enabled", False))
    except Exception:
        byok_on = False
    if byok_on:
        try:
            from local_secrets import get_local_secret
            k = (get_local_secret("USER_OPENAI_API_KEY") or "").strip()
            if k:
                return k, "byok"
        except Exception as e:
            logger.warning(f"image_tools: BYOK store unreadable: {e}")
    k = (os.getenv("OPENAI_API_KEY") or "").strip()
    if k:
        return k, "env"
    enc = os.getenv("OPENAI_API_KEY_ENCRYPTED", "")
    if enc:
        try:
            from encrypt import decrypt_value, ENCRYPTION_KEY
            k = (decrypt_value(enc, ENCRYPTION_KEY) or "").strip()
            if k:
                return k, "encrypted"
        except Exception as e:
            logger.error(f"image_tools: could not decrypt OPENAI_API_KEY_ENCRYPTED: {e}")
    return "", "none"


def _images_dir() -> str:
    d = os.path.join(APP_ROOT, "temp", "agent_images")
    os.makedirs(d, exist_ok=True)
    return d


async def call_openai(key: str, body: dict, timeout: float = 180.0) -> tuple:
    """(status, json_or_text)."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=timeout)) as client:
        r = await client.post(OPENAI_IMAGES_URL, json=body,
                              headers={"Authorization": f"Bearer {key}",
                                       "Content-Type": "application/json"})
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"error": {"message": (r.text or "")[:300]}}


@tool(
    "generate_image",
    "Create a NEW picture from a text description (illustration, mock-up, "
    "concept art, a logo idea, a scene) with the platform's image model. Use "
    "when the user asks to generate, draw, create, or make an image/picture. "
    "Write a detailed prompt (subject, style, setting, lighting, colours). The "
    "image comes back as an inline image line plus a download link — include "
    "BOTH verbatim. This is for creating images, not for charts (aihub-chart) "
    "or maps (render_map). Costs real money per image; make one, not several, "
    "unless asked.",
    {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "What to draw, in detail"},
            "size": {"type": "string",
                     "description": "square (default), portrait, landscape, or WxH"},
            "name": {"type": "string", "description": "Optional file name (no extension)"},
        },
        "required": ["prompt"],
        "additionalProperties": False,
    },
)
async def generate_image(args: dict[str, Any]) -> dict[str, Any]:
    if not enabled():
        return _text("Image generation is turned off on this install "
                     "(CC_IMAGE_GENERATION_ENABLED / AGENT_IMAGE_TOOLS). An admin can "
                     "enable it.", is_error=True)
    prompt = " ".join(str(args.get("prompt") or "").split())
    if len(prompt) < 3:
        return _text("Describe what to draw.", is_error=True)
    key, source = resolve_openai_key()
    if not key:
        return _text("Image generation is not available — no OpenAI API key is configured "
                     "(Settings -> API Keys, or OPENAI_API_KEY).", is_error=True)
    model = DEFAULT_MODEL
    body = build_request(model, prompt[:4000], str(args.get("size") or "square"))
    started = time.time()
    try:
        status, data = await call_openai(key, body)
    except httpx.TimeoutException:
        return _text("The image service did not answer in time — nothing was generated. "
                     "Try again, or simplify the prompt.", is_error=True)
    except Exception as e:
        return _text(f"Could not reach the image service: {type(e).__name__}: {e}",
                     is_error=True)
    if status >= 400 or not isinstance(data, dict) or not data.get("data"):
        msg = ""
        if isinstance(data, dict):
            err = data.get("error") or {}
            msg = err.get("message") if isinstance(err, dict) else str(err)
        if status == 401:
            return _text(f"The OpenAI key ({source}) was rejected — image generation is "
                         "not available until an admin fixes it.", is_error=True)
        if status == 400 and msg:
            return _text(f"The image model ({model}) could not generate that image: "
                         f"{msg}", is_error=True)
        return _text(f"Image generation failed (HTTP {status}): {msg or data} — nothing "
                     "was generated.", is_error=True)
    item = data["data"][0] or {}
    b64 = item.get("b64_json")
    if not b64:
        return _text("The image service returned no image data — nothing was generated.",
                     is_error=True)
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return _text("The image service returned unreadable image data.", is_error=True)
    user = CURRENT_USER.get() or {}
    uid = int(user.get("user_id") or 0)
    from export_tools import safe_name
    fname = safe_name(args.get("name") or prompt[:40], "png", default="image")
    path = os.path.join(_images_dir(), f"{uuid.uuid4().hex}_{fname}")
    with open(path, "wb") as fh:
        fh.write(raw)
    from file_tools import stage_offer
    ok, link, _dst = stage_offer(uid, path, fname)
    try:
        os.remove(path)
    except OSError:
        pass
    if not ok:
        return _text(f"The image was generated but could not be delivered: {link}",
                     is_error=True)
    import rich_blocks
    imgs = rich_blocks.image_lines([link])
    revised = str(item.get("revised_prompt") or "").strip()
    logger.info(f"generate_image: {model} {body['size']} {len(raw)} bytes in "
                f"{time.time() - started:.1f}s (key {source})")
    return _text(f"Image generated ({model}, {body['size']}). Include BOTH lines below "
                 "VERBATIM in your reply — the first shows the picture inline, the "
                 "second is the download:\n" + "\n".join(imgs) + "\n" + link
                 + (f"\n(The model's revised prompt: {revised[:300]})" if revised else ""))


IMAGE_TOOLS = [generate_image]
