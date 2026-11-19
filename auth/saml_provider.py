"""
SAML 2.0 sign-in (service provider side)

Everything here is plain logic with no web framework in it: building the
sign-in request, checking the identity provider's signed answer, and turning
it into the identity AI Hub provisions a user from. The routes that drive it
live in auth/saml_routes.py.

Written against Microsoft Entra ID's SAML output (the claim names below are
Entra's defaults), but nothing in the checks is Entra-specific.

The one rule that matters most: user data is only ever read from the bytes
the signature covers. `_trusted_assertion` returns the assertion re-parsed
from the verified, canonicalised data, never an element of the document as it
arrived. That is what defeats signature-wrapping and comment-splitting tricks.
"""

import base64
import binascii
import datetime as _dt
import logging
import re
import secrets
import threading
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urlsplit

logger = logging.getLogger(__name__)

try:
    from lxml import etree
    from signxml import SignatureConfiguration, XMLVerifier
    SIGNXML_AVAILABLE = True
    SIGNXML_IMPORT_ERROR = ''
except Exception as _import_err:  # pragma: no cover - depends on the build
    etree = None
    SIGNXML_AVAILABLE = False
    SIGNXML_IMPORT_ERROR = str(_import_err)

NS_SAML = 'urn:oasis:names:tc:SAML:2.0:assertion'
NS_SAMLP = 'urn:oasis:names:tc:SAML:2.0:protocol'
NS_MD = 'urn:oasis:names:tc:SAML:2.0:metadata'
NS_DS = 'http://www.w3.org/2000/09/xmldsig#'
_NS = {'saml': NS_SAML, 'samlp': NS_SAMLP, 'md': NS_MD, 'ds': NS_DS}

BINDING_POST = 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST'
BINDING_REDIRECT = 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect'
STATUS_SUCCESS = 'urn:oasis:names:tc:SAML:2.0:status:Success'
METHOD_BEARER = 'urn:oasis:names:tc:SAML:2.0:cm:bearer'

# Claim names Microsoft Entra ID sends unless the administrator changes them.
DEFAULT_ATTRIBUTES = {
    'attr_username': 'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name',
    'attr_email': 'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress',
    'attr_display_name': 'http://schemas.microsoft.com/identity/claims/displayname',
    'attr_groups': 'http://schemas.microsoft.com/ws/2008/06/identity/claims/groups',
    'attr_external_id': 'http://schemas.microsoft.com/identity/claims/objectidentifier',
}
# Entra leaves the group list out, and sends this instead, for a user in more
# groups than fit in the response.
GROUPS_OVERAGE_CLAIM = 'http://schemas.microsoft.com/claims/groups.link'

# Tolerance for clocks that disagree between the identity provider and this
# server. A provider's setting (clock_skew_seconds) overrides it.
DEFAULT_CLOCK_SKEW_SECONDS = 180


class SamlError(Exception):
    """A SAML sign-in that must be refused.

    `public` is safe to show the person signing in; `detail` is the precise
    reason, for the log and for an administrator running a test sign-in.
    """

    def __init__(self, public: str, detail: str = ''):
        super().__init__(detail or public)
        self.public = public
        self.detail = detail or public


@dataclass
class SamlIdentity:
    """Who the identity provider says signed in, read from verified data only."""
    external_id: str
    name_id: str
    username: str
    email: Optional[str]
    display_name: str
    groups: List[str]
    groups_incomplete: bool
    attributes: Dict[str, List[str]]
    assertion_id: str
    expires_at: float
    in_response_to: Optional[str]
    issuer: str = ''
    extra: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _require_library():
    if not SIGNXML_AVAILABLE:
        raise SamlError(
            'SAML sign-in is not available on this install.',
            f'The XML signature library could not be loaded: {SIGNXML_IMPORT_ERROR}')


def _parse_xml(data: bytes):
    """Parse untrusted XML with everything dangerous switched off."""
    if b'<!DOCTYPE' in data or b'<!ENTITY' in data:
        raise SamlError('The sign-in response could not be read.',
                        'The response contains a DOCTYPE or ENTITY declaration, which is never accepted.')
    try:
        return etree.fromstring(data, _xml_parser())
    except etree.XMLSyntaxError as e:
        raise SamlError('The sign-in response could not be read.', f'The response is not well-formed XML: {e}')


def _xml_parser():
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                           dtd_validation=False, huge_tree=False)


_TIME_RE = re.compile(r'^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)$')


def _parse_time(value: Optional[str], what: str) -> float:
    """SAML timestamp to epoch seconds. Any number of fractional digits is accepted."""
    m = _TIME_RE.match((value or '').strip())
    if not m:
        raise SamlError('The sign-in response could not be read.', f'{what} is not a valid time: {value!r}')
    year, month, day, hour, minute, second, frac, zone = m.groups()
    micro = int((frac or '0')[:6].ljust(6, '0'))
    if zone == 'Z':
        tz = _dt.timezone.utc
    else:
        sign = 1 if zone[0] == '+' else -1
        tz = _dt.timezone(sign * _dt.timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6])))
    try:
        return _dt.datetime(int(year), int(month), int(day), int(hour), int(minute), int(second),
                            micro, tzinfo=tz).timestamp()
    except ValueError as e:
        raise SamlError('The sign-in response could not be read.', f'{what} is not a valid time: {value!r} ({e})')


def _format_time(epoch: float) -> str:
    return _dt.datetime.fromtimestamp(epoch, _dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def same_url(a: str, b: str) -> bool:
    """True when two addresses are the same place: scheme and host compared
    without regard to case, default ports dropped, path and query exact."""
    def norm(u):
        p = urlsplit((u or '').strip())
        scheme = p.scheme.lower()
        host = (p.hostname or '').lower()
        port = p.port
        if (scheme, port) in (('https', 443), ('http', 80)):
            port = None
        return scheme, host, port, p.path or '/', p.query
    return bool(a) and bool(b) and norm(a) == norm(b)


def _text(el) -> str:
    return (''.join(el.itertext()) if el is not None else '').strip()


# ---------------------------------------------------------------------------
# Certificates and identity-provider metadata
# ---------------------------------------------------------------------------

_PEM_RE = re.compile(r'-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----', re.S)


def normalize_certificates(value) -> List[str]:
    """Signing certificates as a list of PEM strings.

    Accepts what administrators actually paste: one or more PEM blocks, or the
    bare Base64 body with no BEGIN/END lines (as it appears in metadata), or a
    list of either. Raises SamlError naming the certificate that will not parse.
    """
    from cryptography import x509

    if not value:
        return []
    chunks: List[str] = []
    for item in (value if isinstance(value, (list, tuple)) else [value]):
        item = str(item or '').strip()
        if not item:
            continue
        bodies = _PEM_RE.findall(item)
        chunks.extend(bodies if bodies else [item])
    pems: List[str] = []
    for index, body in enumerate(chunks, start=1):
        b64 = re.sub(r'\s+', '', body)
        try:
            der = base64.b64decode(b64, validate=True)
            x509.load_der_x509_certificate(der)
        except (binascii.Error, ValueError) as e:
            raise SamlError('The signing certificate could not be read.',
                            f'Signing certificate {index} is not a valid X.509 certificate: {e}')
        wrapped = '\n'.join(b64[i:i + 64] for i in range(0, len(b64), 64))
        pem = f'-----BEGIN CERTIFICATE-----\n{wrapped}\n-----END CERTIFICATE-----\n'
        if pem not in pems:
            pems.append(pem)
    return pems


def describe_certificates(pems: List[str]) -> List[Dict[str, Any]]:
    """Subject and expiry of each certificate, for the admin screen."""
    from cryptography import x509

    out = []
    now = _dt.datetime.now(_dt.timezone.utc)
    for pem in pems:
        cert = x509.load_pem_x509_certificate(pem.encode())
        expires = cert.not_valid_after_utc
        out.append({
            'subject': cert.subject.rfc4514_string(),
            'expires': expires.strftime('%Y-%m-%d'),
            'expired': expires < now,
        })
    return out


def parse_idp_metadata(xml: bytes) -> Dict[str, Any]:
    """Pull the three things AI Hub needs out of an identity provider's
    federation metadata: its identifier, its sign-in address and its signing
    certificates."""
    _require_library()
    root = _parse_xml(xml if isinstance(xml, bytes) else str(xml).encode('utf-8'))
    if root.tag == f'{{{NS_MD}}}EntitiesDescriptor':
        entity = root.find('md:EntityDescriptor', _NS)
    else:
        entity = root
    if entity is None or entity.tag != f'{{{NS_MD}}}EntityDescriptor':
        raise SamlError('That is not SAML federation metadata.',
                        f'Expected an EntityDescriptor document, found <{root.tag}>.')
    idp = entity.find('md:IDPSSODescriptor', _NS)
    if idp is None:
        raise SamlError('That metadata does not describe an identity provider.',
                        'The metadata has no IDPSSODescriptor element.')
    sso_url = ''
    for binding in (BINDING_REDIRECT, BINDING_POST):
        for svc in idp.findall('md:SingleSignOnService', _NS):
            if svc.get('Binding') == binding and svc.get('Location'):
                sso_url = svc.get('Location')
                break
        if sso_url:
            break
    certs = []
    for key in idp.findall('md:KeyDescriptor', _NS):
        if key.get('use') not in (None, 'signing'):
            continue
        for c in key.findall('.//ds:X509Certificate', _NS):
            if _text(c):
                certs.append(_text(c))
    return {
        'idp_entity_id': (entity.get('entityID') or '').strip(),
        'idp_sso_url': sso_url,
        'idp_certificates': normalize_certificates(certs),
    }


# ---------------------------------------------------------------------------
# Outgoing: sign-in request and our own metadata
# ---------------------------------------------------------------------------

def build_authn_request(sp_entity_id: str, acs_url: str, idp_sso_url: str, now: Optional[float] = None):
    """Return (request_id, request_xml). The request is not signed; Entra ID
    does not ask for that, and the answer is tied to this request by its id."""
    _require_library()
    request_id = '_' + secrets.token_hex(20)
    root = etree.Element(f'{{{NS_SAMLP}}}AuthnRequest', nsmap={'samlp': NS_SAMLP, 'saml': NS_SAML})
    root.set('ID', request_id)
    root.set('Version', '2.0')
    root.set('IssueInstant', _format_time(time.time() if now is None else now))
    root.set('Destination', idp_sso_url)
    root.set('ProtocolBinding', BINDING_POST)
    root.set('AssertionConsumerServiceURL', acs_url)
    issuer = etree.SubElement(root, f'{{{NS_SAML}}}Issuer')
    issuer.text = sp_entity_id
    return request_id, etree.tostring(root)


def build_redirect_url(idp_sso_url: str, request_xml: bytes, relay_state: str = '') -> str:
    """The identity provider's address with the request attached (HTTP-Redirect binding)."""
    deflated = zlib.compress(request_xml)[2:-4]  # raw DEFLATE: no zlib header or checksum
    params = {'SAMLRequest': base64.b64encode(deflated).decode('ascii')}
    if relay_state:
        params['RelayState'] = relay_state
    return idp_sso_url + ('&' if '?' in idp_sso_url else '?') + urlencode(params)


def build_sp_metadata(sp_entity_id: str, acs_url: str) -> bytes:
    """AI Hub's own metadata, for identity providers that take a metadata file."""
    _require_library()
    root = etree.Element(f'{{{NS_MD}}}EntityDescriptor', nsmap={'md': NS_MD})
    root.set('entityID', sp_entity_id)
    sp = etree.SubElement(root, f'{{{NS_MD}}}SPSSODescriptor')
    sp.set('protocolSupportEnumeration', NS_SAMLP)
    sp.set('AuthnRequestsSigned', 'false')
    sp.set('WantAssertionsSigned', 'true')
    acs = etree.SubElement(sp, f'{{{NS_MD}}}AssertionConsumerService')
    acs.set('Binding', BINDING_POST)
    acs.set('Location', acs_url)
    acs.set('index', '0')
    acs.set('isDefault', 'true')
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8')


# ---------------------------------------------------------------------------
# Incoming: the identity provider's answer
# ---------------------------------------------------------------------------

def _decode_response(saml_response_b64: str) -> bytes:
    try:
        data = base64.b64decode(re.sub(r'\s+', '', saml_response_b64 or ''), validate=True)
    except (binascii.Error, ValueError) as e:
        raise SamlError('The sign-in response could not be read.', f'SAMLResponse is not valid Base64: {e}')
    if not data:
        raise SamlError('The sign-in response could not be read.', 'SAMLResponse is empty.')
    return data


def peek_issuer(saml_response_b64: str) -> str:
    """The issuer a response CLAIMS, unverified. Only for choosing which
    provider's certificate to check it against; validate_response then
    confirms the issuer from signed data."""
    _require_library()
    root = _parse_xml(_decode_response(saml_response_b64))
    for path in ('saml:Issuer', 'saml:Assertion/saml:Issuer'):
        el = root.find(path, _NS)
        if el is not None and _text(el):
            return _text(el)
    return ''


def _trusted_assertion(data: bytes, certificates: List[str], now: float):
    """Return (assertion, response_was_signed), both from signature-verified bytes.

    Accepts a signed Response that contains the assertion, or a signed
    Assertion inside an unsigned Response. Entra ID sends the second by default.
    `now` is the moment the signing certificate must be valid at.
    """
    from signxml.exceptions import InvalidCertificate, InvalidDigest, InvalidInput, InvalidSignature

    if not certificates:
        raise SamlError('SAML sign-in is not fully set up.',
                        'No signing certificate is saved for this identity provider.')
    places = (('./', 'response'), (f'./{{{NS_SAML}}}Assertion/', 'assertion'))
    reasons: List[str] = []
    saw_signature = False
    at = _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
    for location, label in places:
        for index, pem in enumerate(certificates, start=1):
            try:
                result = XMLVerifier().verify(
                    data, x509_cert=pem, parser=_xml_parser(),
                    expect_config=SignatureConfiguration(location=location, verification_time=at))
            except InvalidInput as e:
                if 'Expected to find XML element Signature' in str(e):
                    break  # nothing signed at this place; other certificates cannot change that
                saw_signature = True
                reasons.append(f'{label} signature, certificate {index}: {e}')
                continue
            except (InvalidSignature, InvalidDigest, InvalidCertificate) as e:
                saw_signature = True
                reasons.append(f'{label} signature, certificate {index}: {e}')
                continue
            except Exception as e:  # a malformed signature must refuse the sign-in, never crash it
                saw_signature = True
                reasons.append(f'{label} signature, certificate {index}: {type(e).__name__}: {e}')
                continue
            saw_signature = True
            signed = result.signed_xml
            if signed is None:
                reasons.append(f'{label} signature: the signed data is not XML')
                continue
            if signed.tag == f'{{{NS_SAML}}}Assertion':
                return signed, False
            if signed.tag == f'{{{NS_SAMLP}}}Response':
                found = signed.findall('saml:Assertion', _NS)
                if len(found) == 1:
                    return found[0], True
                reasons.append(f'{label} signature: the signed response holds {len(found)} assertions, expected 1')
                continue
            reasons.append(f'{label} signature covers <{signed.tag}>, not the response or its assertion')
    if not saw_signature:
        raise SamlError('The sign-in response could not be verified.',
                        'The response is not signed. In the identity provider, set the signing option to '
                        'sign the SAML assertion (or the response).')
    raise SamlError('The sign-in response could not be verified.',
                    'No saved signing certificate verifies this response. If the identity provider '
                    'renewed its certificate, load its metadata again. Details: ' + ' | '.join(reasons))


def validate_response(saml_response_b64: str, *, idp_entity_id: str, certificates: List[str],
                      sp_entity_id: str, acs_url: str, expected_request_id: Optional[str],
                      allow_unsolicited: bool = False, attribute_names: Optional[Dict[str, str]] = None,
                      clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS,
                      now: Optional[float] = None) -> SamlIdentity:
    """Check an identity provider's answer and return who signed in.

    `expected_request_id` is the id of the request this browser started with,
    or None when there is none (a sign-in started at the identity provider,
    accepted only with `allow_unsolicited`). Raises SamlError for anything
    that does not hold.
    """
    _require_library()
    now = time.time() if now is None else now
    skew = max(0, int(clock_skew_seconds))
    names = dict(DEFAULT_ATTRIBUTES)
    names.update({k: v for k, v in (attribute_names or {}).items() if v})

    data = _decode_response(saml_response_b64)
    root = _parse_xml(data)
    if root.tag != f'{{{NS_SAMLP}}}Response':
        raise SamlError('The sign-in response could not be read.', f'Expected a SAML Response, found <{root.tag}>.')

    status = root.find('samlp:Status/samlp:StatusCode', _NS)
    status_value = status.get('Value') if status is not None else ''
    if status_value != STATUS_SUCCESS:
        message = _text(root.find('samlp:Status/samlp:StatusMessage', _NS))
        inner = root.find('samlp:Status/samlp:StatusCode/samlp:StatusCode', _NS)
        raise SamlError('Your identity provider did not complete the sign-in.',
                        f'Status {status_value or "(missing)"}'
                        + (f' / {inner.get("Value")}' if inner is not None else '')
                        + (f': {message}' if message else ''))

    if root.find('saml:Assertion', _NS) is None and root.find('saml:EncryptedAssertion', _NS) is not None:
        raise SamlError('The sign-in response could not be read.',
                        'The assertion is encrypted, which AI Hub does not support. Turn off token '
                        'encryption for this application in the identity provider.')

    assertion, response_signed = _trusted_assertion(data, certificates, now)

    # --- everything below reads the verified copy only ---
    issuer = _text(assertion.find('saml:Issuer', _NS))
    if not issuer or issuer != (idp_entity_id or '').strip():
        raise SamlError('The sign-in response came from an unexpected identity provider.',
                        f'Issuer is {issuer!r}; this provider is set up for {idp_entity_id!r}.')

    assertion_id = assertion.get('ID') or ''
    if not assertion_id:
        raise SamlError('The sign-in response could not be read.', 'The assertion has no ID.')

    # Destination and InResponseTo on the Response are outside the signature
    # when only the assertion is signed, so they are a consistency check here;
    # the binding checks that count are on the signed SubjectConfirmationData.
    destination = root.get('Destination')
    if destination and not same_url(destination, acs_url):
        raise SamlError('The sign-in response was sent to the wrong address.',
                        f'Destination is {destination!r}; AI Hub expects {acs_url!r}. The Reply URL in the '
                        'identity provider must match exactly.')

    name_id = _text(assertion.find('saml:Subject/saml:NameID', _NS))
    confirmed = False
    in_response_to = None
    confirm_expiry = None
    confirm_reasons: List[str] = []
    for sc in assertion.findall('saml:Subject/saml:SubjectConfirmation', _NS):
        if sc.get('Method') != METHOD_BEARER:
            continue
        scd = sc.find('saml:SubjectConfirmationData', _NS)
        if scd is None:
            confirm_reasons.append('bearer confirmation has no SubjectConfirmationData')
            continue
        recipient = scd.get('Recipient')
        if not recipient or not same_url(recipient, acs_url):
            confirm_reasons.append(f'Recipient is {recipient!r}, AI Hub expects {acs_url!r}')
            continue
        not_after = _parse_time(scd.get('NotOnOrAfter'), 'SubjectConfirmationData NotOnOrAfter')
        if now - skew >= not_after:
            confirm_reasons.append('the confirmation has expired')
            continue
        irt = scd.get('InResponseTo')
        if expected_request_id:
            if irt != expected_request_id:
                confirm_reasons.append('the response answers a different sign-in request')
                continue
        else:
            if irt:
                confirm_reasons.append('the response answers a sign-in request this browser did not start')
                continue
            if not allow_unsolicited:
                confirm_reasons.append('sign-in started at the identity provider is switched off for this provider')
                continue
        confirmed, in_response_to, confirm_expiry = True, irt, not_after
        break
    if not confirmed:
        if not expected_request_id and any('did not start' in r for r in confirm_reasons):
            raise SamlError('Your sign-in session was not found or has expired. Start again from the AI Hub '
                            'sign-in page.', 'No matching sign-in request: ' + '; '.join(confirm_reasons))
        raise SamlError('The sign-in response could not be accepted.',
                        'No usable bearer SubjectConfirmation: '
                        + ('; '.join(confirm_reasons) or 'none present'))

    conditions = assertion.find('saml:Conditions', _NS)
    if conditions is None:
        raise SamlError('The sign-in response could not be accepted.', 'The assertion has no Conditions.')
    expires_at = confirm_expiry
    if conditions.get('NotBefore'):
        if now + skew < _parse_time(conditions.get('NotBefore'), 'Conditions NotBefore'):
            raise SamlError('The sign-in response is not valid yet. Check the clock on the AI Hub server.',
                            f'Conditions NotBefore is {conditions.get("NotBefore")}, later than this '
                            f'server\'s time {_format_time(now)} (allowing {skew}s).')
    if conditions.get('NotOnOrAfter'):
        cond_after = _parse_time(conditions.get('NotOnOrAfter'), 'Conditions NotOnOrAfter')
        if now - skew >= cond_after:
            raise SamlError('The sign-in response has expired. Please sign in again.',
                            f'Conditions NotOnOrAfter is {conditions.get("NotOnOrAfter")}; this server\'s '
                            f'time is {_format_time(now)} (allowing {skew}s).')
        expires_at = min(expires_at, cond_after)
    restrictions = conditions.findall('saml:AudienceRestriction', _NS)
    if not restrictions:
        raise SamlError('The sign-in response could not be accepted.',
                        'The assertion has no AudienceRestriction.')
    wanted = (sp_entity_id or '').strip()
    for restriction in restrictions:
        audiences = [_text(a) for a in restriction.findall('saml:Audience', _NS)]
        if wanted not in audiences:
            raise SamlError('The sign-in response was issued for a different application.',
                            f'Audience is {audiences}; AI Hub\'s Identifier (Entity ID) is {wanted!r}. They '
                            'must match exactly.')

    attributes: Dict[str, List[str]] = {}
    for attr in assertion.findall('saml:AttributeStatement/saml:Attribute', _NS):
        name = attr.get('Name')
        if not name:
            continue
        values = [_text(v) for v in attr.findall('saml:AttributeValue', _NS)]
        attributes.setdefault(name, []).extend(v for v in values if v)

    def first(key):
        return (attributes.get(names[key]) or [''])[0].strip()

    username = first('attr_username') or name_id
    if not username:
        raise SamlError('The sign-in response did not say who signed in.',
                        f'Neither the claim {names["attr_username"]!r} nor a NameID is present.')
    groups = list(attributes.get(names['attr_groups']) or [])
    groups_incomplete = not groups and GROUPS_OVERAGE_CLAIM in attributes

    return SamlIdentity(
        external_id=first('attr_external_id') or name_id or username,
        name_id=name_id,
        username=username,
        email=first('attr_email') or None,
        display_name=first('attr_display_name') or username,
        groups=groups,
        groups_incomplete=groups_incomplete,
        attributes=attributes,
        assertion_id=assertion_id,
        expires_at=expires_at,
        in_response_to=in_response_to,
        issuer=issuer,
        extra={'response_signed': response_signed},
    )


# ---------------------------------------------------------------------------
# User names
# ---------------------------------------------------------------------------

def candidate_usernames(raw: str, username_domain: str = '') -> List[str]:
    """AI Hub user names a sign-in name may correspond to, preferred first.

    With a user name domain set, 'jsmith@example.com' is known in AI Hub as
    'jsmith' (the same short name directory sign-in uses), and only for that
    domain: a sign-in name from any other domain keeps its full form, so two
    people who share a short name in different domains never collide.
    """
    raw = (raw or '').strip()
    out: List[str] = []
    domain = (username_domain or '').strip().lstrip('@').lower()
    if domain and raw.lower().endswith('@' + domain) and len(raw) > len(domain) + 1:
        out.append(raw[:-(len(domain) + 1)])
    if raw and raw not in out:
        out.append(raw)
    return out


# ---------------------------------------------------------------------------
# One-time use
# ---------------------------------------------------------------------------

_seen_assertions: Dict[str, float] = {}
_seen_lock = threading.Lock()


def remember_assertion(assertion_id: str, keep_until: float, now: Optional[float] = None) -> bool:
    """Record an accepted assertion. False if it was already used.

    `keep_until` is the last moment the assertion could still be accepted (its
    expiry plus the clock tolerance in force). Remembering it until then is
    enough, so entries past that moment are dropped on the way in. Held in
    memory: the main app is one process, and a restart also ends every
    sign-in that was in flight.
    """
    now = time.time() if now is None else now
    with _seen_lock:
        for key in [k for k, until in _seen_assertions.items() if until < now]:
            del _seen_assertions[key]
        if assertion_id in _seen_assertions:
            return False
        _seen_assertions[assertion_id] = keep_until
        return True
