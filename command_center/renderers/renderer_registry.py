"""
Command Center — Renderer Registry
=====================================
Maps block types to renderer functions. Extensible via plugins.
"""

import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

# Registry: block_type -> renderer function
_renderers: Dict[str, Callable] = {}


def register(block_type: str, renderer_fn: Callable):
    """Register a renderer for a block type."""
    _renderers[block_type] = renderer_fn
    logger.info(f"Registered renderer for block type: {block_type}")


def get_renderer(block_type: str) -> Callable:
    """Get the renderer for a block type."""
    return _renderers.get(block_type)


def list_renderers() -> List[str]:
    """List all registered block types."""
    return list(_renderers.keys())


def render_block(block: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process a single content block through its renderer.
    If no custom renderer exists, returns the block as-is.
    """
    block_type = block.get("type", "text")
    renderer = _renderers.get(block_type)
    if renderer:
        try:
            return renderer(block)
        except Exception as e:
            logger.error(f"Renderer error for {block_type}: {e}")
            return block
    return block


def render_blocks(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Process all content blocks through their renderers."""
    return [render_block(b) for b in blocks]
