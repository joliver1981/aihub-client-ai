"""
SMS completion action.

Sends a text message via the platform's hosted SMS service
(`/api/notifications/sms`), the same endpoint workflows and agents use.
Subject to the deployment's tenant-level SMS rate limits.

Schema config:
    {
      "type": "sms",
      "label": "Notify on submission",
      "to": "+15551234567",                 // E.164. Required, OR use to_from_field
      "to_from_field": "followup_phone",    // optional: pull number from a collected field
      "message_template": "New speaker program request from {{submitter_name}}: "
                          "{{topic_id.label}} on {{target_date}}.",
      "continue_on_error": true
    }

Notes:
  - Single recipient per action (the Cloud API takes one `to`). To text
    multiple people, list multiple sms actions in `completion.actions`.
  - Carriers truncate around 160 chars. Keep `message_template` short.
  - The platform tracks SMS usage and may rate-limit; that error surfaces
    as a non-success ActionResult with the limit details intact.
"""

import logging
import re
from typing import Dict, List, Optional

from . import ActionHandler, ActionResult, render_template

logger = logging.getLogger(__name__)


# Loose phone-number regex — the upstream Cloud API does the real
# validation / formatting. We just want to catch obviously-empty or
# garbage values before paying for a network round-trip.
_PHONE_RE = re.compile(r'^[+\d][\d\s\-().]{6,}$')


class SmsAction(ActionHandler):
    action_type = 'sms'

    def execute(self, collected_data: Dict, session, config: Dict, schema: Dict) -> ActionResult:
        label = config.get('label') or 'Send SMS'

        # Resolve recipient (priority: explicit 'to' template, then collected field)
        to_template = config.get('to')
        to_from_field = config.get('to_from_field')
        recipient = None
        if to_template:
            rendered = render_template(to_template, collected_data, session, schema)
            if rendered:
                recipient = str(rendered).strip()
        if not recipient and to_from_field:
            recipient = self._lookup_field_value(to_from_field, collected_data)
            if recipient is not None:
                recipient = str(recipient).strip()
        if not recipient:
            return ActionResult(
                action_type=self.action_type, label=label, success=False,
                message="SMS action requires a recipient ('to' or 'to_from_field').",
            )
        if not _PHONE_RE.match(recipient):
            return ActionResult(
                action_type=self.action_type, label=label, success=False,
                message=f"'{recipient}' does not look like a phone number.",
            )

        # Resolve message body
        message_template = config.get('message_template') or config.get('message')
        if not message_template:
            return ActionResult(
                action_type=self.action_type, label=label, success=False,
                message="SMS action requires 'message_template'.",
            )
        message = render_template(message_template, collected_data, session, schema)
        if not message or not str(message).strip():
            return ActionResult(
                action_type=self.action_type, label=label, success=False,
                message="Rendered SMS message is empty.",
            )

        # Send via platform's notification client
        try:
            from notification_client import sms_text_message_alert
        except Exception as e:
            return ActionResult(
                action_type=self.action_type, label=label, success=False,
                message=f"SMS transport unavailable: {e}",
            )

        try:
            result = sms_text_message_alert(
                to=recipient,
                message=str(message),
                agent_name='Data Collection Agent',
            )
        except Exception as e:
            logger.exception("SMS send raised an exception")
            return ActionResult(
                action_type=self.action_type, label=label, success=False,
                message=f"SMS send failed: {e}",
                details={'recipient': recipient},
            )

        if result and result.get('success'):
            return ActionResult(
                action_type=self.action_type, label=label, success=True,
                message=f"SMS sent to {recipient}.",
                details={
                    'recipient': recipient,
                    'message_length': len(str(message)),
                    'response': result,
                },
            )

        # Surface the error verbatim — including rate-limit info if present
        err = (result or {}).get('error') or 'SMS send failed'
        if (result or {}).get('blocked_by_limit'):
            current = result.get('current_usage', 0)
            cap = result.get('max_allowed', 0)
            err = f"Daily SMS limit reached ({current}/{cap})."
        return ActionResult(
            action_type=self.action_type, label=label, success=False,
            message=err,
            details={'recipient': recipient, 'response': result},
        )

    # ------------------------------------------------------------------
    def validate_config(self, config: Dict) -> List[str]:
        errors = []
        if not config.get('to') and not config.get('to_from_field'):
            errors.append("sms action requires 'to' or 'to_from_field'")
        if not (config.get('message_template') or config.get('message')):
            errors.append("sms action requires 'message_template'")
        # Soft warning-territory checks would go in warnings, but the
        # registry's validate_config returns errors only — we only
        # reject things the action genuinely can't run with.
        return errors

    # ------------------------------------------------------------------
    def _lookup_field_value(self, field_id: str, collected_data: Dict):
        # Allow "section.field" or bare "field"
        if '.' in field_id:
            section_id, fid = field_id.split('.', 1)
            return (collected_data.get(section_id) or {}).get(fid)
        for sec_data in collected_data.values():
            if isinstance(sec_data, dict) and field_id in sec_data:
                return sec_data[field_id]
        return None
