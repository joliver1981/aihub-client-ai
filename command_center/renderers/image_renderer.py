"""
Command Center — Image Renderer
==================================
Image generation and manipulation utilities.
"""

import base64
import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def image_to_base64_block(image_bytes: bytes, alt: str = "Generated image", mime_type: str = "image/png") -> dict:
    """Convert raw image bytes to a rich content image block."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return {
        "type": "image",
        "src": f"data:{mime_type};base64,{b64}",
        "alt": alt,
    }


def create_placeholder_image(width: int = 400, height: int = 300, text: str = "No Image") -> Optional[bytes]:
    """Create a simple placeholder image. Requires Pillow."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.warning("Pillow not installed, image generation unavailable")
        return None

    img = Image.new("RGB", (width, height), color=(240, 240, 240))
    draw = ImageDraw.Draw(img)
    draw.text((width // 2 - 30, height // 2 - 10), text, fill=(128, 128, 128))

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()
