"""
Webhook completion action.

Fire-and-forget POST of the collected data to an arbitrary URL. Designed for
Zapier, Make, Power Automate, n8n, or any webhook receiver.

Schema config:
    {
      "type": "webhook",
      "label": "Notify external system",
      "url": "https://hooks.example.com/abc",
      "include_metadata": true,                // adds session_id, config_id, user_id, ts
      "headers": { "X-Custom": "value" },      // optional custom headers (templated)
      "timeout_seconds": 15,
      "continue_on_error": true
    }
"""

import json
import logging
from datetime import datetime
from typing import Dict, List

import requests

from . import ActionHandler, ActionResult, render_template

logger = logging.getLogger(__name__)


class WebhookAction(ActionHandler):
    action_type = 'webhook'

    def execute(self, collected_data: Dict, session, config: Dict, schema: Dict) -> ActionResult:
        label = config.get('label') or 'Fire webhook'

        url_template = config.get('url')
        if not url_template:
            return ActionResult(
                action_type=self.action_type, label=label, success=False,
                message="webhook action requires 'url'",
            )
        url = render_template(url_template, collected_data, session, schema)

        headers = render_template(config.get('headers') or {}, collected_data, session, schema)
        headers.setdefault('Content-Type', 'application/json')

        body = {'data': collected_data}
        if config.get('include_metadata', True):
            body['metadata'] = {
                'session_id': session.session_id,
                'config_id': session.config_id,
                'user_id': session.user_id,
                'submitted_at': session.submitted_at or datetime.utcnow().isoformat(),
                'schema_id': schema.get('id'),
                'schema_name': schema.get('name'),
            }

        timeout = int(config.get('timeout_seconds') or 15)

        try:
            response = requests.post(url, headers=headers, json=body, timeout=timeout)
        except requests.exceptions.RequestException as e:
            return ActionResult(
                action_type=self.action_type, label=label, success=False,
                message=f"Webhook failed: {e}",
                details={'url': url},
            )

        ok = 200 <= response.status_code < 300
        try:
            response_body = response.json()
        except (ValueError, json.JSONDecodeError):
            response_body = response.text[:300]

        if ok:
            return ActionResult(
                action_type=self.action_type, label=label, success=True,
                message=f"Webhook delivered (HTTP {response.status_code}).",
                details={'status_code': response.status_code, 'response': response_body},
            )
        return ActionResult(
            action_type=self.action_type, label=label, success=False,
            message=f"Webhook returned HTTP {response.status_code}.",
            details={'status_code': response.status_code, 'response': response_body},
        )

    def validate_config(self, config: Dict) -> List[str]:
        if not config.get('url'):
            return ["webhook action requires 'url'"]
        return []
