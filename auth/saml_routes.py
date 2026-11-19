"""
SAML sign-in routes

    GET  /auth/saml/login     send the browser to the identity provider
    POST /auth/saml/acs       the identity provider's answer comes back here
    GET  /auth/saml/metadata  AI Hub's own SAML metadata

The password form and local accounts are untouched: this adds a second way
in, shown on the sign-in page only while a SAML provider is switched on.

How one sign-in attempt is kept together
----------------------------------------
The identity provider posts its answer from another site, and the browser
does not send AI Hub's session cookie on such a request. So each attempt gets
its own short-lived cookie (signed, HTTPS-only, sent cross-site) holding the
id of the request we issued, and the same random value is sent to the
identity provider as RelayState. The answer is accepted only when it names
that request and comes back with both. That ties it to the browser that
started it, which a signature alone does not.

Nothing here touches the Flask session until a sign-in has fully passed, so a
failed or test sign-in can never replace the session of someone already
signed in on that browser.
"""

import hmac
import logging
import os
import secrets
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from flask import Blueprint, Response, current_app, flash, redirect, request, url_for
from flask_login import current_user, login_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from auth import saml_provider as saml
from auth.saml_provider import SamlError

logger = logging.getLogger(__name__)

STATE_COOKIE = 'aihub_saml_state'
# How long someone may take at the identity provider (password, MFA, consent)
# before the attempt has to be started again.
STATE_LIFETIME_SECONDS = 3600
_STATE_SALT = 'aihub-saml-sign-in-state'


def saml_provider_config(provider: Dict[str, Any]) -> Dict[str, Any]:
    """A provider row's config with certificates normalised to a list of PEMs."""
    config = dict(provider.get('config') or {})
    try:
        config['idp_certificates'] = saml.normalize_certificates(config.get('idp_certificates'))
    except SamlError:
        config['idp_certificates'] = []
    return config


def public_base_url() -> str:
    """The address users reach AI Hub at: the published address behind a
    reverse proxy, otherwise the one this request arrived on."""
    try:
        from published_addresses import published_address
        base = published_address('APP_PUBLIC_BASE_URL')
    except Exception:
        base = ''
    return base or request.url_root.rstrip('/')


def sp_addresses(config: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """AI Hub's Identifier (Entity ID), Reply URL and metadata address."""
    base = public_base_url()
    metadata_url = base + '/auth/saml/metadata'
    entity_id = ((config or {}).get('sp_entity_id') or '').strip() or metadata_url
    return {'entity_id': entity_id, 'acs_url': base + '/auth/saml/acs', 'metadata_url': metadata_url}


def _enabled_saml_providers() -> List[Dict[str, Any]]:
    from auth.provider_chain import get_enabled_providers
    return [p for p in get_enabled_providers() if p.get('provider_type') == 'saml']


def _load_provider(provider_id: int) -> Optional[Dict[str, Any]]:
    """One provider by id, enabled or not (a test sign-in runs before it is switched on)."""
    import json
    from DataUtils import Get_Identity_Providers

    df = Get_Identity_Providers(provider_id=int(provider_id))
    if df is None or df.empty:
        return None
    row = df.iloc[0]

    def loads(value):
        try:
            return json.loads(value) if value else {}
        except (TypeError, ValueError):
            return {}

    return {
        'id': int(row['id']),
        'provider_type': row['provider_type'],
        'provider_name': row['provider_name'],
        'is_enabled': bool(row['is_enabled']),
        'config': loads(row['config_json']),
        'auto_provision': bool(row['auto_provision']),
        'default_role': int(row['default_role']),
        'group_role_mapping': loads(row['group_role_mapping']),
    }


def _safe_next(target: Optional[str]) -> str:
    """A place inside AI Hub to land on after sign-in, or '' for the default.
    Anything that would leave this site is dropped."""
    target = (target or '').strip()
    if not target or '\\' in target or any(ord(ch) < 32 for ch in target):
        return ''
    parts = urlsplit(target)
    if not parts.scheme and not parts.netloc:
        return target if target.startswith('/') and not target.startswith('//') else ''
    if parts.scheme not in ('http', 'https'):
        return ''
    own_hosts = {request.host.lower(), urlsplit(public_base_url()).netloc.lower()}
    if parts.netloc.lower() not in own_hosts:
        return ''
    return (parts.path or '/') + (('?' + parts.query) if parts.query else '')


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.secret_key, salt=_STATE_SALT)


def _read_state() -> Optional[Dict[str, Any]]:
    raw = request.cookies.get(STATE_COOKIE)
    if not raw:
        return None
    try:
        state = _serializer().loads(raw, max_age=STATE_LIFETIME_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return state if isinstance(state, dict) and state.get('r') and state.get('s') else None


def _set_state_cookie(response, state: Dict[str, Any]):
    # SameSite=None so it comes back on the identity provider's cross-site
    # POST; that requires Secure, which is why SAML needs HTTPS (browsers
    # also accept it on http://localhost, which is what a local test uses).
    response.set_cookie(STATE_COOKIE, _serializer().dumps(state), max_age=STATE_LIFETIME_SECONDS,
                        secure=True, httponly=True, samesite='None', path='/auth/saml')


def _clear_state_cookie(response):
    response.delete_cookie(STATE_COOKIE, path='/auth/saml', secure=True, httponly=True, samesite='None')
    return response


def _message_page(title: str, message: str, *, ok: bool = False, detail: str = '',
                  facts: Optional[list] = None, claims: Optional[dict] = None, status: int = 200):
    """A self-contained page for an attempt that signed nobody in.

    Rendered straight from the template, without the app's template context
    (which looks up tier and feature settings for the app shell), and with the
    session left exactly as the browser has it.
    """
    template = current_app.jinja_env.get_template('auth/saml_message.html')
    html = template.render(title=title, message=message, ok=ok, detail=detail, facts=facts or [],
                           claims=claims or {}, sign_in_url=url_for('login'))
    _leave_session_untouched()
    return _clear_state_cookie(Response(html, status=status, mimetype='text/html'))


def _leave_session_untouched():
    """Stop this response from carrying a session cookie.

    The identity provider's POST arrives without the browser's AI Hub session
    cookie, so the request starts on a brand-new empty session. The session
    backend stamps every new session and would send its cookie back, replacing
    the cookie of whoever is already signed in on this browser: an Admin
    running a test sign-in would be signed out by the result page. An empty,
    unmodified session is the one state the backend neither saves nor sends,
    and it leaves any stored session as it is.
    """
    try:
        from flask import session
        session.clear()
        session.modified = False
    except Exception as e:  # never let this turn a result page into an error
        logger.debug(f"Could not neutralise the session for a SAML result page: {e}")


def create_saml_blueprint(db, user_class, bcrypt_instance) -> Blueprint:
    bp = Blueprint('saml', __name__, url_prefix='/auth/saml')

    @bp.app_context_processor
    def _inject_sign_in_options():
        def saml_sign_in_options():
            """Buttons for the sign-in page: one per switched-on SAML provider.
            Called from login.html only, so no other page pays for the lookup."""
            try:
                target = request.args.get('next') or None
                return [{'name': p.get('provider_name') or 'single sign-on',
                         'url': url_for('saml.login', provider=p['id'], next=target)}
                        for p in _enabled_saml_providers()]
            except Exception as e:
                logger.error(f"SAML sign-in options unavailable: {e}")
                return []
        return {'saml_sign_in_options': saml_sign_in_options}

    @bp.route('/login')
    def login():
        test_mode = request.args.get('test') == '1'
        provider_id = request.args.get('provider', type=int)

        if test_mode:
            # A test may run against a provider that is not switched on yet,
            # so it is for signed-in Admins only.
            if not current_user.is_authenticated or int(getattr(current_user, 'role', 0) or 0) < 3:
                flash('Sign in as an Admin to run a SAML test sign-in.', 'warning')
                return redirect(url_for('login'))
            provider = _load_provider(provider_id) if provider_id else None
            if provider is not None and provider.get('provider_type') != 'saml':
                provider = None
        else:
            enabled = _enabled_saml_providers()
            provider = next((p for p in enabled if not provider_id or p['id'] == provider_id), None)

        if provider is None:
            flash('Single sign-on is not set up on this AI Hub.', 'warning')
            return redirect(url_for('login'))

        config = saml_provider_config(provider)
        try:
            if not saml.SIGNXML_AVAILABLE:
                raise SamlError('', f'the XML signature library is missing ({saml.SIGNXML_IMPORT_ERROR})')
            missing = [label for key, label in (('idp_entity_id', 'identity provider identifier'),
                                                ('idp_sso_url', 'login URL'),
                                                ('idp_certificates', 'signing certificate'))
                       if not config.get(key)]
            if missing:
                raise SamlError('', 'missing ' + ', '.join(missing))
            sp = sp_addresses(config)
            request_id, request_xml = saml.build_authn_request(sp['entity_id'], sp['acs_url'],
                                                               config['idp_sso_url'])
        except SamlError as e:
            logger.error(f"SAML sign-in cannot start for provider {provider.get('id')}: {e.detail}")
            flash('Single sign-on is not fully set up. Ask your administrator to check the '
                  'Identity & Security screen.', 'danger')
            return redirect(url_for('login'))

        nonce = secrets.token_urlsafe(24)
        state = {'r': request_id, 's': nonce, 'p': provider['id'],
                 'n': _safe_next(request.args.get('next')), 't': 1 if test_mode else 0}
        response = redirect(saml.build_redirect_url(config['idp_sso_url'], request_xml, nonce))
        _set_state_cookie(response, state)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.route('/acs', methods=['POST'])
    def acs():
        raw = request.form.get('SAMLResponse', '')
        relay = request.form.get('RelayState', '')

        # State counts only when the cookie and RelayState agree. A leftover
        # cookie from an abandoned attempt must not block a sign-in that was
        # started at the identity provider, so a mismatch means "no state".
        state = _read_state()
        if state and not (relay and hmac.compare_digest(str(relay), str(state['s']))):
            state = None
        test_mode = bool(state and state.get('t'))

        provider = None
        try:
            if not raw:
                raise SamlError('The sign-in response could not be read.', 'No SAMLResponse was posted.')

            if state:
                provider = _load_provider(state['p'])
                if provider is not None and not test_mode and not provider.get('is_enabled'):
                    provider = None
            else:
                issuer = saml.peek_issuer(raw)
                provider = next((p for p in _enabled_saml_providers()
                                 if (p.get('config') or {}).get('idp_entity_id', '').strip() == issuer), None)
            if provider is None or provider.get('provider_type') != 'saml':
                raise SamlError('Single sign-on is not set up for your identity provider.',
                                'No switched-on SAML provider matches this response.')

            config = saml_provider_config(provider)
            sp = sp_addresses(config)
            try:
                skew = int(config.get('clock_skew_seconds', saml.DEFAULT_CLOCK_SKEW_SECONDS))
            except (TypeError, ValueError):
                skew = saml.DEFAULT_CLOCK_SKEW_SECONDS
            identity = saml.validate_response(
                raw,
                idp_entity_id=config.get('idp_entity_id', ''),
                certificates=config['idp_certificates'],
                sp_entity_id=sp['entity_id'],
                acs_url=sp['acs_url'],
                expected_request_id=state['r'] if state else None,
                allow_unsolicited=bool(config.get('allow_idp_initiated')),
                attribute_names={k: config.get(k) for k in saml.DEFAULT_ATTRIBUTES},
                clock_skew_seconds=skew,
            )
            if not saml.remember_assertion(identity.assertion_id, identity.expires_at + skew):
                raise SamlError('This sign-in has already been used. Please sign in again.',
                                f'Assertion {identity.assertion_id} was presented a second time.')
        except SamlError as e:
            logger.warning(f"SAML sign-in refused (provider {provider.get('id') if provider else 'unknown'}): "
                           f"{e.detail}")
            return _message_page('Sign-in did not complete', e.public or 'The sign-in could not be completed.',
                                 detail=e.detail if test_mode else '', status=400)
        except Exception as e:
            logger.error(f"SAML sign-in failed unexpectedly: {type(e).__name__}: {e}")
            return _message_page('Sign-in did not complete', 'The sign-in could not be completed.',
                                 detail=f'{type(e).__name__}: {e}' if test_mode else '', status=500)

        usernames = saml.candidate_usernames(identity.username, config.get('username_domain', ''))
        mapping = provider.get('group_role_mapping') or {}
        if identity.groups_incomplete:
            logger.warning(f"SAML sign-in for '{usernames[0]}': the identity provider left the group list out "
                           f"because the user is in too many groups. Set it to send only the groups assigned "
                           f"to the application.")

        from auth.user_provisioner import UserProvisioner
        provisioner = UserProvisioner(db, user_class, bcrypt_instance)
        common = dict(
            auth_provider='saml',
            external_id=identity.external_id,
            username=usernames[0],
            email=identity.email,
            groups=identity.groups,
            auto_provision=provider.get('auto_provision', True),
            default_role=provider.get('default_role', 1),
            group_role_mapping=mapping,
            alt_usernames=usernames[1:],
            match_email=True,
        )

        if test_mode:
            try:
                outcome = provisioner.preview(**common)
            except Exception as e:
                logger.error(f"SAML test sign-in: preview failed: {e}")
                outcome = {'outcome': 'refuse', 'username': usernames[0], 'role': None,
                           'reason': f'Could not look the user up: {e}'}
            role_names = {1: 'User', 2: 'Developer', 3: 'Admin'}
            verdict = {'existing': 'Would sign in as existing user', 'link': 'Would sign in as existing user',
                       'create': 'Would create new user', 'refuse': 'Would be refused'}[outcome['outcome']]
            facts = [
                ('Result', f"{verdict}: {outcome['username']}" if outcome['outcome'] != 'refuse' else verdict),
                ('Why', outcome['reason']),
                ('Role', role_names.get(outcome.get('role'), '') if outcome.get('role') else ''),
                ('Sign-in name received', identity.username),
                ('AI Hub user name', usernames[0]),
                ('Email', identity.email or '(none received)'),
                ('Display name', identity.display_name),
                ('Unique ID', identity.external_id),
                ('Groups received', (f'{len(identity.groups)}: ' + ', '.join(identity.groups))
                 if identity.groups else ('(left out by the identity provider: too many groups)'
                                          if identity.groups_incomplete else '(none received)')),
            ]
            return _message_page('Test sign-in passed', 'The identity provider\'s answer was verified. '
                                 'Nobody was signed in and nothing was changed.', ok=True,
                                 facts=[f for f in facts if f[1]], claims=identity.attributes)

        user = provisioner.find_or_create_user(name=identity.display_name, relink_external=True, **common)
        if user is None:
            logger.warning(f"SAML sign-in verified for '{usernames[0]}' but no AI Hub user was matched or created")
            return _message_page('You do not have access to AI Hub',
                                 'Your sign-in was verified, but your account is not set up for AI Hub. '
                                 'Ask your AI Hub administrator for access.', status=403)

        login_user(user)
        logger.info(f"SAML authentication successful for user: {user.username}")
        target = (state or {}).get('n') or url_for('home')
        return _clear_state_cookie(redirect(target, code=303))

    @bp.route('/metadata')
    def metadata():
        provider_id = request.args.get('provider', type=int)
        config = {}
        try:
            provider = (_load_provider(provider_id) if provider_id
                        else next(iter(_enabled_saml_providers()), None))
            if provider is not None and provider.get('provider_type') == 'saml':
                config = saml_provider_config(provider)
        except Exception as e:
            logger.error(f"SAML metadata: provider lookup failed, using defaults: {e}")
        try:
            sp = sp_addresses(config)
            return Response(saml.build_sp_metadata(sp['entity_id'], sp['acs_url']),
                            mimetype='application/samlmetadata+xml')
        except SamlError as e:
            return Response(e.detail, status=503, mimetype='text/plain')

    return bp
