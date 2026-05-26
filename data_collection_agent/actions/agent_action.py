"""
Agent delegation completion action.

Sends a message to another platform agent with the collected data, kicking
off downstream processing (review, scheduling, compliance checks, etc.).

Uses the platform's unified chat endpoint `/api/agents/<id>/chat` so we get
the same routing as the rest of the system (general vs. data agents handled
automatically).

Schema config:
    {
      "type": "agent",
      "label": "Notify review agent",
      "agent_id": 15,
      "message_template": "A new submission has arrived. Details:\n\n{{__summary__}}",
      "wait_for_response": false,            // if true, blocks until the agent replies
      "timeout_seconds": 60,
      "continue_on_error": true
    }
"""

import logging
import os
from typing import Dict, List

import requests

from . import ActionHandler, ActionResult, render_template

logger = logging.getLogger(__name__)


def _get_base_url() -> str:
    try:
        from CommonUtils import get_base_url
        return get_base_url().rstrip('/')
    except Exception:
        host_port = os.environ.get('HOST_PORT', '5001')
        return f"http://localhost:{host_port}"


class AgentAction(ActionHandler):
    action_type = 'agent'

    def execute(self, collected_data: Dict, session, config: Dict, schema: Dict) -> ActionResult:
        label = config.get('label') or 'Send to agent'

        agent_id = config.get('agent_id')
        if not agent_id:
            return ActionResult(
                action_type=self.action_type, label=label, success=False,
                message="agent action requires 'agent_id'",
            )

        message_template = config.get('message_template') or '{{__summary__}}'
        message = render_template(message_template, collected_data, session, schema)
        if not message:
            return ActionResult(
                action_type=self.action_type, label=label, success=False,
                message="Rendered message is empty",
            )

        wait = bool(config.get('wait_for_response', False))
        timeout = int(config.get('timeout_seconds') or (60 if wait else 15))

        url = f"{_get_base_url()}/api/agents/{int(agent_id)}/chat"
        api_key = os.environ.get('API_KEY', '')
        headers = {'Content-Type': 'application/json'}
        if api_key:
            headers['X-API-Key'] = api_key

        payload = {
            'prompt': message,
            'history': '[]',
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.exceptions.Timeout:
            if not wait:
                # If we don't care about the response, treat timeout as fire-and-forget success
                return ActionResult(
                    action_type=self.action_type, label=label, success=True,
                    message=f"Message sent to agent {agent_id} (no response wait).",
                    details={'agent_id': agent_id, 'fire_and_forget': True},
                )
            return ActionResult(
                action_type=self.action_type, label=label, success=False,
                message=f"Agent {agent_id} did not respond within {timeout}s",
            )
        except requests.exceptions.RequestException as e:
            return ActionResult(
                action_type=self.action_type, label=label, success=False,
                message=f"Could not reach agent API: {e}",
            )

        if response.status_code != 200:
            return ActionResult(
                action_type=self.action_type, label=label, success=False,
                message=f"Agent API returned HTTP {response.status_code}",
                details={'response': response.text[:300]},
            )

        try:
            body = response.json()
        except Exception:
            body = {}

        agent_response_text = body.get('response', '')
        summary = (
            f"Agent {agent_id} responded ({len(agent_response_text)} chars)."
            if wait and agent_response_text
            else f"Message delivered to agent {agent_id}."
        )

        return ActionResult(
            action_type=self.action_type, label=label, success=True,
            message=summary,
            details={
                'agent_id': agent_id,
                'response_excerpt': agent_response_text[:500] if agent_response_text else '',
                'agent_type': body.get('agent_type'),
            },
        )

    def validate_config(self, config: Dict) -> List[str]:
        errors = []
        if not config.get('agent_id'):
            errors.append("agent action requires 'agent_id'")
        if not config.get('message_template'):
            errors.append("agent action requires 'message_template'")
        return errors
