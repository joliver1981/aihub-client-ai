"""
Unit tests for email_graph.py — Microsoft 365 (Graph API) notification sender.

All HTTP is mocked at email_graph.requests.post. The contract under test:
  * client-credentials token fetched once, cached, refreshed near expiry;
  * Microsoft's own error text (AADSTS..., ErrorAccessDenied...) surfaces in
    GraphMailError so the Email Settings "send test" can show it;
  * sendMail goes to /users/{sender}/sendMail with a Bearer token;
  * a rejected (401) token is refreshed exactly once;
  * inline attachments are base64 and refused above 3 MB (SMTP fallback
    carries them);
  * send_email_graph keeps the bool never-raises contract of send_email_smtp.
"""

import base64
from unittest.mock import MagicMock, patch

import pytest
import requests

import email_graph
from email_graph import GraphMailError

pytestmark = pytest.mark.unit

CONF = {
    'graph_tenant_id': 'tenant-1',
    'graph_client_id': 'client-1',
    'graph_client_secret': 's3cret',
    'graph_sender': 'alerts@contoso.test',
}


def _resp(status, json_body=None, text=''):
    r = MagicMock()
    r.status_code = status
    r.text = text
    if json_body is None:
        r.json.side_effect = ValueError('no json body')
    else:
        r.json.return_value = json_body
    return r


def _token_ok(token='tok-1', expires_in=3600):
    return _resp(200, {'access_token': token, 'expires_in': expires_in, 'token_type': 'Bearer'})


@pytest.fixture(autouse=True)
def clean_token_cache():
    email_graph.clear_token_cache()
    yield
    email_graph.clear_token_cache()


# ---------------------------------------------------------------- token

class TestGetAccessToken:

    def test_missing_config_raises_before_any_http(self):
        with patch('email_graph.requests.post') as post:
            with pytest.raises(GraphMailError, match='not configured'):
                email_graph.get_access_token('', 'client-1', 's3cret')
        post.assert_not_called()

    def test_fetches_client_credentials_token_and_caches_it(self):
        with patch('email_graph.requests.post', return_value=_token_ok('tok-1')) as post:
            first = email_graph.get_access_token('tenant-1', 'client-1', 's3cret')
            second = email_graph.get_access_token('tenant-1', 'client-1', 's3cret')

        assert first == second == 'tok-1'
        assert post.call_count == 1
        args, kwargs = post.call_args
        assert args[0] == 'https://login.microsoftonline.com/tenant-1/oauth2/v2.0/token'
        assert kwargs['data'] == {
            'grant_type': 'client_credentials',
            'client_id': 'client-1',
            'client_secret': 's3cret',
            'scope': 'https://graph.microsoft.com/.default',
        }

    def test_token_near_expiry_is_refreshed(self):
        responses = [_token_ok('tok-1', expires_in=100), _token_ok('tok-2', expires_in=3600)]
        with patch('email_graph.requests.post', side_effect=responses) as post:
            assert email_graph.get_access_token('tenant-1', 'client-1', 's3cret') == 'tok-1'
            # 100 s left is inside the 300 s leeway -> fetch again
            assert email_graph.get_access_token('tenant-1', 'client-1', 's3cret') == 'tok-2'
        assert post.call_count == 2

    def test_entra_error_text_surfaces(self):
        err = _resp(400, {'error': 'invalid_client',
                          'error_description': 'AADSTS7000215: Invalid client secret provided.'})
        with patch('email_graph.requests.post', return_value=err):
            with pytest.raises(GraphMailError) as exc:
                email_graph.get_access_token('tenant-1', 'client-1', 'wrong')
        assert 'HTTP 400' in str(exc.value)
        assert 'AADSTS7000215' in str(exc.value)

    def test_network_error_is_wrapped(self):
        with patch('email_graph.requests.post', side_effect=requests.ConnectionError('dns')):
            with pytest.raises(GraphMailError, match='token request failed'):
                email_graph.get_access_token('tenant-1', 'client-1', 's3cret')


# ---------------------------------------------------------------- message body

class TestBuildMessage:

    def test_text_body_and_recipients(self):
        payload = email_graph.build_message(['a@x.test', 'b@x.test'], 'Subj', 'Hello')
        msg = payload['message']
        assert payload['saveToSentItems'] is True
        assert msg['subject'] == 'Subj'
        assert msg['body'] == {'contentType': 'Text', 'content': 'Hello'}
        assert [r['emailAddress']['address'] for r in msg['toRecipients']] == ['a@x.test', 'b@x.test']
        assert 'attachments' not in msg
        assert 'ccRecipients' not in msg

    def test_html_body(self):
        msg = email_graph.build_message('a@x.test', 'S', '<b>hi</b>', html_content=True)['message']
        assert msg['body'] == {'contentType': 'HTML', 'content': '<b>hi</b>'}

    def test_string_lists_cc_and_bcc(self):
        msg = email_graph.build_message('a@x.test, b@x.test', 'S', 'B',
                                        cc='c@x.test', bcc=['d@x.test'])['message']
        assert [r['emailAddress']['address'] for r in msg['toRecipients']] == ['a@x.test', 'b@x.test']
        assert msg['ccRecipients'] == [{'emailAddress': {'address': 'c@x.test'}}]
        assert msg['bccRecipients'] == [{'emailAddress': {'address': 'd@x.test'}}]

    def test_no_recipient_raises(self):
        with pytest.raises(GraphMailError, match='recipient'):
            email_graph.build_message([], 'S', 'B')

    def test_inline_attachment_is_base64(self, tmp_path):
        f = tmp_path / 'report.xlsx'
        f.write_bytes(b'\x00\x01binary')
        msg = email_graph.build_message('a@x.test', 'S', 'B', attachment_path=str(f))['message']
        assert msg['attachments'] == [{
            '@odata.type': '#microsoft.graph.fileAttachment',
            'name': 'report.xlsx',
            'contentType': 'application/octet-stream',
            'contentBytes': base64.b64encode(b'\x00\x01binary').decode('ascii'),
        }]

    def test_oversized_attachment_raises_for_smtp_fallback(self, tmp_path):
        f = tmp_path / 'big.bin'
        with open(f, 'wb') as fh:
            fh.seek(email_graph.INLINE_ATTACHMENT_LIMIT)   # one byte over the limit
            fh.write(b'x')
        with pytest.raises(GraphMailError, match='3 MB'):
            email_graph.build_message('a@x.test', 'S', 'B', attachment_path=str(f))

    def test_missing_attachment_raises(self, tmp_path):
        with pytest.raises(GraphMailError, match='not found'):
            email_graph.build_message('a@x.test', 'S', 'B', attachment_path=str(tmp_path / 'nope.pdf'))


# ---------------------------------------------------------------- sendMail

class TestSendMail:

    def test_posts_to_sender_mailbox_with_bearer_token(self):
        with patch('email_graph.requests.post', side_effect=[_token_ok('tok-1'), _resp(202)]) as post:
            email_graph.send_mail(CONF, ['to@x.test'], 'Subj', 'Body')

        assert post.call_count == 2
        args, kwargs = post.call_args_list[1]
        assert args[0] == 'https://graph.microsoft.com/v1.0/users/alerts@contoso.test/sendMail'
        assert kwargs['headers']['Authorization'] == 'Bearer tok-1'
        assert kwargs['json']['message']['subject'] == 'Subj'
        assert kwargs['json']['message']['toRecipients'] == [{'emailAddress': {'address': 'to@x.test'}}]

    def test_graph_error_text_surfaces(self):
        denied = _resp(403, {'error': {'code': 'ErrorAccessDenied',
                                       'message': 'Access is denied. Check credentials and try again.'}})
        with patch('email_graph.requests.post', side_effect=[_token_ok(), denied]):
            with pytest.raises(GraphMailError) as exc:
                email_graph.send_mail(CONF, 'to@x.test', 'S', 'B')
        assert 'HTTP 403' in str(exc.value)
        assert 'ErrorAccessDenied' in str(exc.value)
        assert 'Access is denied' in str(exc.value)

    def test_rejected_token_is_refreshed_exactly_once(self):
        responses = [_token_ok('tok-1'), _resp(401, {'error': {'code': 'InvalidAuthenticationToken',
                                                               'message': 'expired'}}),
                     _token_ok('tok-2'), _resp(202)]
        with patch('email_graph.requests.post', side_effect=responses) as post:
            email_graph.send_mail(CONF, 'to@x.test', 'S', 'B')

        assert post.call_count == 4
        assert post.call_args_list[1][1]['headers']['Authorization'] == 'Bearer tok-1'
        assert post.call_args_list[3][1]['headers']['Authorization'] == 'Bearer tok-2'

    def test_second_401_is_reported_not_retried_forever(self):
        responses = [_token_ok('tok-1'), _resp(401, {'error': {'code': 'InvalidAuthenticationToken',
                                                               'message': 'bad'}}),
                     _token_ok('tok-2'), _resp(401, {'error': {'code': 'InvalidAuthenticationToken',
                                                               'message': 'still bad'}})]
        with patch('email_graph.requests.post', side_effect=responses) as post:
            with pytest.raises(GraphMailError, match='HTTP 401'):
                email_graph.send_mail(CONF, 'to@x.test', 'S', 'B')
        assert post.call_count == 4

    def test_missing_sender_raises_before_any_http(self):
        conf = dict(CONF, graph_sender='')
        with patch('email_graph.requests.post') as post:
            with pytest.raises(GraphMailError, match='sender mailbox'):
                email_graph.send_mail(conf, 'to@x.test', 'S', 'B')
        post.assert_not_called()

    def test_sendmail_network_error_is_wrapped(self):
        with patch('email_graph.requests.post',
                   side_effect=[_token_ok(), requests.ReadTimeout('slow')]):
            with pytest.raises(GraphMailError, match='sendMail request failed'):
                email_graph.send_mail(CONF, 'to@x.test', 'S', 'B')


# ---------------------------------------------------------------- bool wrapper

class TestSendEmailGraph:

    def test_returns_true_on_success(self):
        with patch('email_graph.requests.post', side_effect=[_token_ok(), _resp(202)]):
            assert email_graph.send_email_graph('to@x.test', 'S', 'B', conf=CONF) is True

    def test_returns_false_and_never_raises_on_failure(self):
        err = _resp(400, {'error': 'invalid_client', 'error_description': 'AADSTS7000215: bad secret'})
        with patch('email_graph.requests.post', return_value=err):
            assert email_graph.send_email_graph('to@x.test', 'S', 'B', conf=CONF) is False

    def test_resolves_live_email_settings_when_no_conf_given(self):
        import email_settings
        with patch.object(email_settings, 'get_email_config', return_value=dict(CONF)) as resolver, \
             patch('email_graph.requests.post', side_effect=[_token_ok(), _resp(202)]):
            assert email_graph.send_email_graph('to@x.test', 'S', 'B') is True
        resolver.assert_called_once()
