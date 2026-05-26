"""
External REST API completion action.

Makes an HTTP call to an arbitrary external endpoint with the collected data,
reshaped via a body_mapping template. Designed for direct integration with
external systems that have a REST API.

Schema config:
    {
      "type": "api",
      "label": "Create record",
      "method": "POST",                            // GET / POST / PUT / PATCH / DELETE
      "url": "https://api.example.com/v1/records",
      "headers": {
        "Authorization": "Bearer {{__secret:external_api_key__}}",
        "Content-Type": "application/json"
      },
      "body_mapping": {                            // arbitrary JSON shape with {{...}}
        "type": "{{request_type}}",
        "scheduledFor": "{{target_date}}"
      },
      "query_params": { "source": "dca" },         // optional URL params, also templated
      "success_status_codes": [200, 201],          // default: 2xx
      "timeout_seconds": 30,
      "continue_on_error": false
    }
"""

import json
import logging
from typing import Dict, List

import requests

from . import ActionHandler, ActionResult, render_template

logger = logging.getLogger(__name__)


class ApiAction(ActionHandler):
    action_type = 'api'

    def execute(self, collected_data: Dict, session, config: Dict, schema: Dict) -> ActionResult:
        label = config.get('label') or 'Call external API'

        method = (config.get('method') or 'POST').upper()
        url_template = config.get('url')
        if not url_template:
            return ActionResult(
                action_type=self.action_type, label=label, success=False,
                message="api action requires 'url'",
            )
        url = render_template(url_template, collected_data, session, schema)

        headers = render_template(config.get('headers') or {}, collected_data, session, schema)
        params = render_template(config.get('query_params') or {}, collected_data, session, schema)
        body_mapping = config.get('body_mapping')

        if body_mapping is not None:
            body = render_template(body_mapping, collected_data, session, schema)
        else:
            # Default body: full collected_data
            body = collected_data

        timeout = int(config.get('timeout_seconds') or 30)
        success_codes = set(config.get('success_status_codes') or [])

        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, params=params, timeout=timeout)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, params=params, timeout=timeout)
            elif method in ('POST', 'PUT', 'PATCH'):
                response = requests.request(
                    method, url, headers=headers, params=params, json=body, timeout=timeout,
                )
            else:
                return ActionResult(
                    action_type=self.action_type, label=label, success=False,
                    message=f"Unsupported HTTP method: {method}",
                )
        except requests.exceptions.RequestException as e:
            return ActionResult(
                action_type=self.action_type, label=label, success=False,
                message=f"Request failed: {e}",
                details={'url': url, 'method': method},
            )

        ok = (
            response.status_code in success_codes
            if success_codes
            else 200 <= response.status_code < 300
        )

        # Try to parse the response as JSON for nicer details
        try:
            response_body = response.json()
        except (ValueError, json.JSONDecodeError):
            response_body = response.text[:500]

        if ok:
            return ActionResult(
                action_type=self.action_type, label=label, success=True,
                message=f"{method} {url} → HTTP {response.status_code}",
                details={
                    'status_code': response.status_code,
                    'response': response_body,
                },
            )

        return ActionResult(
            action_type=self.action_type, label=label, success=False,
            message=f"{method} {url} → HTTP {response.status_code}",
            details={
                'status_code': response.status_code,
                'response': response_body,
            },
        )

    def validate_config(self, config: Dict) -> List[str]:
        errors = []
        if not config.get('url'):
            errors.append("api action requires 'url'")
        method = (config.get('method') or 'POST').upper()
        if method not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
            errors.append(f"unsupported HTTP method: {method}")
        return errors
