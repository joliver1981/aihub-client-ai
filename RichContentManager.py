"""
RichContentManager.py
Side-channel storage for rich content blocks (charts, diagrams, etc.)
produced by tools during agent execution.

Follows the same pattern as DataFrameFileManager:
- Tools save rich content blocks during execution
- GeneralAgent.run() loads and deletes them after agent completes
- Content is injected into the SmartContentRenderer pipeline
"""

import os
import json
import logging
from logging.handlers import WatchedFileHandler
import glob
import uuid
from typing import Dict, List, Any
from CommonUtils import get_app_path, get_log_path


# Configure logging
logger = logging.getLogger("RichContentManager")
log_level_name = os.getenv('LOG_LEVEL', 'DEBUG')
log_level = getattr(logging, log_level_name, logging.DEBUG)
logger.setLevel(log_level)

_log_file = os.getenv('RICH_CONTENT_MANAGER_LOG', get_log_path('rich_content_manager.txt'))
if not logger.handlers:
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler = WatchedFileHandler(filename=_log_file, encoding='utf-8')
    handler.setFormatter(formatter)
    logger.addHandler(handler)


class RichContentManager:
    """
    Manages temporary storage for rich content blocks (charts, diagrams, etc.)
    that tools produce during agent execution.

    Tools call save() to store structured blocks; GeneralAgent.run() calls
    load_all_and_delete() after the agent finishes to inject them into the response.
    """

    def __init__(self, temp_dir: str = None):
        self.temp_dir = temp_dir or get_app_path('temp')
        os.makedirs(self.temp_dir, exist_ok=True)

    def _get_pattern(self, user_id: str) -> str:
        if not user_id:
            user_id = "0"
        return os.path.join(self.temp_dir, f"tmp_richcontent_{user_id}_*.json")

    def save(self, block: Dict[str, Any], user_id: str, block_id: str = None) -> str:
        """Save a rich content block (chart, diagram, etc.) to temp storage."""
        if not user_id:
            user_id = "0"
        if not block_id:
            block_id = str(uuid.uuid4())[:8]

        filename = f"tmp_richcontent_{user_id}_{block_id}.json"
        filepath = os.path.join(self.temp_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(block, f, default=str)

        logger.info(f"Saved rich content block to {filepath}")
        return filepath

    def load_all_and_delete(self, user_id: str) -> List[Dict[str, Any]]:
        """Load all rich content blocks for a user and delete the files."""
        if not user_id:
            user_id = "0"

        blocks = []
        for filepath in glob.glob(self._get_pattern(user_id)):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    block = json.load(f)
                blocks.append(block)
                os.remove(filepath)
                logger.info(f"Loaded and deleted rich content from {filepath}")
            except Exception as e:
                logger.error(f"Error loading {filepath}: {e}")
        return blocks

    def cleanup(self, user_id: str):
        """Remove all rich content files for a user."""
        if not user_id:
            user_id = "0"

        for filepath in glob.glob(self._get_pattern(user_id)):
            try:
                os.remove(filepath)
            except Exception as e:
                logger.error(f"Error cleaning up {filepath}: {e}")

    def cleanup_all(self):
        """Clean up all temporary rich content files (useful for app startup)."""
        pattern = os.path.join(self.temp_dir, "tmp_richcontent_*.json")
        for filepath in glob.glob(pattern):
            try:
                os.remove(filepath)
                logger.info(f"Cleaned up: {filepath}")
            except Exception as e:
                logger.error(f"Error cleaning up {filepath}: {e}")


# Module-level instance
rich_content_manager = RichContentManager()
