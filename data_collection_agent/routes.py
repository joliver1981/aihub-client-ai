"""
Routes for the data collection agent runtime experience.

Three groups of endpoints:

  1. Page route — serves the chat UI for a given config.
  2. Session lifecycle — create / load / list / delete sessions, navigate
     between sections, update fields directly from the UI.
  3. Conversation — POST a message, get the agent's response with metadata.
  4. Submission — execute the configured completion actions.

The blueprint itself is created in `data_collection_agent/__init__.py`. This
file just registers route handlers onto it.
"""

import logging
import os
from logging.handlers import WatchedFileHandler
from typing import Any, Dict

from flask import Blueprint, jsonify, render_template, request

from CommonUtils import rotate_logs_on_startup, get_log_path

from .agent import DataCollectionAgent
from .actions import ActionRegistry
from .auth_token import decode_token, extract_session_overrides, is_configured as jwt_configured
from .branding import resolve_branding, branding_to_style_block, safe_url
from .voice_settings import resolve_voice_settings
from . import debug_mode as debug_mod
from .field_extractor import extract_and_save_fields
from .identity import (
    apply_identity_cookie,
    assert_session_owner,
    current_identity,
    is_test_mode,
    require_identity,
)
from .schema_loader import (
    list_schemas,
    load_schema,
    get_section_order,
)
from .state_manager import (
    set_voice_mode,
    CollectionSession,
    create_session,
    delete_session,
    get_user_sessions,
    load_session,
    save_session,
    set_current_section,
    set_status,
    set_section_status,
    SECTION_NOT_STARTED,
    SECTION_IN_PROGRESS,
    SECTION_COMPLETE,
    STATUS_SUBMITTED,
    STATUS_SUBMISSION_FAILED,
    append_submission_log,
)
from .validation_engine import (
    validate_all,
    validate_field,
    coerce_value,
)

import time as _time_mod
# Cache-buster appended to every static asset URL so an app restart
# guarantees the browser refreshes JS/CSS instead of serving stale copies.
_ASSET_VERSION = str(int(_time_mod.time()))

rotate_logs_on_startup(os.getenv('DCA_ROUTES_LOG', get_log_path('data_collection_routes_log.txt')))

logger = logging.getLogger("DataCollectionRoutes")
log_level = getattr(logging, os.getenv('LOG_LEVEL', 'DEBUG'), logging.DEBUG)
logger.setLevel(log_level)
_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
_handler = WatchedFileHandler(
    filename=os.getenv('DCA_ROUTES_LOG', get_log_path('data_collection_routes_log.txt')),
    encoding='utf-8',
)
_handler.setFormatter(_formatter)
logger.addHandler(_handler)


# ----------------------------------------------------------------------
# Auth helper — degrade gracefully if role_decorators isn't importable
# ----------------------------------------------------------------------
try:
    from role_decorators import api_key_or_session_required as _auth_decorator
except Exception:  # pragma: no cover
    logger.warning("role_decorators not available — falling back to no-op auth")

    def _auth_decorator(*_args, **_kwargs):
        def wrap(fn):
            return fn
        return wrap


def _current_user_id() -> str:
    """
    Best-effort current user identification.

    Now delegates to identity.current_identity() which centralizes the
    test-mode flag, JWT identity, platform auth, and anonymous-cookie
    fallback. Kept as a thin wrapper so existing call sites don't need to
    change.
    """
    return current_identity().user_id


# ---- Legacy helper kept for reference; not used anymore ---------------
def _legacy_user_id_lookup() -> str:
    try:
        from flask_login import current_user
        if current_user and current_user.is_authenticated:
            return str(getattr(current_user, 'id', None) or getattr(current_user, 'get_id', lambda: '')())
    except Exception:
        pass
    try:
        from flask import session as flask_session
        uid = flask_session.get('user_id') or flask_session.get('id')
        if uid:
            return str(uid)
    except Exception:
        pass
    return os.environ.get('API_KEY', 'anonymous')[:32]


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _load_or_404(session_id: str):
    """Load a session, 404 if not found. Does NOT check ownership."""
    session = load_session(session_id)
    if not session:
        return None, (jsonify({'status': 'error', 'error': 'Session not found'}), 404)
    return session, None


def _load_owned_or_error(session_id: str):
    """
    Load a session AND verify the current identity owns it. Returns
    (session, None) on success, or (None, error_response) on any failure.

    In test mode the ownership check is a no-op. In production mode this
    enforces session privacy across users on the same instance.
    """
    session, err = _load_or_404(session_id)
    if err:
        return None, err
    allowed, err = assert_session_owner(session)
    if not allowed:
        return None, err
    return session, None


def _serialize_session(session: CollectionSession) -> dict:
    """Shape a session for the frontend. Excludes nothing currently — full echo."""
    return session.to_dict()


# ======================================================================
# Route registration
# ======================================================================

def register_routes(bp: Blueprint):
    """Attach all runtime routes to the data_collection_agent blueprint."""

    # ------------------------------------------------------------------
    # Page routes
    # ------------------------------------------------------------------
    @bp.route('/data-collection/', methods=['GET'])
    @bp.route('/data-collection', methods=['GET'])
    @_auth_decorator()
    def data_collection_gallery():
        """End-user gallery: pick a data collection agent to chat with.

        Non-admins see schemas without "Edit" / "Open Schema Builder"
        controls. Admins see everything. Test mode treats all visitors
        as admin (see identity.is_test_mode()).
        """
        ident = current_identity()
        # No specific schema for the gallery — use app-level branding only.
        branding = resolve_branding(schema=None)

        all_schemas = list_schemas()

        # For each schema, count this user's in-progress sessions so the
        # gallery card can show a "2 in progress" badge — supports the
        # multi-session resume use case.
        user_sessions_by_config: Dict[str, int] = {}
        if ident.user_id:
            for sess in get_user_sessions(user_id=ident.user_id):
                user_sessions_by_config[sess.config_id] = (
                    user_sessions_by_config.get(sess.config_id, 0) + 1
                )

        from flask import make_response
        resp = make_response(render_template(
            'gallery.html',
            schemas=all_schemas,
            user_sessions_by_config=user_sessions_by_config,
            identity=ident,
            is_admin=ident.is_admin,
            is_test_mode=is_test_mode(),
            branding=branding,
            branding_style=branding_to_style_block(branding),
            safe_logo_url=safe_url(branding.get('logo_url')),
            safe_favicon_url=safe_url(branding.get('favicon_url')),
        ))
        # See runtime page handler for rationale — disable bfcache so the
        # browser can't restore in-flight TTS audio across navigations.
        resp.headers['Cache-Control'] = 'no-store'
        return apply_identity_cookie(resp)

    @bp.route('/data-collection/my-sessions', methods=['GET'])
    @_auth_decorator()
    def my_sessions_page():
        """List the current user's open sessions across all schemas.

        Lets a user pick up partially-completed forms across visits and
        schemas. Each row shows: which agent, last updated, completion
        progress, and a Resume / Discard button pair.

        Optional ``?config_id=<id>`` filters to a single schema — used by
        the gallery's "N in progress" badge to deep-link into the manager
        view for one specific agent.
        """
        ident = current_identity()
        branding = resolve_branding(schema=None)

        # Optional per-schema filter
        filter_config_id = (request.args.get('config_id') or '').strip() or None

        # Build the schema id -> name map from the catalog so we can show
        # human-readable agent names alongside session metadata
        schemas_by_id = {s['id']: s for s in list_schemas()}
        # If filter is set, get the friendly name for the heading
        filtered_schema_name = None
        if filter_config_id:
            filtered_schema_name = (
                schemas_by_id.get(filter_config_id, {}).get('name')
                or filter_config_id
            )

        rows = []
        if ident.user_id:
            user_sess = get_user_sessions(
                user_id=ident.user_id,
                config_id=filter_config_id,  # state_manager honors this
            )
            for sess in user_sess:
                schema_meta = schemas_by_id.get(sess.config_id, {})
                # Quick completion estimate — count fields with a value vs total
                # required fields across all sections in the schema
                full_schema = load_schema(sess.config_id) if sess.config_id else None
                total_required = 0
                filled_required = 0
                if full_schema:
                    for section in full_schema.get('sections') or []:
                        for fld in section.get('fields') or []:
                            if not fld.get('required'):
                                continue
                            total_required += 1
                            v = (sess.collected_data.get(section.get('id')) or {}).get(fld.get('id'))
                            if v not in (None, '', []):
                                filled_required += 1
                pct = int(round(100.0 * filled_required / total_required)) if total_required else 0
                rows.append({
                    'session_id': sess.session_id,
                    'config_id': sess.config_id,
                    'schema_name': schema_meta.get('name') or sess.config_id,
                    'status': sess.status,
                    'updated_at': sess.updated_at,
                    'created_at': sess.created_at,
                    'percent_complete': pct,
                    'current_section_id': sess.current_section_id,
                    'schema_missing': not bool(full_schema),
                })

        from flask import make_response
        resp = make_response(render_template(
            'my_sessions.html',
            sessions=rows,
            filter_config_id=filter_config_id,
            filtered_schema_name=filtered_schema_name,
            identity=ident,
            is_admin=ident.is_admin,
            is_test_mode=is_test_mode(),
            branding=branding,
            branding_style=branding_to_style_block(branding),
            safe_logo_url=safe_url(branding.get('logo_url')),
            safe_favicon_url=safe_url(branding.get('favicon_url')),
        ))
        # See runtime page handler — no-store opts out of bfcache.
        resp.headers['Cache-Control'] = 'no-store'
        return apply_identity_cookie(resp)

    @bp.route('/data-collection/<config_id>', methods=['GET'])
    @_auth_decorator()
    def data_collection_page(config_id):
        """Serve the data collection chat page for a specific schema.

        Optionally accepts ``?prefill=<JWT>`` for deep-link callers (MER360
        etc.). The JWT is purely additive — direct URL access without a
        token continues to work. Bad / expired tokens are silently ignored
        with a warning logged; we never return 4xx for a bad token.
        """
        # Reserve specific paths for the wizard / admin / my-sessions etc.
        # so they don't collide with schema ids
        if config_id in ('builder', 'admin', 'my-sessions'):
            return ('', 404)
        schema = load_schema(config_id)

        # Try to decode any prefill JWT — this is optional and never gates
        # access. Pull the raw token string off the query so we can forward
        # it to the create-session POST via the page bootstrap.
        prefill_token = (request.args.get('prefill') or '').strip()
        jwt_claims = None
        if prefill_token:
            jwt_claims, jwt_err = decode_token(prefill_token)
            if jwt_err:
                logger.warning(
                    f"Ignoring ?prefill= token on /data-collection/{config_id}: {jwt_err}"
                )
            elif jwt_claims and schema:
                # If the token specifies a config_id, make sure it matches
                ovr = extract_session_overrides(jwt_claims, expected_config_id=config_id)
                if '__config_id_mismatch' in ovr:
                    expected, got = ovr['__config_id_mismatch'], config_id
                    logger.warning(
                        f"Token's config_id={expected!r} doesn't match URL "
                        f"config_id={got!r}; ignoring prefill"
                    )
                    jwt_claims = None  # Don't apply mismatched prefill
                    prefill_token = ''  # Don't forward to the create-session call

        if not schema:
            branding = resolve_branding(schema=None, jwt_claims=jwt_claims)
            ident = current_identity(jwt_claims=jwt_claims)
            return render_template(
                'data_collection.html',
                config_id=config_id,
                schema_name=f"Unknown form ({config_id})",
                schema_missing=True,
                branding=branding,
                branding_style=branding_to_style_block(branding),
                safe_logo_url=safe_url(branding.get('logo_url')),
                safe_favicon_url=safe_url(branding.get('favicon_url')),
                identity=ident,
                is_admin=ident.is_admin,
                is_test_mode=is_test_mode(),
                prefill_token='',
            ), 404

        # If no live JWT in the URL, check whether the user has an existing
        # in-progress session for this schema that was created with a token —
        # if so, re-use its branding_override so the look stays consistent
        # across reloads.
        resumed_branding_override = None
        if not jwt_claims:
            existing = get_user_sessions(
                user_id=_current_user_id(),
                config_id=config_id,
                include_submitted=False,
            )
            if existing and existing[0].branding_override:
                resumed_branding_override = existing[0].branding_override

        # Synthesize a faux jwt_claims so resolve_branding's hierarchy applies
        # the resumed override at the JWT level
        effective_jwt_claims = jwt_claims
        if resumed_branding_override and not effective_jwt_claims:
            effective_jwt_claims = {'branding': resumed_branding_override}

        branding = resolve_branding(schema=schema, jwt_claims=effective_jwt_claims)
        # Schema-level display_name override wins over schema['name'] for the title
        display_name = branding.get('display_name') or schema.get('name', config_id)

        # Resolve identity here so the template knows whether to expose
        # admin shortcuts (Edit Schema link, etc.). JWT users are never admin.
        ident = current_identity(jwt_claims=jwt_claims)

        from flask import make_response
        resp = make_response(render_template(
            'data_collection.html',
            config_id=config_id,
            schema_name=display_name,
            schema_missing=False,
            branding=branding,
            branding_style=branding_to_style_block(branding),
            safe_logo_url=safe_url(branding.get('logo_url')),
            safe_favicon_url=safe_url(branding.get('favicon_url')),
            identity=ident,
            is_admin=ident.is_admin,
            is_test_mode=is_test_mode(),
            debug_mode_enabled=debug_mod.is_enabled(),
            # Schema's advisory "this form really wants HTTPS" hint —
            # rendered as a banner client-side when window.location is HTTP.
            requires_secure_context=bool(schema.get('requires_secure_context')),
            # Forwarded to JS so it can be passed to POST /sessions
            prefill_token=prefill_token,
            # Cache-buster: the templates use ?v={{ asset_version }} on every
            # JS/CSS link so browser-cached older copies don't get used.
            # Picks up the server's start time on boot — every restart bumps it.
            asset_version=_ASSET_VERSION,
        ))
        # Disable bfcache for the runtime page. Otherwise Chrome / Edge /
        # Safari snapshot the running page (including <audio> elements
        # with TTS blobs paused mid-stream) and resume them byte-for-byte
        # on back/forward navigation — which was causing the AI to
        # "replay an old conversation in the nice voice" symptom.
        # Cache-Control: no-store is the documented opt-out for bfcache.
        resp.headers['Cache-Control'] = 'no-store'
        return apply_identity_cookie(resp)

    # ------------------------------------------------------------------
    # Schema endpoint (used by the progress panel)
    # ------------------------------------------------------------------
    @bp.route('/api/data-collection/schema/<config_id>', methods=['GET'])
    @_auth_decorator()
    def get_schema(config_id):
        schema = load_schema(config_id)
        if not schema:
            return jsonify({'status': 'error', 'error': 'Schema not found'}), 404
        return jsonify({'status': 'success', 'schema': schema})

    @bp.route('/api/data-collection/configs', methods=['GET'])
    @_auth_decorator()
    def list_configs():
        return jsonify({'status': 'success', 'configs': list_schemas()})

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    @bp.route('/api/data-collection/sessions', methods=['GET'])
    @_auth_decorator()
    def list_user_sessions():
        config_id = request.args.get('config_id')
        include_submitted = request.args.get('include_submitted', '').lower() in ('1', 'true', 'yes')
        sessions = get_user_sessions(
            user_id=_current_user_id(),
            config_id=config_id,
            include_submitted=include_submitted,
        )
        return jsonify({
            'status': 'success',
            'sessions': [_serialize_session(s) for s in sessions],
        })

    @bp.route('/api/data-collection/sessions', methods=['POST'])
    @_auth_decorator()
    def start_session():
        """Create a new collection session for a given config.

        Optionally accepts a ``prefill_token`` field — a JWT issued by an
        external caller (e.g. MER360). When valid, the token's `prefill`
        claim seeds `collected_data`, the `callback_url` is stored on the
        session for the submission pipeline, and any `branding` claim is
        persisted as a session-level override so the look stays consistent
        across reloads. Bad / missing tokens are silently ignored.
        """
        data = request.get_json() or {}
        config_id = data.get('config_id')
        if not config_id:
            return jsonify({'status': 'error', 'error': 'config_id is required'}), 400
        schema = load_schema(config_id)
        if not schema:
            return jsonify({'status': 'error', 'error': f'Schema not found: {config_id}'}), 404

        # ---- Decode optional prefill token -----------------------------
        prefill_token = (data.get('prefill_token') or '').strip()
        overrides: Dict[str, Any] = {}
        if prefill_token:
            jwt_claims, jwt_err = decode_token(prefill_token)
            if jwt_err:
                logger.warning(f"Ignoring prefill_token on session create: {jwt_err}")
            elif jwt_claims:
                overrides = extract_session_overrides(jwt_claims, expected_config_id=config_id)
                if '__config_id_mismatch' in overrides:
                    logger.warning(
                        f"prefill_token config_id mismatch — applying user identity / "
                        f"callback only, dropping prefill data"
                    )
                    overrides.pop('prefill', None)

        # User identity: token wins over the platform-derived id
        user_id = overrides.get('user_id') or _current_user_id()

        # Start in the first section
        order = get_section_order(schema)
        first_section = order[0] if order else None
        session = create_session(
            config_id=config_id,
            user_id=user_id,
            initial_section_id=first_section,
        )

        # Apply token overrides directly onto the session
        if 'user_name' in overrides:
            session.user_name = overrides['user_name']
        if 'user_email' in overrides:
            session.user_email = overrides['user_email']
        if 'callback_url' in overrides:
            session.external_callback_url = overrides['callback_url']
        if 'callback_secret_ref' in overrides:
            session.external_callback_secret_ref = overrides['callback_secret_ref']
        if 'return_url' in overrides:
            session.return_url = overrides['return_url']
        if 'branding' in overrides:
            session.branding_override = overrides['branding']

        # Apply prefill data: deep-merge into collected_data
        prefill_data = overrides.get('prefill') or {}
        if isinstance(prefill_data, dict):
            for sec_id, fields in prefill_data.items():
                if not isinstance(fields, dict):
                    continue
                # Only accept prefill into known sections, and only known field ids
                if not any(s.get('id') == sec_id for s in (schema.get('sections') or [])):
                    logger.warning(f"prefill ignored for unknown section_id={sec_id!r}")
                    continue
                for f_id, val in fields.items():
                    # Validate the field exists in this section
                    section = next(
                        (s for s in schema.get('sections') or [] if s.get('id') == sec_id),
                        None,
                    )
                    if section and any(f.get('id') == f_id for f in section.get('fields') or []):
                        session.set_field_value(sec_id, f_id, val)
                    else:
                        logger.warning(
                            f"prefill ignored for unknown field_id={f_id!r} in section={sec_id!r}"
                        )

        # Initialize section_status entries
        for sid in order:
            session.section_status.setdefault(sid, SECTION_NOT_STARTED)
        if first_section:
            session.section_status[first_section] = SECTION_IN_PROGRESS
        save_session(session)

        # Have the agent emit an opening greeting and persist it as the
        # first assistant message in chat_history. This avoids the frontend
        # having to send a synthetic "kickoff" user message that would be
        # replayed visibly on session resume.
        try:
            agent = DataCollectionAgent(session=session, schema=schema)
            agent.bootstrap_greeting()
            # Reload the session from disk so the saved greeting is included
            session = load_session(session.session_id) or session
        except Exception as e:
            logger.error(f"bootstrap_greeting failed: {e}", exc_info=True)

        return jsonify({
            'status': 'success',
            'session': _serialize_session(session),
        })

    @bp.route('/api/data-collection/session/<session_id>', methods=['GET'])
    @_auth_decorator()
    def get_session(session_id):
        session, err = _load_owned_or_error(session_id)
        if err:
            return err
        return jsonify({'status': 'success', 'session': _serialize_session(session)})

    @bp.route('/api/data-collection/session/<session_id>', methods=['DELETE'])
    @_auth_decorator()
    def abandon_session(session_id):
        ok = delete_session(session_id)
        return jsonify({'status': 'success' if ok else 'error', 'deleted': ok})

    # ------------------------------------------------------------------
    # Conversation
    # ------------------------------------------------------------------
    @bp.route('/api/data-collection/message', methods=['POST'])
    @_auth_decorator()
    def send_message():
        data = request.get_json() or {}
        session_id = data.get('session_id')
        message = data.get('message', '')
        if not session_id or not message:
            return jsonify({
                'status': 'error',
                'error': 'session_id and message are required',
            }), 400

        session, err = _load_owned_or_error(session_id)
        if err:
            return err

        schema = load_schema(session.config_id)
        if not schema:
            return jsonify({
                'status': 'error',
                'error': f'Schema not found: {session.config_id}',
            }), 404

        # Pre-extraction: run a small fast LLM over the raw user message
        # and the schema, save any field values it finds. The agent is
        # then constructed AFTER this step, so its system prompt reflects
        # the post-extraction state via DATA COLLECTED SO FAR. We also
        # pass the extraction records into the agent so it can show a
        # "JUST CAPTURED THIS TURN" block in the prompt — the explicit
        # delta of "what changed in response to the user's last message".
        # Without that delta, reasoning models tend to re-ask values
        # they should already trust as captured.
        extraction_records = extract_and_save_fields(message, session, schema)

        try:
            agent = DataCollectionAgent(
                session=session, schema=schema,
                just_extracted=extraction_records,
            )
            response_text, metadata = agent.process_message(message)
            # Surface the extraction records to the client so the debug
            # panel + UI can show what was auto-captured.
            if extraction_records and isinstance(metadata, dict):
                metadata['extraction_records'] = extraction_records
        except Exception as e:
            logger.error(f"Error in process_message: {e}", exc_info=True)
            return jsonify({
                'status': 'error',
                'error': f'Agent error: {e}',
            }), 500

        return jsonify({
            'status': 'success',
            'response': response_text,
            'metadata': metadata,
        })

    # ------------------------------------------------------------------
    # Direct UI actions (navigate, inline field edits)
    # ------------------------------------------------------------------
    @bp.route('/api/data-collection/session/<session_id>/navigate', methods=['POST'])
    @_auth_decorator()
    def navigate(session_id):
        data = request.get_json() or {}
        section_id = data.get('section_id')
        if not section_id:
            return jsonify({'status': 'error', 'error': 'section_id is required'}), 400

        session, err = _load_owned_or_error(session_id)
        if err:
            return err
        schema = load_schema(session.config_id)
        if not schema:
            return jsonify({'status': 'error', 'error': 'Schema not found'}), 404

        # Update state
        session = set_current_section(session_id, section_id)

        # Inject a system message so the agent has context next turn
        session.append_chat(
            'system',
            f"User navigated to section '{section_id}' from the progress panel.",
        )
        save_session(session)

        return jsonify({
            'status': 'success',
            'session': _serialize_session(session),
        })

    # ------------------------------------------------------------------
    # Voice (Phase 2): SSE message stream, TTS endpoint, voice-mode toggle
    # ------------------------------------------------------------------
    @bp.route('/api/data-collection/message-stream', methods=['POST'])
    @_auth_decorator()
    def send_message_stream():
        """
        Server-Sent Events variant of /message — for the streaming-hybrid
        voice path (Path B). Sentence-chunked transcript events let the
        frontend pipeline TTS calls so time-to-first-audio drops to ~one
        sentence's worth of synthesis (vs. waiting for the whole reply).

        Event types:
          - transcript_chunk : { text: str, final: false }   one sentence
          - transcript_final : { text: str, final: true }    full text
          - rich_blocks      : list of rich content blocks
          - actions          : side-channel UI actions
          - metadata         : full metadata dict (phase, current_section, etc.)
          - done             : end-of-stream sentinel

        We don't stream from the LLM token-by-token here (that requires
        rewriting the agent loop). For sub-second turn-taking, use Path D
        (OpenAI Realtime API) — true bidirectional streaming.
        """
        import json as _json
        import re

        data = request.get_json() or {}
        session_id = data.get('session_id')
        message = data.get('message', '')
        if not session_id or not message:
            return jsonify({
                'status': 'error',
                'error': 'session_id and message are required',
            }), 400

        session, err = _load_owned_or_error(session_id)
        if err:
            return err
        schema = load_schema(session.config_id)
        if not schema:
            return jsonify({'status': 'error', 'error': f'Schema not found: {session.config_id}'}), 404

        def _split_sentences(text: str):
            """
            Split a response into sentence-sized chunks for TTS pipelining.
            Keeps trailing punctuation with the preceding sentence and
            preserves blank-line paragraph breaks as separate chunks.
            """
            if not text:
                return []
            text = text.strip()
            parts = re.split(r'(?<=[.!?])\s+|\n{2,}', text)
            return [p.strip() for p in parts if p and p.strip()]

        def _sse(event: str, data_obj) -> str:
            return f"event: {event}\ndata: {_json.dumps(data_obj, default=str)}\n\n"

        def _generate():
            # Run the extractor + agent INSIDE the generator so we can
            # emit progress events as the work happens. Otherwise the
            # browser sees nothing until the entire turn finishes — which
            # is what causes the dreaded indefinite "Thinking…" hang
            # when the proxy is slow or the LLM is reasoning hard.
            try:
                # Open with an immediate keepalive frame — flushes the
                # browser's response headers and confirms the SSE channel
                # is alive. The frontend ignores 'progress' events that
                # have no transcript content.
                yield _sse('progress', {'stage': 'received'})

                # 1) Pre-agent extraction — saves any field values from
                # the raw message directly to session.collected_data.
                # The agent is constructed AFTER this step, so its
                # system prompt's DATA COLLECTED SO FAR reflects the
                # updated state. No synthetic note injection — single
                # source of truth for the agent's view of the world.
                try:
                    yield _sse('progress', {'stage': 'extracting'})
                    extraction_records = extract_and_save_fields(message, session, schema)
                except Exception as e:
                    logger.error(f"Extractor failed: {e}", exc_info=True)
                    extraction_records = []
                    yield _sse('progress', {'stage': 'extract_failed', 'error': str(e)[:200]})

                # 2) Conversational agent. Pass the extraction records so
                # the system prompt can show "JUST CAPTURED THIS TURN".
                try:
                    yield _sse('progress', {'stage': 'agent_thinking'})
                    agent = DataCollectionAgent(
                        session=session, schema=schema,
                        just_extracted=extraction_records,
                    )
                    response_text, metadata = agent.process_message(message)
                    if extraction_records and isinstance(metadata, dict):
                        metadata['extraction_records'] = extraction_records
                except Exception as e:
                    logger.error(f"Error in process_message (stream): {e}", exc_info=True)
                    yield _sse('error', {'error': f'Agent error: {e}'})
                    return

                # 3) Stream the response.
                sentences = _split_sentences(response_text)
                for s in sentences:
                    yield _sse('transcript_chunk', {'text': s, 'final': False})
                yield _sse('transcript_final', {'text': response_text, 'final': True})
                rich_blocks = (metadata or {}).get('rich_blocks') or []
                if rich_blocks:
                    yield _sse('rich_blocks', rich_blocks)
                actions = (metadata or {}).get('actions') or []
                if actions:
                    yield _sse('actions', actions)
                yield _sse('metadata', metadata or {})
                yield _sse('done', {'ok': True})
            except GeneratorExit:
                pass  # client disconnected mid-stream
            except Exception as e:
                logger.error(f"SSE generator error: {e}", exc_info=True)
                yield _sse('error', {'error': str(e)})

        from flask import Response, stream_with_context
        return Response(
            stream_with_context(_generate()),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-store',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive',
            },
        )

    @bp.route('/api/data-collection/tts', methods=['POST'])
    @_auth_decorator()
    def synthesize_tts():
        """
        Server-side TTS for the streaming-hybrid voice path (Path B).

        Body: { text: str, voice?: str, format?: 'mp3'|'wav'|'opus' }
        Response: audio bytes (Content-Type matches the format) on success,
                  or 200 with `{"status":"browser_fallback"}` JSON when the
                  configured provider is "browser" (frontend uses
                  SpeechSynthesisUtterance instead).

        Path D (Realtime) does NOT use this — TTS is in-stream there.
        """
        data = request.get_json() or {}
        text = (data.get('text') or '').strip()
        voice = (data.get('voice') or '').strip() or None
        fmt = (data.get('format') or 'mp3').lower()
        if not text:
            return jsonify({'status': 'error', 'error': 'text is required'}), 400

        # Cap input length so a runaway request can't synthesize forever
        if len(text) > 4000:
            text = text[:4000]

        from .voice import synthesize as voice_synth
        audio, mime, err = voice_synth(text=text, voice=voice, fmt=fmt)

        if mime == 'browser':
            # Provider is intentionally browser-only — frontend uses
            # SpeechSynthesisUtterance. Return JSON so the client knows.
            return jsonify({'status': 'browser_fallback'})
        if err or not audio:
            return jsonify({
                'status': 'error',
                'error': err or 'no audio',
                'fallback': 'browser',
            }), 502

        from flask import Response
        return Response(audio, mimetype=mime, headers={
            'Cache-Control': 'no-store',
            'X-DCA-Voice-Provider': data.get('provider') or 'auto',
        })

    @bp.route('/api/data-collection/session/<session_id>/voice-mode', methods=['POST'])
    @_auth_decorator()
    def toggle_voice_mode(session_id):
        """Flip voice_mode on the session.

        Body: { "enabled": true | false }
        Returns the updated session — the next /message turn will pick up
        the new system prompt automatically (voice-friendly addendum is
        added when enabled=true).
        """
        session, err = _load_owned_or_error(session_id)
        if err:
            return err
        data = request.get_json() or {}
        enabled = bool(data.get('enabled'))
        updated = set_voice_mode(session_id, enabled)
        return jsonify({
            'status': 'success',
            'session': _serialize_session(updated or session),
        })

    @bp.route('/api/data-collection/<config_id>/voice-settings', methods=['GET'])
    def get_voice_settings(config_id):
        """Resolved voice settings for a given form (admin/schema/JWT layered).

        Public read — voice settings don't expose anything sensitive, and the
        frontend needs them before the user has authenticated through the
        chat. The JWT prefill claim, when present, can override per session.
        """
        schema = load_schema(config_id)
        if not schema:
            return jsonify({'status': 'error', 'error': 'Schema not found'}), 404
        # JWT override is read from the same `?prefill=` token used elsewhere
        jwt_claims = None
        token = request.args.get('prefill')
        if token and jwt_configured():
            try:
                jwt_claims = decode_token(token) or None
            except Exception:
                jwt_claims = None
        settings = resolve_voice_settings(schema=schema, jwt_claims=jwt_claims)
        return jsonify({
            'status': 'success',
            'settings': settings,
        })

    @bp.route('/api/data-collection/session/<session_id>/debug', methods=['GET'])
    @_auth_decorator()
    def get_debug_events(session_id):
        """Recent debug events for this session, used by the inspection
        panel. Tied to test mode / DCA_DEBUG_MODE — when neither is on,
        returns an empty list and `enabled=false`.

        Query params:
          - since_ms: only return events with ts_ms > since_ms (for polling)
          - types:    comma-separated list of event types to include
          - limit:    cap (most recent N if list is longer)
        """
        session, err = _load_owned_or_error(session_id)
        if err:
            return err
        if not debug_mod.is_enabled():
            return jsonify({
                'status': 'success',
                'enabled': False,
                'events': [],
            })
        since_raw = request.args.get('since_ms')
        try:
            since_ms = int(since_raw) if since_raw else None
        except ValueError:
            since_ms = None
        types_raw = (request.args.get('types') or '').strip()
        types = [t.strip() for t in types_raw.split(',') if t.strip()] or None
        try:
            limit = int(request.args.get('limit', '0')) or None
        except ValueError:
            limit = None
        events = debug_mod.get_events(
            session_id, since_ms=since_ms, types=types, limit=limit
        )
        return jsonify({
            'status': 'success',
            'enabled': True,
            'events': events,
            'event_types': debug_mod.KNOWN_EVENT_TYPES,
        })

    @bp.route('/api/data-collection/session/<session_id>/debug', methods=['DELETE'])
    @_auth_decorator()
    def clear_debug_events(session_id):
        """Wipe the in-memory debug buffer for this session."""
        session, err = _load_owned_or_error(session_id)
        if err:
            return err
        debug_mod.clear_events(session_id)
        return jsonify({'status': 'success'})

    @bp.route('/api/data-collection/session/<session_id>/update-field', methods=['POST'])
    @_auth_decorator()
    def update_field_inline(session_id):
        """Inline field edit from the progress panel (no chat round-trip)."""
        data = request.get_json() or {}
        section_id = data.get('section_id')
        field_id = data.get('field_id')
        value = data.get('value')
        if not section_id or not field_id:
            return jsonify({
                'status': 'error',
                'error': 'section_id and field_id are required',
            }), 400

        session, err = _load_owned_or_error(session_id)
        if err:
            return err
        schema = load_schema(session.config_id)
        if not schema:
            return jsonify({'status': 'error', 'error': 'Schema not found'}), 404

        from .schema_loader import get_field
        field_def = get_field(schema, section_id, field_id)
        if not field_def:
            return jsonify({
                'status': 'error',
                'error': f'Field not found: {section_id}.{field_id}',
            }), 404

        coerced, coerce_err = coerce_value(value, field_def.get('type', 'text'))
        if coerce_err:
            return jsonify({'status': 'error', 'error': coerce_err}), 400

        # Validate with new value tentatively in place
        tentative = {**session.collected_data}
        tentative.setdefault(section_id, {})
        tentative[section_id] = {**tentative[section_id], field_id: coerced}
        errors = validate_field(schema, section_id, field_id, coerced, tentative)
        if errors:
            return jsonify({
                'status': 'error',
                'error': 'Validation failed',
                'validation_errors': errors,
            }), 400

        # Commit
        session.set_field_value(section_id, field_id, coerced)
        # Add a system note to the chat for context continuity
        label = field_def.get('label', field_id)
        session.append_chat(
            'system',
            f"User updated '{label}' to '{coerced}' via the progress panel.",
        )
        save_session(session)

        # Compute the same human-friendly display string the recap panel
        # uses (Yes/No for booleans, label for select/lookup ids, etc.)
        # so the frontend can update any rendered recap rows in place
        # without duplicating the formatter in JS.
        from .agent import _format_value_for_display
        display_value = _format_value_for_display(coerced, field_def, schema)

        return jsonify({
            'status': 'success',
            'session': _serialize_session(session),
            'updated_field': {
                'section_id': section_id,
                'field_id': field_id,
                'value': coerced,
                'display_value': display_value,
            },
        })

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------
    @bp.route('/api/data-collection/session/<session_id>/submit', methods=['POST'])
    @_auth_decorator()
    def submit_session(session_id):
        """Run the completion actions pipeline for a submitted session."""
        session, err = _load_owned_or_error(session_id)
        if err:
            return err
        schema = load_schema(session.config_id)
        if not schema:
            return jsonify({'status': 'error', 'error': 'Schema not found'}), 404

        # Final validation gate — refuse to submit if anything is invalid
        errors = validate_all(schema, session.collected_data)
        if errors:
            return jsonify({
                'status': 'error',
                'error': 'Validation failed before submission',
                'validation_errors': errors,
            }), 400

        # Verify all required sections are complete
        from .validation_engine import is_section_complete
        for sid in get_section_order(schema):
            if not is_section_complete(schema, sid, session.collected_data):
                return jsonify({
                    'status': 'error',
                    'error': f'Section "{sid}" is not complete',
                }), 400

        actions = list((schema.get('completion') or {}).get('actions') or [])

        # If a deep-link caller provided an external callback URL via the
        # JWT prefill token, append a webhook action targeting it. This is
        # how MER360 (or any caller) gets notified of the submission without
        # us having to hard-code MER360 specifics in the schema.
        if session.external_callback_url:
            actions.append({
                'type': 'webhook',
                'label': 'External callback (deep-link caller)',
                'url': session.external_callback_url,
                'include_metadata': True,
                # Don't break the pipeline if the external endpoint is flaky —
                # the form was successfully filled, that's what the user did.
                'continue_on_error': True,
            })

        pipeline = ActionRegistry.execute_pipeline(
            actions=actions,
            collected_data=session.collected_data,
            session=session,
            schema=schema,
        )

        # Log every action result in the session
        for r in pipeline.results:
            append_submission_log(session_id, r.to_dict())

        # Update session status based on pipeline outcome
        if pipeline.all_success:
            session = set_status(session_id, STATUS_SUBMITTED)
            confirmation = (
                (schema.get('completion') or {}).get('confirmation_message')
                or 'Your submission has been completed.'
            )
        else:
            session = set_status(session_id, STATUS_SUBMISSION_FAILED)
            confirmation = (
                "One or more completion actions failed. "
                "See the action log for details."
            )

        return jsonify({
            'status': 'success' if pipeline.all_success else 'partial',
            'message': confirmation,
            'pipeline': pipeline.to_dict(),
            'session': _serialize_session(session) if session else None,
            # Set when the session was created from a JWT with a `return_url`
            # claim — frontend uses this to offer a "Back to <caller>" button.
            'return_url': session.return_url if session else None,
        }), (200 if pipeline.all_success else 207)

    # ------------------------------------------------------------------
    # Healthcheck
    # ------------------------------------------------------------------
    @bp.route('/api/data-collection/health', methods=['GET'])
    def health():
        return jsonify({
            'status': 'ok',
            'configs': len(list_schemas()),
            'action_types': ActionRegistry.list_types(),
        })
