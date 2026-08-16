"""AI-managed category assignment for newly detected document types.

THE GAP THIS CLOSES: migration 016's seed covered the types that existed at
migration time. A type detected AFTER that has no DocumentTypeCategories row —
and an unmapped type is visible to ADMINS ONLY. Without this hook, every new
type silently disappears from every non-admin's search the moment it is coined.

Flow (owner's decisions, 2026-08-13):
  * AI-managed ON (default): the AI files the type into a category immediately
    (status='active'), and the category's STEWARD group gets an FYI in My Work.
  * AI-managed OFF: the assignment is written status='pending' (admin-only until
    approved on the review page) and the steward gets an approval item.
  * Low-confidence hold (separate setting, default OFF): pending even when
    AI-managed is on, for clients where full automation misfires.

The classifier can only file into EXISTING categories or propose a NEW 1:1
category. Either way the fail-safe holds: a wrong assignment can be recategorised
in the UI; an unfiled type stays admin-only. The AI never edits group grants —
a NEW category starts with ZERO grants (admin-only) until an admin grants it,
so the classifier cannot widen anyone's access.
"""

import json
import logging
import os
import re
from typing import Optional

from doc_search_v3.enumerate_engine import _connect, _llm, _parse_json

logger = logging.getLogger(__name__)


def ensure_category_assignment(document_type: str,
                               sample_text: str = '') -> Optional[dict]:
    """Idempotent: give `document_type` a category row if it lacks one.

    Called from ingestion after type detection. Never raises — a categorisation
    failure must not fail a document ingest; the type simply stays admin-only
    until the review page picks it up.

    Returns a summary dict when an assignment was made, else None.
    """
    import config as cfg
    try:
        if not document_type or not str(document_type).strip():
            return None
        document_type = str(document_type).strip()

        conn, cur = _connect()
        try:
            cur.execute("SELECT 1 FROM DocumentTypeCategories WHERE document_type = ?",
                        document_type)
            if cur.fetchone():
                return None      # already mapped — the common case, one cheap query

            cur.execute("""SELECT category_id, category_slug, category_name
                           FROM DocumentCategories ORDER BY category_name""")
            existing = cur.fetchall()

            choice = _classify(document_type, existing, sample_text)
            confidence = choice.get('confidence') or 0

            category_id = None
            created_category = False
            if choice.get('category_slug'):
                for cid, slug, _name in existing:
                    if slug == choice['category_slug']:
                        category_id = cid
                        break
            if category_id is None:
                # New 1:1 category. Starts with ZERO group grants = admin-only;
                # granting is a human act on the Groups page.
                slug = re.sub(r'[^a-z0-9_]', '_', document_type.lower())[:100]
                cur.execute("""INSERT INTO DocumentCategories
                                   (category_slug, category_name, is_system, created_by)
                               VALUES (?, ?, 0, 'ai_categoriser')""",
                            slug, document_type)
                cur.execute("SELECT category_id FROM DocumentCategories WHERE category_slug = ?",
                            slug)
                category_id = cur.fetchone()[0]
                created_category = True

            ai_managed = bool(getattr(cfg, 'DOC_CATEGORY_AI_MANAGED', True))
            hold_low = bool(getattr(cfg, 'DOC_CATEGORY_HOLD_LOW_CONFIDENCE', False))
            threshold = int(getattr(cfg, 'DOC_CATEGORY_CONFIDENCE_THRESHOLD', 70))
            status = 'active' if ai_managed else 'pending'
            if ai_managed and hold_low and confidence < threshold:
                status = 'pending'

            cur.execute("""INSERT INTO DocumentTypeCategories
                               (document_type, category_id, status, assigned_by,
                                ai_confidence, created_by)
                           VALUES (?, ?, ?, 'ai', ?, 'ai_categoriser')""",
                        document_type, category_id, status, float(confidence))
            conn.commit()

            summary = {'document_type': document_type, 'category_id': category_id,
                       'status': status, 'confidence': confidence,
                       'created_category': created_category,
                       'reason': choice.get('reason', '')}
            logger.info(f"[category-ai] {document_type} -> category {category_id} "
                        f"({status}, conf {confidence}"
                        f"{', NEW category' if created_category else ''})")
            _notify_stewards(cur, conn, summary)
            return summary
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"[category-ai] assignment failed for "
                       f"'{document_type}': {e} — type stays admin-only until "
                       f"reviewed")
        return None


def _classify(document_type: str, existing, sample_text: str) -> dict:
    """Pick an existing category or propose none (=> new 1:1). Mini-LLM."""
    listing = "\n".join(f"- {slug}: {name}" for _cid, slug, name in existing[:200])
    raw = _llm(
        f"A document was classified as type '{document_type}'.\n"
        + (f"Opening text of the document:\n{sample_text[:1500]}\n\n" if sample_text else "")
        + f"Existing document categories:\n{listing}\n\n"
          'Return STRICT JSON: {"category_slug": "<existing slug or null>", '
          '"confidence": <0-100>, "reason": "<one sentence>"}. '
          "Pick the existing category this type belongs in (a lease variant "
          "belongs with the other leases). Return null ONLY when the type "
          "genuinely fits no existing category — null creates a new admin-only "
          "category, which is the safe default when unsure between two.",
        system="You file document types into categories. STRICT JSON only.",
        max_tokens=250)
    out = _parse_json(raw)
    return out if isinstance(out, dict) else {}


def notify_type_stewards(document_type: str, title: str, detail: str,
                         extra: Optional[dict] = None):
    """FYI to the steward groups (can_manage) of the category that owns
    `document_type`, via the approvals sidecar — the platform's non-workflow
    My Work lane. Public seam for OTHER subsystems (e.g. record-set flags in
    the document engine) so steward routing lives in exactly one place.
    Best-effort: never raises."""
    try:
        conn, cur = _connect()
        try:
            cur.execute("""SELECT cg.group_id
                           FROM DocumentTypeCategories tc
                           JOIN DocumentCategoryGroups cg
                             ON cg.category_id = tc.category_id
                           WHERE tc.document_type = ? AND cg.can_manage = 1""",
                        document_type)
            stewards = [r[0] for r in cur.fetchall()]
        finally:
            try:
                conn.close()
            except Exception:
                pass
        if not stewards:
            return
        from automations.approval_store import add_row
        from CommonUtils import get_app_path
        base = get_app_path("automations", f"tenant_{os.getenv('API_KEY')}")
        os.makedirs(base, exist_ok=True)
        payload = dict(extra or {})
        payload.setdefault('document_type', document_type)
        for gid in stewards:
            add_row(base, title, detail, gid, json.dumps(payload),
                    priority=0, assigned_to_type='group')
    except Exception as e:
        logger.info(f"[category-ai] steward notify skipped: {e}")


def _notify_stewards(cur, conn, summary: dict):
    """FYI (active) or approval item (pending) to the category's steward groups,
    via the approvals file sidecar — the platform's non-workflow My Work lane.
    Best-effort: a notification failure never blocks categorisation."""
    try:
        cur.execute("""SELECT group_id FROM DocumentCategoryGroups
                       WHERE category_id = ? AND can_manage = 1""",
                    summary['category_id'])
        stewards = [r[0] for r in cur.fetchall()]
        if not stewards:
            return   # nobody stewards this category yet — the review page shows it
        from automations.approval_store import add_row
        from CommonUtils import get_app_path
        # Same directory convention as the automations manager (manager.py:140):
        # the sidecar's _approvals/ lives under the tenant automations dir.
        base = get_app_path("automations", f"tenant_{os.getenv('API_KEY')}")
        os.makedirs(base, exist_ok=True)
        pending = summary['status'] == 'pending'
        title = (f"{'Review' if pending else 'FYI'}: new document type "
                 f"'{summary['document_type']}' "
                 f"{'awaiting category approval' if pending else 'auto-categorised'}")
        for gid in stewards:
            add_row(base, title,
                    f"AI filed type '{summary['document_type']}' "
                    f"(confidence {summary['confidence']}). {summary['reason']} "
                    f"Manage on the document categories review page.",
                    gid,
                    json.dumps({'source': 'doc_category_ai',
                                'document_type': summary['document_type'],
                                'category_id': summary['category_id'],
                                'status': summary['status']}),
                    priority=2 if pending else 0, assigned_to_type='group')
    except Exception as e:
        logger.info(f"[category-ai] steward notification skipped: {e}")
