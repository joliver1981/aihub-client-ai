"""SAML sign-in (2026-11-19): auth/saml_provider.py, auth/saml_routes.py and the
user-matching additions in auth/user_provisioner.py.

THE CONTRACT
  * A response is accepted only when a saved signing certificate verifies it,
    and user data is read only from the bytes that signature covers. Forged
    assertions wrapped around a genuine one, a comment hidden inside a name,
    a changed claim, another signer, an unsigned response: all refused or
    harmless.
  * The verified assertion must also be from the configured issuer, for this
    AI Hub (audience), delivered to this AI Hub (recipient), inside its time
    window, an answer to the request this browser started, and used once.
  * One sign-in attempt is held together by a signed, HTTPS-only, cross-site
    cookie plus RelayState. Without both, a response that answers a request
    is refused; a sign-in started at the identity provider is accepted only
    when the provider allows it.
  * A result page that signs nobody in (failure, or an Admin's test sign-in)
    sends no session cookie, so it cannot sign out whoever is already there.
  * Matching: already linked > user name (short name first, then the full
    sign-in name) > email. A matched user is re-linked to SAML whatever it
    was linked to before and keeps its role. An email shared by two users
    matches nobody. Directory (LDAP) sign-in behaves exactly as before, plus:
    a user since re-linked to SAML can still sign in with their directory
    password and stays linked to SAML.

Responses are signed in-test with a throwaway key. Two fixtures were signed
by .NET's own XML signature classes (an independent implementation, the same
family Microsoft's stack uses) and prove interoperability; only their public
certificate is stored. Force-add to git (gitignore hides test*.py).
"""
import ast
import base64
import datetime as dt
import importlib
import re
import sys
import time
import types
import zlib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip('signxml')

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import XMLSigner, methods

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from auth import saml_provider as saml  # noqa: E402
from auth.saml_provider import SamlError  # noqa: E402

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent / 'fixtures' / 'saml'
TENANT = '00000000-1111-2222-3333-444444444444'
IDP = f'https://sts.windows.net/{TENANT}/'
SSO = f'https://login.example.test/{TENANT}/saml2'
BASE = 'https://aihub.example.test'
SP = BASE + '/auth/saml/metadata'
ACS = BASE + '/auth/saml/acs'
NOW = float(int(time.time()))
GROUPS = saml.DEFAULT_ATTRIBUTES['attr_groups']
NS = {'saml': saml.NS_SAML, 'samlp': saml.NS_SAMLP}


# ---------------------------------------------------------------------------
# Throwaway signing material and response builder
# ---------------------------------------------------------------------------

def _make_cert(name, days_from=-1, days_to=365):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    at = dt.datetime.fromtimestamp(NOW, dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(at + dt.timedelta(days=days_from)).not_valid_after(at + dt.timedelta(days=days_to))
            .sign(key, hashes.SHA256()))
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return key_pem, cert.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture(scope='module')
def signer():
    return _make_cert('Test Identity Provider')


@pytest.fixture(scope='module')
def other_signer():
    return _make_cert('Somebody Else')


def _t(offset=0):
    return dt.datetime.fromtimestamp(NOW + offset, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def build_response(signer, *, sign='assertion', issuer=IDP, audience=SP, recipient=ACS, destination=ACS,
                   in_response_to='_req1', name_id='jsmith@example.com', upn='jsmith@example.com',
                   email='jordan.smith@example.com', groups=('g-1', 'g-2'), not_before=-300, not_after=3600,
                   confirm_not_after=3600, assertion_id='_assertion-1', status=saml.STATUS_SUCCESS,
                   extra_attrs='', raw=False):
    """An Entra-shaped response (default namespace on the assertion, as Entra writes it)."""
    key_pem, cert_pem = signer
    irt = f' InResponseTo="{in_response_to}"' if in_response_to else ''
    dest = f' Destination="{destination}"' if destination else ''
    group_values = ''.join(f'<AttributeValue>{g}</AttributeValue>' for g in groups)
    group_attr = f'<Attribute Name="{GROUPS}">{group_values}</Attribute>' if groups else ''
    upn_attr = (f'<Attribute Name="{saml.DEFAULT_ATTRIBUTES["attr_username"]}">'
                f'<AttributeValue>{upn}</AttributeValue></Attribute>') if upn else ''
    email_attr = (f'<Attribute Name="{saml.DEFAULT_ATTRIBUTES["attr_email"]}">'
                  f'<AttributeValue>{email}</AttributeValue></Attribute>') if email else ''
    xml = (
        f'<samlp:Response ID="_response-1" Version="2.0" IssueInstant="{_t()}"{dest}{irt} '
        f'xmlns:samlp="{saml.NS_SAMLP}">'
        f'<Issuer xmlns="{saml.NS_SAML}">{issuer}</Issuer>'
        f'<samlp:Status><samlp:StatusCode Value="{status}"/></samlp:Status>'
        f'<Assertion ID="{assertion_id}" IssueInstant="{_t()}" Version="2.0" xmlns="{saml.NS_SAML}">'
        f'<Issuer>{issuer}</Issuer>'
        f'<Subject><NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">{name_id}</NameID>'
        f'<SubjectConfirmation Method="{saml.METHOD_BEARER}">'
        f'<SubjectConfirmationData{irt} NotOnOrAfter="{_t(confirm_not_after)}" Recipient="{recipient}"/>'
        f'</SubjectConfirmation></Subject>'
        f'<Conditions NotBefore="{_t(not_before)}" NotOnOrAfter="{_t(not_after)}">'
        f'<AudienceRestriction><Audience>{audience}</Audience></AudienceRestriction></Conditions>'
        f'<AttributeStatement>'
        f'<Attribute Name="{saml.DEFAULT_ATTRIBUTES["attr_external_id"]}">'
        f'<AttributeValue>9f2c1c1e-aaaa-bbbb-cccc-0123456789ab</AttributeValue></Attribute>'
        f'<Attribute Name="{saml.DEFAULT_ATTRIBUTES["attr_display_name"]}">'
        f'<AttributeValue>Jordan Smith</AttributeValue></Attribute>'
        f'{group_attr}{email_attr}{upn_attr}{extra_attrs}'
        f'</AttributeStatement>'
        f'<AuthnStatement AuthnInstant="{_t()}"><AuthnContext><AuthnContextClassRef>'
        f'urn:oasis:names:tc:SAML:2.0:ac:classes:Password</AuthnContextClassRef></AuthnContext></AuthnStatement>'
        f'</Assertion></samlp:Response>'
    ).encode()
    root = etree.fromstring(xml)
    if sign:
        signer_ = XMLSigner(method=methods.enveloped, signature_algorithm='rsa-sha256', digest_algorithm='sha256',
                            c14n_algorithm='http://www.w3.org/2001/10/xml-exc-c14n#')
        if sign == 'assertion':
            assertion = root.find('saml:Assertion', NS)
            signed = signer_.sign(assertion, key=key_pem, cert=cert_pem, reference_uri='#' + assertion.get('ID'))
            root.replace(assertion, signed)
        else:
            root = signer_.sign(root, key=key_pem, cert=cert_pem, reference_uri='#' + root.get('ID'))
    data = etree.tostring(root)
    return data if raw else base64.b64encode(data).decode()


def check(response, signer, **overrides):
    args = dict(idp_entity_id=IDP, certificates=[signer[1]], sp_entity_id=SP, acs_url=ACS,
                expected_request_id='_req1', now=NOW)
    args.update(overrides)
    return saml.validate_response(response, **args)


def refused(response, signer, expect_in_detail, **overrides):
    with pytest.raises(SamlError) as err:
        check(response, signer, **overrides)
    assert expect_in_detail.lower() in err.value.detail.lower(), err.value.detail
    return err.value


# ---------------------------------------------------------------------------
# Signature and trust
# ---------------------------------------------------------------------------

class TestSignature:
    def test_signed_assertion_is_accepted(self, signer):
        identity = check(build_response(signer), signer)
        assert identity.username == 'jsmith@example.com'
        assert identity.email == 'jordan.smith@example.com'
        assert identity.display_name == 'Jordan Smith'
        assert identity.external_id == '9f2c1c1e-aaaa-bbbb-cccc-0123456789ab'
        assert identity.groups == ['g-1', 'g-2']
        assert identity.extra['response_signed'] is False

    def test_signed_response_is_accepted(self, signer):
        identity = check(build_response(signer, sign='response'), signer)
        assert identity.username == 'jsmith@example.com'
        assert identity.extra['response_signed'] is True

    def test_unsigned_response_is_refused(self, signer):
        refused(build_response(signer, sign=None), signer, 'not signed')

    def test_another_signer_is_refused(self, signer, other_signer):
        refused(build_response(other_signer), signer, 'No saved signing certificate verifies')

    def test_changed_claim_is_refused(self, signer):
        data = build_response(signer, raw=True).replace(b'<AttributeValue>g-2<', b'<AttributeValue>g-9<')
        refused(base64.b64encode(data).decode(), signer, 'No saved signing certificate verifies')

    def test_changed_name_is_refused(self, signer):
        data = build_response(signer, raw=True).replace(b'jsmith@example.com', b'admin@example.com')
        refused(base64.b64encode(data).decode(), signer, 'No saved signing certificate verifies')

    def test_second_saved_certificate_is_tried(self, signer, other_signer):
        """While the identity provider changes certificates, both are saved."""
        identity = check(build_response(signer), signer, certificates=[other_signer[1], signer[1]])
        assert identity.username == 'jsmith@example.com'

    def test_expired_signing_certificate_is_refused(self):
        old = _make_cert('Expired Signer', days_from=-400, days_to=-30)
        refused(build_response(old), old, 'No saved signing certificate verifies')

    def test_no_certificate_saved_is_refused(self, signer):
        refused(build_response(signer), signer, 'No signing certificate is saved', certificates=[])

    def test_forged_assertion_ahead_of_the_genuine_one_is_never_read(self, signer):
        """Signature wrapping: the forged assertion comes first in the document."""
        root = etree.fromstring(build_response(signer, raw=True))
        real = root.find('saml:Assertion', NS)
        forged = etree.fromstring(etree.tostring(real))
        forged.remove(forged.find('{http://www.w3.org/2000/09/xmldsig#}Signature'))
        forged.set('ID', '_forged')
        forged.find('saml:Subject/saml:NameID', NS).text = 'admin@example.com'
        for value in forged.iter(f'{{{saml.NS_SAML}}}AttributeValue'):
            if value.text == 'jsmith@example.com':
                value.text = 'admin@example.com'
        real.addprevious(forged)
        try:
            identity = check(base64.b64encode(etree.tostring(root)).decode(), signer)
        except SamlError:
            return  # refusing outright is equally safe
        assert identity.username == 'jsmith@example.com'
        assert identity.name_id == 'jsmith@example.com'

    def test_forged_assertion_carrying_a_copied_signature_is_never_read(self, signer):
        """Signature wrapping: the genuine assertion is buried, the forged one sits in its place."""
        root = etree.fromstring(build_response(signer, raw=True))
        real = root.find('saml:Assertion', NS)
        forged = etree.fromstring(etree.tostring(real))
        forged.set('ID', '_forged')
        forged.find('saml:Subject/saml:NameID', NS).text = 'admin@example.com'
        holder = etree.SubElement(root, f'{{{saml.NS_SAMLP}}}Extensions')
        root.remove(real)
        holder.append(real)
        root.insert(2, forged)
        try:
            identity = check(base64.b64encode(etree.tostring(root)).decode(), signer)
        except SamlError:
            return
        assert 'admin' not in identity.username and 'admin' not in identity.name_id

    def test_comment_inside_a_name_cannot_shorten_it(self, signer):
        """The owner of 'jsmith@example.com.evil.test' hides the tail behind a
        comment (comments are outside the signature). The whole name must come back."""
        long = 'jsmith@example.com.evil.test'
        data = build_response(signer, name_id=long, upn=long, raw=True)
        data = data.replace(b'jsmith@example.com.evil.test</NameID>', b'jsmith@example.com<!---->.evil.test</NameID>')
        data = data.replace(b'<AttributeValue>jsmith@example.com.evil.test<',
                            b'<AttributeValue>jsmith@example.com<!---->.evil.test<')
        assert data.count(b'<!---->') == 2
        identity = check(base64.b64encode(data).decode(), signer)
        assert identity.username == long
        assert identity.name_id == long

    def test_doctype_is_refused(self, signer):
        data = b'<!DOCTYPE x [<!ENTITY a "b">]>' + build_response(signer, raw=True)
        refused(base64.b64encode(data).decode(), signer, 'DOCTYPE')

    def test_not_base64_is_refused(self, signer):
        refused('this is not base64 !!!', signer, 'not valid Base64')

    def test_encrypted_assertion_is_refused_with_the_fix(self, signer):
        data = (f'<samlp:Response xmlns:samlp="{saml.NS_SAMLP}" ID="_r" Version="2.0">'
                f'<samlp:Status><samlp:StatusCode Value="{saml.STATUS_SUCCESS}"/></samlp:Status>'
                f'<EncryptedAssertion xmlns="{saml.NS_SAML}"/></samlp:Response>').encode()
        refused(base64.b64encode(data).decode(), signer, 'token encryption')


class TestDotNetSignedSamples:
    """Signed by .NET's SignedXml, not by the library under test."""

    @pytest.mark.parametrize('name,response_signed', [('dotnet_assertion_signed.xml', False),
                                                      ('dotnet_response_signed.xml', True)])
    def test_sample_verifies(self, name, response_signed):
        data = (FIXTURES / name).read_bytes()
        issued = saml._parse_time(etree.fromstring(data).get('IssueInstant'), 'IssueInstant')
        identity = saml.validate_response(
            base64.b64encode(data).decode(), idp_entity_id=IDP,
            certificates=saml.normalize_certificates((FIXTURES / 'dotnet_signing_cert.pem').read_text()),
            sp_entity_id='https://aihub.example.com/auth/saml/metadata',
            acs_url='https://aihub.example.com/auth/saml/acs',
            expected_request_id='_req-0123456789', now=issued + 60)
        assert identity.username == 'jsmith@example.com'
        assert identity.email == 'jordan.smith@example.com'
        assert len(identity.groups) == 2
        assert identity.extra['response_signed'] is response_signed

    def test_sample_with_a_changed_group_is_refused(self):
        data = (FIXTURES / 'dotnet_assertion_signed.xml').read_bytes().replace(b'000000000002', b'000000000009')
        issued = saml._parse_time(etree.fromstring(data).get('IssueInstant'), 'IssueInstant')
        with pytest.raises(SamlError):
            saml.validate_response(
                base64.b64encode(data).decode(), idp_entity_id=IDP,
                certificates=saml.normalize_certificates((FIXTURES / 'dotnet_signing_cert.pem').read_text()),
                sp_entity_id='https://aihub.example.com/auth/saml/metadata',
                acs_url='https://aihub.example.com/auth/saml/acs',
                expected_request_id='_req-0123456789', now=issued + 60)


# ---------------------------------------------------------------------------
# What a verified assertion must also say
# ---------------------------------------------------------------------------

class TestConditions:
    def test_wrong_issuer(self, signer):
        refused(build_response(signer, issuer='https://sts.windows.net/another-tenant/'), signer, 'Issuer is')

    def test_wrong_audience(self, signer):
        refused(build_response(signer, audience='https://some-other-app.example.test'), signer, 'Audience is')

    def test_wrong_recipient(self, signer):
        refused(build_response(signer, recipient='https://elsewhere.example.test/auth/saml/acs', destination=None),
                signer, 'Recipient is')

    def test_wrong_destination(self, signer):
        refused(build_response(signer, destination='https://elsewhere.example.test/auth/saml/acs'),
                signer, 'Destination is')

    def test_recipient_differs_only_by_host_case_and_default_port(self, signer):
        identity = check(build_response(signer, recipient='https://AIHUB.example.test:443/auth/saml/acs',
                                        destination=None), signer)
        assert identity.username == 'jsmith@example.com'

    def test_expired(self, signer):
        refused(build_response(signer, not_after=-600, confirm_not_after=3600), signer, 'NotOnOrAfter')

    def test_expired_confirmation(self, signer):
        refused(build_response(signer, confirm_not_after=-600), signer, 'expired')

    def test_not_valid_yet(self, signer):
        refused(build_response(signer, not_before=900), signer, 'NotBefore')

    def test_clock_tolerance_is_honoured(self, signer):
        late = build_response(signer, not_after=-60, confirm_not_after=-60)
        assert check(late, signer, clock_skew_seconds=180).username == 'jsmith@example.com'
        refused(late, signer, 'expired', clock_skew_seconds=0)

    def test_answer_to_a_different_request(self, signer):
        refused(build_response(signer, in_response_to='_someone-elses'), signer, 'different sign-in request')

    def test_answer_to_a_request_this_browser_did_not_start(self, signer):
        err = refused(build_response(signer), signer, 'did not start', expected_request_id=None,
                      allow_unsolicited=True)
        assert 'Start again' in err.public

    def test_started_at_the_identity_provider_needs_the_setting(self, signer):
        unsolicited = build_response(signer, in_response_to=None)
        refused(unsolicited, signer, 'switched off', expected_request_id=None, allow_unsolicited=False)
        identity = check(unsolicited, signer, expected_request_id=None, allow_unsolicited=True)
        assert identity.in_response_to is None

    def test_failed_status_reports_the_providers_reason(self, signer):
        refused(build_response(signer, sign=None, status='urn:oasis:names:tc:SAML:2.0:status:Requester'),
                signer, 'status:Requester')

    def test_user_name_falls_back_to_name_id(self, signer):
        identity = check(build_response(signer, upn=None, name_id='fallback@example.com'), signer)
        assert identity.username == 'fallback@example.com'

    def test_custom_claim_names(self, signer):
        extra = '<Attribute Name="shortname"><AttributeValue>jsmith</AttributeValue></Attribute>'
        identity = check(build_response(signer, extra_attrs=extra), signer,
                         attribute_names={'attr_username': 'shortname', 'attr_email': ''})
        assert identity.username == 'jsmith'
        assert identity.email == 'jordan.smith@example.com'  # empty name keeps the default

    def test_group_list_left_out_is_flagged(self, signer):
        extra = (f'<Attribute Name="{saml.GROUPS_OVERAGE_CLAIM}">'
                 f'<AttributeValue>https://graph.example.test/groups</AttributeValue></Attribute>')
        identity = check(build_response(signer, groups=(), extra_attrs=extra), signer)
        assert identity.groups == [] and identity.groups_incomplete is True

    def test_seven_digit_fractions_and_offsets_parse(self):
        assert saml._parse_time('2026-11-19T15:00:00.1234567Z', 't') == pytest.approx(
            saml._parse_time('2026-11-19T10:00:00.123456-05:00', 't'), abs=0.001)
        with pytest.raises(SamlError):
            saml._parse_time('yesterday', 't')


class TestOneTimeUse:
    def test_an_assertion_is_accepted_once(self):
        saml._seen_assertions.clear()
        assert saml.remember_assertion('_a', NOW + 60, now=NOW) is True
        assert saml.remember_assertion('_a', NOW + 60, now=NOW + 1) is False
        assert saml.remember_assertion('_b', NOW + 60, now=NOW + 1) is True

    def test_entries_are_dropped_once_they_could_no_longer_be_accepted(self):
        saml._seen_assertions.clear()
        saml.remember_assertion('_old', NOW + 60, now=NOW)
        saml.remember_assertion('_new', NOW + 500, now=NOW + 61)
        assert '_old' not in saml._seen_assertions and '_new' in saml._seen_assertions


# ---------------------------------------------------------------------------
# Helpers: names, certificates, metadata, outgoing request
# ---------------------------------------------------------------------------

class TestNamesAndAddresses:
    def test_short_name_only_for_the_configured_domain(self):
        assert saml.candidate_usernames('jsmith@example.com', 'example.com') == ['jsmith', 'jsmith@example.com']
        assert saml.candidate_usernames('JSmith@EXAMPLE.com', '@example.com') == ['JSmith', 'JSmith@EXAMPLE.com']
        assert saml.candidate_usernames('jsmith@other.test', 'example.com') == ['jsmith@other.test']
        assert saml.candidate_usernames('jsmith@notexample.com', 'example.com') == ['jsmith@notexample.com']
        assert saml.candidate_usernames('jsmith@example.com', '') == ['jsmith@example.com']
        assert saml.candidate_usernames('@example.com', 'example.com') == ['@example.com']

    def test_same_url(self):
        assert saml.same_url('https://A.example.test/x', 'https://a.example.test:443/x')
        assert not saml.same_url('https://a.example.test/x', 'https://a.example.test/X')
        assert not saml.same_url('https://a.example.test/x', 'http://a.example.test/x')
        assert not saml.same_url('https://a.example.test:8443/x', 'https://a.example.test/x')
        assert not saml.same_url('', '')


class TestCertificates:
    def test_accepts_pem_bare_base64_and_lists(self, signer, other_signer):
        pem = signer[1]
        bare = ''.join(pem.strip().splitlines()[1:-1])
        assert saml.normalize_certificates(pem) == saml.normalize_certificates(bare)
        both = saml.normalize_certificates(pem + '\n' + other_signer[1])
        assert len(both) == 2 and both == saml.normalize_certificates([pem, other_signer[1]])
        assert saml.normalize_certificates(pem + pem) == saml.normalize_certificates(pem)  # no duplicates
        assert saml.normalize_certificates('') == [] and saml.normalize_certificates(None) == []

    def test_rubbish_is_named(self):
        with pytest.raises(SamlError) as err:
            saml.normalize_certificates('-----BEGIN CERTIFICATE-----\nnot a certificate\n-----END CERTIFICATE-----')
        assert 'certificate 1' in err.value.detail

    def test_describe(self, signer):
        info = saml.describe_certificates(saml.normalize_certificates(signer[1]))
        assert info[0]['expired'] is False and re.match(r'\d{4}-\d\d-\d\d', info[0]['expires'])


class TestMetadata:
    def _metadata(self, cert_pem):
        body = ''.join(cert_pem.strip().splitlines()[1:-1])
        return (f'<EntityDescriptor xmlns="{saml.NS_MD}" entityID="{IDP}">'
                f'<RoleDescriptor xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                f'protocolSupportEnumeration="x"/>'
                f'<IDPSSODescriptor protocolSupportEnumeration="{saml.NS_SAMLP}">'
                f'<KeyDescriptor use="signing"><KeyInfo xmlns="http://www.w3.org/2000/09/xmldsig#"><X509Data>'
                f'<X509Certificate>{body}</X509Certificate></X509Data></KeyInfo></KeyDescriptor>'
                f'<SingleSignOnService Binding="{saml.BINDING_POST}" Location="{SSO}/post"/>'
                f'<SingleSignOnService Binding="{saml.BINDING_REDIRECT}" Location="{SSO}"/>'
                f'</IDPSSODescriptor></EntityDescriptor>').encode()

    def test_reads_identifier_login_url_and_certificate(self, signer):
        parsed = saml.parse_idp_metadata(self._metadata(signer[1]))
        assert parsed['idp_entity_id'] == IDP
        assert parsed['idp_sso_url'] == SSO  # the redirect address is preferred
        assert parsed['idp_certificates'] == saml.normalize_certificates(signer[1])

    def test_something_else_is_refused(self):
        with pytest.raises(SamlError):
            saml.parse_idp_metadata(b'<html><body>Sign in</body></html>')

    def test_our_own_metadata(self):
        root = etree.fromstring(saml.build_sp_metadata(SP, ACS))
        assert root.get('entityID') == SP
        acs = root.find(f'{{{saml.NS_MD}}}SPSSODescriptor/{{{saml.NS_MD}}}AssertionConsumerService')
        assert acs.get('Location') == ACS and acs.get('Binding') == saml.BINDING_POST


class TestOutgoingRequest:
    def test_request_names_us_and_where_to_answer(self):
        request_id, xml = saml.build_authn_request(SP, ACS, SSO, now=NOW)
        root = etree.fromstring(xml)
        assert root.get('ID') == request_id and request_id.startswith('_') and len(request_id) > 30
        assert root.get('AssertionConsumerServiceURL') == ACS
        assert root.get('Destination') == SSO
        assert root.find('saml:Issuer', NS).text == SP
        assert saml.build_authn_request(SP, ACS, SSO)[0] != request_id

    def test_redirect_carries_the_request_and_relay_state(self):
        _, xml = saml.build_authn_request(SP, ACS, SSO, now=NOW)
        url = saml.build_redirect_url(SSO + '?tenant=x', xml, 'state-value')
        query = parse_qs(urlsplit(url).query)
        assert query['tenant'] == ['x'] and query['RelayState'] == ['state-value']
        assert zlib.decompress(base64.b64decode(query['SAMLRequest'][0]), -15) == xml


# ---------------------------------------------------------------------------
# The routes, end to end, on a small app that uses the real session backend
# ---------------------------------------------------------------------------

class _User:
    is_authenticated = True
    is_active = True
    is_anonymous = False

    def __init__(self, uid, username, role=1):
        self.id, self.username, self.role = uid, username, role

    def get_id(self):
        return str(self.id)


class _Harness:
    def __init__(self, tmp_path, monkeypatch, signer, **provider_overrides):
        from flask import Flask
        from flask_login import LoginManager, current_user, login_user
        from flask_session import Session
        from auth import saml_routes as routes

        monkeypatch.delenv('USE_REVERSE_PROXY', raising=False)
        self.routes = routes
        self.users = {1: _User(1, 'jsmith'), 9: _User(9, 'boss', role=3)}
        config = {'idp_entity_id': IDP, 'idp_sso_url': SSO, 'idp_certificates': [signer[1]],
                  'username_domain': 'example.com'}
        config.update(provider_overrides.pop('config', {}))
        self.provider = {'id': 7, 'provider_type': 'saml', 'provider_name': 'Example Corp', 'is_enabled': True,
                         'config': config, 'auto_provision': True, 'default_role': 1, 'group_role_mapping': {}}
        self.provider.update(provider_overrides)
        self.calls = calls = []
        users = self.users

        class FakeProvisioner:
            def __init__(self, db, user_class, bcrypt_instance):
                pass

            def find_or_create_user(self, **kw):
                calls.append(('sign-in', kw))
                return next((u for u in users.values() if u.username == kw['username']), None)

            def preview(self, **kw):
                calls.append(('preview', kw))
                return {'outcome': 'create', 'username': kw['username'], 'role': 1,
                        'reason': 'A new AI Hub user would be created.'}

        import auth.user_provisioner as provisioner_module
        monkeypatch.setattr(provisioner_module, 'UserProvisioner', FakeProvisioner)
        monkeypatch.setattr(routes, '_enabled_saml_providers',
                            lambda: [self.provider] if self.provider['is_enabled'] else [])
        monkeypatch.setattr(routes, '_load_provider', lambda pid: self.provider if int(pid) == 7 else None)
        saml._seen_assertions.clear()

        app = Flask('saml_harness', template_folder=str(_ROOT / 'templates'))
        app.secret_key = 'unit-test-only'
        app.config.update(TESTING=True, SESSION_TYPE='filesystem', SESSION_FILE_DIR=str(tmp_path / 'sessions'))
        Session(app)
        manager = LoginManager(app)
        manager.user_loader(lambda uid: users.get(int(uid)))

        @app.route('/login')
        def login():
            return 'sign-in page'

        @app.route('/')
        def home():
            return 'home:' + (current_user.username if current_user.is_authenticated else 'nobody')

        @app.route('/become/<int:uid>')
        def become(uid):
            login_user(users[uid])
            return 'ok'

        app.register_blueprint(routes.create_saml_blueprint(db=None, user_class=None, bcrypt_instance=None))
        self.app = app
        self.signer = signer

    def client(self):
        return self.app.test_client()

    def start(self, client, query=''):
        """Begin a sign-in. Returns (request id, RelayState, state cookie header value)."""
        resp = client.get('/auth/saml/login' + query, base_url=BASE)
        assert resp.status_code == 302, resp.get_data(as_text=True)
        location = urlsplit(resp.headers['Location'])
        assert f'{location.scheme}://{location.netloc}{location.path}' == SSO
        query_ = parse_qs(location.query)
        root = etree.fromstring(zlib.decompress(base64.b64decode(query_['SAMLRequest'][0]), -15))
        assert root.get('AssertionConsumerServiceURL') == ACS and root.find('saml:Issuer', NS).text == SP
        cookie = next(c for c in resp.headers.getlist('Set-Cookie') if c.startswith(self.routes.STATE_COOKIE + '='))
        return root.get('ID'), query_['RelayState'][0], cookie

    def answer(self, response_b64, relay=None, cookie=None, client=None):
        """Post the identity provider's answer the way a cross-site POST arrives:
        on a browser context that sends only the cookies passed in here."""
        client = client or self.client()
        if cookie:
            name, value = cookie.split(';', 1)[0].split('=', 1)
            client.set_cookie(name, value, domain=urlsplit(BASE).hostname, path='/auth/saml')
        form = {'SAMLResponse': response_b64}
        if relay is not None:
            form['RelayState'] = relay
        return client.post('/auth/saml/acs', data=form, base_url=BASE)


def _session_cookies(resp):
    return [c for c in resp.headers.getlist('Set-Cookie') if c.startswith('session=')]


@pytest.fixture
def harness(tmp_path, monkeypatch, signer):
    return _Harness(tmp_path, monkeypatch, signer)


class TestSignInRoundTrip:
    def test_state_cookie_is_https_only_cross_site_and_scoped(self, harness):
        _, relay, cookie = harness.start(harness.client())
        lowered = cookie.lower()
        assert 'secure' in lowered and 'httponly' in lowered and 'samesite=none' in lowered
        assert 'path=/auth/saml' in lowered
        assert len(relay) <= 80  # the SAML limit for RelayState

    def test_full_round_trip_signs_the_user_in(self, harness):
        request_id, relay, cookie = harness.start(harness.client(), '?next=/data_explorer')
        browser = harness.client()
        resp = harness.answer(build_response(harness.signer, in_response_to=request_id), relay, cookie, browser)
        assert resp.status_code == 303 and resp.headers['Location'] == '/data_explorer'
        assert browser.get('/', base_url=BASE).get_data(as_text=True) == 'home:jsmith'
        kind, kw = harness.calls[-1]
        assert kind == 'sign-in' and kw['auth_provider'] == 'saml' and kw['relink_external'] is True
        assert kw['username'] == 'jsmith' and kw['alt_usernames'] == ['jsmith@example.com']
        assert kw['match_email'] is True and kw['email'] == 'jordan.smith@example.com'
        assert kw['external_id'] == '9f2c1c1e-aaaa-bbbb-cccc-0123456789ab' and kw['groups'] == ['g-1', 'g-2']
        # the attempt's cookie is cleared
        assert any(c.startswith(harness.routes.STATE_COOKIE + '=;') for c in resp.headers.getlist('Set-Cookie'))

    def test_next_that_leaves_the_site_is_dropped(self, harness):
        for target in ('https://evil.example.test/steal', '//evil.example.test', 'javascript:alert(1)'):
            request_id, relay, cookie = harness.start(harness.client(), '?next=' + target)
            resp = harness.answer(build_response(harness.signer, in_response_to=request_id,
                                                 assertion_id='_a-' + str(abs(hash(target)))), relay, cookie)
            assert resp.status_code == 303 and resp.headers['Location'] == '/'

    def test_next_on_our_own_address_is_kept_as_a_path(self, harness):
        request_id, relay, cookie = harness.start(harness.client(), '?next=' + BASE + '/users?tab=2')
        resp = harness.answer(build_response(harness.signer, in_response_to=request_id), relay, cookie)
        assert resp.headers['Location'] == '/users?tab=2'

    def test_answer_without_the_cookie_is_refused(self, harness):
        request_id, relay, _ = harness.start(harness.client())
        resp = harness.answer(build_response(harness.signer, in_response_to=request_id), relay, cookie=None)
        assert resp.status_code == 400 and 'Start again' in resp.get_data(as_text=True)
        assert not harness.calls

    def test_answer_with_the_wrong_relay_state_is_refused(self, harness):
        request_id, _, cookie = harness.start(harness.client())
        resp = harness.answer(build_response(harness.signer, in_response_to=request_id), 'not-the-nonce', cookie)
        assert resp.status_code == 400 and not harness.calls

    def test_answer_to_another_browsers_request_is_refused(self, harness):
        """Login fixation: a valid answer to request A is posted on the browser that started B."""
        request_a, _, _ = harness.start(harness.client())
        _, relay_b, cookie_b = harness.start(harness.client())
        resp = harness.answer(build_response(harness.signer, in_response_to=request_a), relay_b, cookie_b)
        assert resp.status_code == 400 and not harness.calls

    def test_tampered_state_cookie_counts_as_no_cookie(self, harness):
        request_id, relay, cookie = harness.start(harness.client())
        name, value = cookie.split(';', 1)[0].split('=', 1)
        forged = f'{name}={value[:-4]}AAAA'
        resp = harness.answer(build_response(harness.signer, in_response_to=request_id), relay, forged)
        assert resp.status_code == 400 and not harness.calls

    def test_the_same_answer_cannot_be_used_twice(self, harness):
        request_id, relay, cookie = harness.start(harness.client())
        answer = build_response(harness.signer, in_response_to=request_id)
        assert harness.answer(answer, relay, cookie).status_code == 303
        again = harness.answer(answer, relay, cookie)
        assert again.status_code == 400 and 'already been used' in again.get_data(as_text=True)

    def test_refusal_page_hides_the_reason_and_sends_no_session_cookie(self, harness, other_signer):
        request_id, relay, cookie = harness.start(harness.client())
        resp = harness.answer(build_response(other_signer, in_response_to=request_id), relay, cookie)
        body = resp.get_data(as_text=True)
        assert resp.status_code == 400 and 'could not be verified' in body
        assert 'certificate' not in body.lower()  # the precise reason is for the log
        assert _session_cookies(resp) == []

    def test_verified_but_no_account_is_told_so_and_not_signed_in(self, harness):
        request_id, relay, cookie = harness.start(harness.client())
        browser = harness.client()
        resp = harness.answer(build_response(harness.signer, in_response_to=request_id,
                                             upn='stranger@example.com', name_id='stranger@example.com'),
                              relay, cookie, browser)
        assert resp.status_code == 403 and 'not set up for AI Hub' in resp.get_data(as_text=True)
        assert _session_cookies(resp) == []
        assert browser.get('/', base_url=BASE).get_data(as_text=True) == 'home:nobody'

    def test_switched_off_provider_offers_and_accepts_nothing(self, harness):
        harness.provider['is_enabled'] = False
        resp = harness.client().get('/auth/saml/login', base_url=BASE)
        assert resp.status_code == 302 and resp.headers['Location'].endswith('/login')
        unsolicited = harness.answer(build_response(harness.signer, in_response_to=None))
        assert unsolicited.status_code == 400 and not harness.calls

    def test_incomplete_provider_does_not_start(self, harness):
        harness.provider['config']['idp_certificates'] = []
        resp = harness.client().get('/auth/saml/login', base_url=BASE)
        assert resp.status_code == 302 and resp.headers['Location'].endswith('/login')


class TestStartedAtTheIdentityProvider:
    def test_refused_by_default(self, harness):
        resp = harness.answer(build_response(harness.signer, in_response_to=None))
        assert resp.status_code == 400 and not harness.calls

    def test_accepted_when_the_provider_allows_it(self, tmp_path, monkeypatch, signer):
        harness = _Harness(tmp_path, monkeypatch, signer, config={'allow_idp_initiated': True})
        browser = harness.client()
        resp = harness.answer(build_response(signer, in_response_to=None), client=browser)
        assert resp.status_code == 303 and resp.headers['Location'] == '/'
        assert browser.get('/', base_url=BASE).get_data(as_text=True) == 'home:jsmith'

    def test_a_leftover_cookie_from_an_abandoned_attempt_does_not_block_it(self, tmp_path, monkeypatch, signer):
        harness = _Harness(tmp_path, monkeypatch, signer, config={'allow_idp_initiated': True})
        _, _, stale_cookie = harness.start(harness.client())
        resp = harness.answer(build_response(signer, in_response_to=None), relay=None, cookie=stale_cookie)
        assert resp.status_code == 303

    def test_response_from_an_unknown_issuer_matches_no_provider(self, tmp_path, monkeypatch, signer):
        harness = _Harness(tmp_path, monkeypatch, signer, config={'allow_idp_initiated': True})
        resp = harness.answer(build_response(signer, in_response_to=None, issuer='https://sts.example.test/x/'))
        assert resp.status_code == 400 and not harness.calls


class TestAdminTestSignIn:
    def test_needs_a_signed_in_admin(self, harness):
        anonymous = harness.client().get('/auth/saml/login?provider=7&test=1', base_url=BASE)
        assert anonymous.status_code == 302 and anonymous.headers['Location'].endswith('/login')
        plain_user = harness.client()
        plain_user.get('/become/1', base_url=BASE)
        resp = plain_user.get('/auth/saml/login?provider=7&test=1', base_url=BASE)
        assert resp.status_code == 302 and resp.headers['Location'].endswith('/login')

    def test_shows_the_result_signs_nobody_in_and_keeps_the_admins_session(self, harness):
        harness.provider['is_enabled'] = False  # a test runs before the provider is switched on
        admin = harness.client()
        admin.get('/become/9', base_url=BASE)
        request_id, relay, cookie = harness.start(admin, '?provider=7&test=1')

        # the identity provider's POST arrives without the admin's session cookie
        resp = harness.answer(build_response(harness.signer, in_response_to=request_id), relay, cookie)
        body = resp.get_data(as_text=True)
        assert resp.status_code == 200 and 'Test sign-in passed' in body
        assert 'Would create new user: jsmith' in body and 'jordan.smith@example.com' in body
        assert saml.DEFAULT_ATTRIBUTES['attr_groups'] in body  # every claim is listed
        assert _session_cookies(resp) == []  # nothing that could replace the admin's cookie
        assert [kind for kind, _ in harness.calls] == ['preview']
        assert admin.get('/', base_url=BASE).get_data(as_text=True) == 'home:boss'

    def test_a_failed_test_shows_the_precise_reason(self, harness, other_signer):
        admin = harness.client()
        admin.get('/become/9', base_url=BASE)
        request_id, relay, cookie = harness.start(admin, '?provider=7&test=1')
        resp = harness.answer(build_response(other_signer, in_response_to=request_id), relay, cookie)
        body = resp.get_data(as_text=True)
        assert resp.status_code == 400 and 'No saved signing certificate verifies' in body
        assert _session_cookies(resp) == []


class TestOurMetadataRoute:
    def test_served_without_signing_in(self, harness):
        resp = harness.client().get('/auth/saml/metadata', base_url=BASE)
        assert resp.status_code == 200
        assert etree.fromstring(resp.get_data()).get('entityID') == SP

    def test_identifier_override(self, tmp_path, monkeypatch, signer):
        harness = _Harness(tmp_path, monkeypatch, signer, config={'sp_entity_id': 'urn:example:aihub'})
        resp = harness.client().get('/auth/saml/metadata', base_url=BASE)
        assert etree.fromstring(resp.get_data()).get('entityID') == 'urn:example:aihub'
        raw =harness.client().get('/auth/saml/login', base_url=BASE)
        issued = etree.fromstring(zlib.decompress(base64.b64decode(
            parse_qs(urlsplit(raw.headers['Location']).query)['SAMLRequest'][0]), -15))
        assert issued.find('saml:Issuer', NS).text == 'urn:example:aihub'


class TestPublishedAddress:
    def test_reverse_proxy_address_is_used_for_our_addresses(self, tmp_path, monkeypatch, signer):
        harness = _Harness(tmp_path, monkeypatch, signer)
        monkeypatch.setenv('USE_REVERSE_PROXY', 'true')
        monkeypatch.setenv('APP_PUBLIC_BASE_URL', 'https://published.example.test/')
        resp = harness.client().get('/auth/saml/login', base_url='http://127.0.0.1:5001')
        issued = etree.fromstring(zlib.decompress(base64.b64decode(
            parse_qs(urlsplit(resp.headers['Location']).query)['SAMLRequest'][0]), -15))
        assert issued.get('AssertionConsumerServiceURL') == 'https://published.example.test/auth/saml/acs'
        assert issued.find('saml:Issuer', NS).text == 'https://published.example.test/auth/saml/metadata'


# ---------------------------------------------------------------------------
# Matching a verified identity to an AI Hub user (real queries, in-memory DB)
# ---------------------------------------------------------------------------

@pytest.fixture
def people(monkeypatch):
    from flask import Flask
    from flask_sqlalchemy import SQLAlchemy
    from auth.user_provisioner import UserProvisioner

    monkeypatch.delenv('API_KEY', raising=False)
    app = Flask('saml_people')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
    db = SQLAlchemy(app)

    class User(db.Model):
        __tablename__ = 'User'
        id = db.Column(db.Integer, primary_key=True, autoincrement=True)
        username = db.Column('user_name', db.String(150), unique=True, nullable=False)
        name = db.Column(db.String(150), nullable=False)
        email = db.Column(db.String(150))
        role = db.Column(db.Integer, nullable=False)
        password = db.Column(db.String(255))
        auth_provider = db.Column(db.String(50), nullable=False, default='local')
        external_id = db.Column(db.String(255), nullable=True)
        external_email = db.Column(db.String(255), nullable=True)
        last_sso_login = db.Column(db.DateTime, nullable=True)

    def add_user(user_id, user_name, role, email, phone, name, password, auth_provider='local', external_id=''):
        row = User(username=user_name, role=role, email=email or None, name=name, password=password,
                   auth_provider=auth_provider, external_id=external_id or None)
        db.session.add(row)
        db.session.commit()
        return row.id, True

    monkeypatch.setitem(sys.modules, 'DataUtils', types.SimpleNamespace(Add_User=add_user))

    ctx = app.app_context()
    ctx.push()
    db.create_all()

    def make(username, provider='local', external_id=None, email=None, role=1):
        row = User(username=username, name=username.title(), role=role, password='x', email=email,
                   auth_provider=provider, external_id=external_id)
        db.session.add(row)
        db.session.commit()
        return row

    kit = types.SimpleNamespace(User=User, db=db, make=make, provisioner=UserProvisioner(db, User, None))
    kit.saml = lambda **kw: kit.provisioner.find_or_create_user(**{
        'auth_provider': 'saml', 'external_id': 'oid-1', 'username': 'jsmith', 'name': 'Jordan Smith',
        'email': 'jordan.smith@example.com', 'alt_usernames': ['jsmith@example.com'], 'match_email': True,
        'relink_external': True, **kw})
    kit.ldap = lambda **kw: kit.provisioner.find_or_create_user(**{
        'auth_provider': 'ldap', 'external_id': 'jsmith', 'username': 'jsmith', 'name': 'Jordan Smith', **kw})
    yield kit
    db.session.remove()
    ctx.pop()


class TestMatchingForSaml:
    def test_already_linked_user_is_found_first(self, people):
        linked = people.make('someone-renamed', provider='saml', external_id='oid-1')
        people.make('jsmith')
        assert people.saml().id == linked.id

    def test_local_user_is_matched_by_short_name_and_relinked(self, people):
        local = people.make('jsmith', role=2)
        user = people.saml()
        assert user.id == local.id and user.auth_provider == 'saml' and user.external_id == 'oid-1'
        assert user.role == 2 and user.external_email == 'jordan.smith@example.com'
        assert people.User.query.count() == 1

    def test_directory_user_is_relinked_automatically(self, people):
        directory = people.make('jsmith', provider='ldap', external_id='jsmith', role=3)
        user = people.saml()
        assert user.id == directory.id and user.auth_provider == 'saml' and user.external_id == 'oid-1'
        assert user.role == 3

    def test_full_sign_in_name_matches_when_the_short_one_does_not(self, people):
        full = people.make('jsmith@example.com')
        assert people.saml().id == full.id

    def test_short_name_wins_over_full_name(self, people):
        people.make('jsmith@example.com')
        short = people.make('jsmith')
        assert people.saml().id == short.id

    def test_email_matches_when_no_name_does(self, people):
        by_email = people.make('jordan', email='jordan.smith@example.com', role=2)
        user = people.saml()
        assert user.id == by_email.id and user.username == 'jordan' and user.auth_provider == 'saml'

    def test_email_shared_by_two_users_matches_nobody(self, people):
        people.make('one', email='jordan.smith@example.com')
        people.make('two', email='jordan.smith@example.com')
        user = people.saml()
        assert user.username == 'jsmith' and people.User.query.count() == 3

    def test_email_matching_is_only_for_callers_that_ask(self, people):
        people.make('jordan', email='jordan.smith@example.com')
        user = people.saml(match_email=False)
        assert user.username == 'jsmith' and people.User.query.count() == 2

    def test_nobody_matches_a_new_user_is_created(self, people):
        user = people.saml()
        assert user.username == 'jsmith' and user.auth_provider == 'saml' and user.external_id == 'oid-1'
        assert user.role == 1

    def test_group_gate_applies_to_new_users_only(self, people):
        mapping = {'AIHub Users': 1}
        assert people.saml(group_role_mapping=mapping, groups=['Everyone']) is None
        existing = people.make('jsmith')
        assert people.saml(group_role_mapping=mapping, groups=['Everyone']).id == existing.id

    def test_mapped_group_raises_but_never_lowers_a_role(self, people):
        people.make('jsmith', role=3)
        assert people.saml(group_role_mapping={'g-1': 2}, groups=['g-1']).role == 3
        people.make('second')
        assert people.saml(external_id='oid-2', username='second', alt_usernames=[], email=None,
                           group_role_mapping={'g-1': 2}, groups=['G-1']).role == 2

    def test_creating_users_switched_off(self, people):
        assert people.saml(auto_provision=False) is None

    def test_preview_reports_each_outcome_and_changes_nothing(self, people):
        args = dict(auth_provider='saml', external_id='oid-1', username='jsmith', email='jordan.smith@example.com',
                    alt_usernames=['jsmith@example.com'], match_email=True)
        assert people.provisioner.preview(**args)['outcome'] == 'create'
        assert people.provisioner.preview(auto_provision=False, **args)['outcome'] == 'refuse'
        assert people.provisioner.preview(group_role_mapping={'x': 1}, groups=['y'], **args)['outcome'] == 'refuse'
        local = people.make('jsmith', role=2)
        linked = people.provisioner.preview(**args)
        assert linked['outcome'] == 'link' and linked['username'] == 'jsmith' and linked['role'] == 2
        people.db.session.refresh(local)
        assert local.auth_provider == 'local' and local.external_id is None  # untouched
        local.auth_provider, local.external_id = 'saml', 'oid-1'
        people.db.session.commit()
        assert people.provisioner.preview(**args)['outcome'] == 'existing'


class TestDirectorySignInIsUnchanged:
    def test_local_user_is_still_linked_by_name(self, people):
        local = people.make('jsmith', role=2)
        user = people.ldap()
        assert user.id == local.id and user.auth_provider == 'ldap' and user.external_id == 'jsmith'

    def test_directory_sign_in_never_matches_by_email_or_other_names(self, people):
        people.make('jordan', email='jordan.smith@example.com')
        people.make('jsmith@example.com')
        user = people.ldap(email='jordan.smith@example.com')
        assert user.username == 'jsmith' and people.User.query.count() == 3

    def test_user_relinked_to_saml_can_still_use_the_directory_password(self, people):
        people.make('jsmith', provider='ldap', external_id='jsmith')
        via_saml = people.saml()
        again = people.ldap()
        assert again is not None and again.id == via_saml.id
        assert again.auth_provider == 'saml' and again.external_id == 'oid-1'  # link left alone
        assert people.User.query.count() == 1

    def test_two_providers_of_the_same_kind_are_still_not_merged(self, people):
        people.make('jsmith', provider='ldap', external_id='a-different-directory-id')
        assert people.ldap() is None  # as before: refused, not handed the other account


# ---------------------------------------------------------------------------
# Saving a SAML provider from the admin screen
# ---------------------------------------------------------------------------

@pytest.fixture
def identity_routes(monkeypatch):
    def passthrough(api=False):
        return lambda f: f
    monkeypatch.setitem(sys.modules, 'role_decorators', types.SimpleNamespace(admin_required=passthrough))
    monkeypatch.setitem(sys.modules, 'DataUtils', types.SimpleNamespace(
        Get_Identity_Providers=None, Save_Identity_Provider=None, Delete_Identity_Provider=None))
    monkeypatch.delitem(sys.modules, 'auth_identity_routes', raising=False)
    module = importlib.import_module('auth_identity_routes')
    yield module
    sys.modules.pop('auth_identity_routes', None)


class TestSavingAProvider:
    def _config(self, signer, **over):
        config = {'idp_entity_id': f' {IDP} ', 'idp_sso_url': SSO, 'idp_certificates': signer[1],
                  'username_domain': '@example.com', 'allow_idp_initiated': 0}
        config.update(over)
        return config

    def test_values_are_tidied(self, identity_routes, signer):
        clean, problem = identity_routes._clean_saml_config(self._config(signer))
        assert problem == ''
        assert clean['idp_entity_id'] == IDP and clean['username_domain'] == 'example.com'
        assert clean['idp_certificates'] == saml.normalize_certificates(signer[1])
        assert clean['allow_idp_initiated'] is False and 'clock_skew_seconds' not in clean

    @pytest.mark.parametrize('change,expected', [
        ({'idp_entity_id': ''}, 'identifier is required'),
        ({'idp_sso_url': 'javascript:alert(1)'}, 'login URL'),
        ({'idp_certificates': ''}, 'signing certificate is required'),
        ({'idp_certificates': 'rubbish'}, 'not a valid X.509'),
        ({'clock_skew_seconds': 'soon'}, 'whole number'),
        ({'clock_skew_seconds': -5}, 'cannot be negative'),
    ])
    def test_problems_are_named(self, identity_routes, signer, change, expected):
        _, problem = identity_routes._clean_saml_config(self._config(signer, **change))
        assert expected in problem

    def test_clock_tolerance_is_kept_when_given(self, identity_routes, signer):
        clean, problem = identity_routes._clean_saml_config(self._config(signer, clock_skew_seconds='60'))
        assert problem == '' and clean['clock_skew_seconds'] == 60


# ---------------------------------------------------------------------------
# Wiring that only a full app start would otherwise show
# ---------------------------------------------------------------------------

class TestWiring:
    def test_sign_in_routes_pass_the_global_sign_in_guard(self):
        tree = ast.parse((_ROOT / 'auth_middleware.py').read_text(encoding='utf-8'))
        unprotected = next(ast.literal_eval(node.value) for node in ast.walk(tree)
                           if isinstance(node, ast.Assign)
                           and getattr(node.targets[0], 'id', '') == 'UNPROTECTED_ENDPOINTS')
        assert {'saml.login', 'saml.acs', 'saml.metadata'} <= unprotected

    def test_app_registers_the_routes_and_survives_their_absence(self):
        source = (_ROOT / 'app.py').read_text(encoding='utf-8', errors='replace')
        block = source[source.index('from auth.saml_routes import create_saml_blueprint') - 200:][:700]
        assert 'try:' in block and 'except Exception' in block
        assert 'create_saml_blueprint(db=db, user_class=User, bcrypt_instance=bcrypt)' in block

    def test_sign_in_page_shows_a_button_only_when_there_is_a_provider(self):
        from jinja2 import DictLoader, Environment
        login = (_ROOT / 'templates' / 'login.html').read_text(encoding='utf-8')
        env = Environment(loader=DictLoader({'base.html': '{% block content %}{% endblock %}', 'login.html': login}),
                          autoescape=True)

        class Field:
            label = staticmethod(lambda **kw: '')

            def __call__(self, **kw):
                return ''

        form = types.SimpleNamespace(hidden_tag=lambda: '', username=Field(), password=Field(), remember=Field(),
                                     submit=Field())
        none = env.get_template('login.html').render(form=form, saml_sign_in_options=lambda: [])
        assert 'login-sso"' not in none and 'Sign in with' not in none
        missing = env.get_template('login.html').render(form=form)  # routes not registered at all
        assert 'Sign in with' not in missing
        one = env.get_template('login.html').render(form=form, saml_sign_in_options=lambda: [
            {'name': 'Example <Corp>', 'url': '/auth/saml/login?provider=7'}])
        assert 'href="/auth/saml/login?provider=7"' in one
        assert 'Sign in with Example &lt;Corp&gt;' in one
