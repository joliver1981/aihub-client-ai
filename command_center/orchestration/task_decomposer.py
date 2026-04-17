"""
Command Center — Task Decomposer
===================================
Breaks complex requests into ordered sub-tasks targeting different agents/tools.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def match_agent_for_task(task_description: str, agents: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Simple keyword matching to find the best agent for a task.
    Returns the matched agent dict or empty dict.
    """
    task_lower = task_description.lower()

    for agent in agents:
        agent_name = (agent.get("name") or agent.get("description") or "").lower()
        # Check if any word in agent name appears in task
        agent_words = agent_name.split()
        for word in agent_words:
            if len(word) > 3 and word in task_lower:
                return agent

    return {}
