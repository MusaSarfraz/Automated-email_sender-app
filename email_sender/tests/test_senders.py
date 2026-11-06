"""Tests for the sender interface: message building, error mapping, and the
send / find-in-sent flows using injected fakes (no real network)."""

from __future__ import annotations

import smtplib
import socket

import pytest

from email_sender.core.senders import AuthError, NetworkError, PermanentError
from email_sender.core.senders import gmail_smtp as G
from email_sender.core.senders import outlook_graph as O


# --------------------------------------------------------------------------- #
# Gmail: pure helpers                                                         #
# --------------------------------------------------------------------------- #
def test_gmail_build_message_preserves_body_and_headers():
    msg = G.build_message(
        from_addr="me@gmail.com",
        to="you@x.com",
        subject="Hi",
        body="Line one\n\nLine two",
        message_id="<abc@gmail.com>",
    )
    assert msg["From"] == "me@gmail.com"
    assert msg["To"] == "you@x.com"
    assert msg["Subject"] == "Hi"
    assert msg["Message-ID"] == "<abc@gmail.com>"
    assert msg.get_content().rstrip("\n") == "Line one\n\nLine two"


@pytest.mark.parametrize(
    "exc, expected",
    [
        (smtplib.SMTPAuthenticationError(535, b"bad creds"), AuthError),
        (smtplib.SMTPRecipientsRefused({"x@y.com": (550, b"No such user")}), PermanentError),
        (smtplib.SMTPResponseException(550, b"Mailbox not found"), PermanentError),
        (smtplib.SMTPResponseException(451, b"Try later"), NetworkError),
        (socket.gaierror("name resolution"), NetworkError),
        (TimeoutError("timed out"), NetworkError),
        (smtplib.SMTPServerDisconnected("dropped"), NetworkError),
    ],
)
def test_gmail_classify_smtp_error(exc, expected):
    assert isinstance(G.classify_smtp_error(exc), expected)


# --------------------------------------------------------------------------- #
# Gmail: send flow with a fake SMTP                                           #
# --------------------------------------------------------------------------- #
class _FakeSMTP:
    def __init__(self, fail=None):
        self.fail = fail
        self.logged_in = False
        self.sent = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, pw):
        self.logged_in = True

    def send_message(self, msg):
        if self.fail:
            raise self.fail
        self.sent = msg


def test_gmail_send_returns_message_id():
    sender = G.GmailSender("me@gmail.com", "pw")
    fake = _FakeSMTP()
    sender._smtp = lambda: fake
    mid = sender.send(to="you@x.com", subject="S", body="B", message_id="<id@gmail.com>")
    assert mid == "<id@gmail.com>"
    assert fake.logged_in and fake.sent is not None


def test_gmail_send_maps_permanent_rejection():
    sender = G.GmailSender("me@gmail.com", "pw")
    sender._smtp = lambda: _FakeSMTP(fail=smtplib.SMTPResponseException(550, b"bad"))
    with pytest.raises(PermanentError):
        sender.send(to="you@x.com", subject="S", body="B", message_id="<id>")


def test_gmail_send_maps_network_error():
    sender = G.GmailSender("me@gmail.com", "pw")
    sender._smtp = lambda: _FakeSMTP(fail=TimeoutError("nope"))
    with pytest.raises(NetworkError):
        sender.send(to="you@x.com", subject="S", body="B", message_id="<id>")


def test_gmail_new_message_id_uses_domain():
    sender = G.GmailSender("me@gmail.com", "pw")
    assert sender.new_message_id().endswith("@gmail.com>")


# --------------------------------------------------------------------------- #
# Outlook: pure helpers                                                       #
# --------------------------------------------------------------------------- #
def test_outlook_build_graph_message():
    payload = O.build_graph_message(
        to="you@x.com", subject="S", body="B\nC", internet_message_id="<mid@outlook.com>"
    )
    assert payload["toRecipients"][0]["emailAddress"]["address"] == "you@x.com"
    assert payload["body"] == {"contentType": "Text", "content": "B\nC"}
    assert payload["internetMessageId"] == "<mid@outlook.com>"


@pytest.mark.parametrize(
    "status, expected",
    [
        (401, AuthError),
        (403, AuthError),
        (429, NetworkError),
        (500, NetworkError),
        (503, NetworkError),
        (400, PermanentError),
        (404, PermanentError),
    ],
)
def test_outlook_classify_graph_error(status, expected):
    assert isinstance(O.classify_graph_error(status, "detail"), expected)


# --------------------------------------------------------------------------- #
# Outlook: send / find flows with a fake HTTP client                          #
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


class _FakeHttp:
    def __init__(self, routes):
        # routes: list of (method, url_substring, _FakeResp)
        self.routes = routes
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for m, sub, resp in self.routes:
            if m == method and sub in url:
                return resp
        raise AssertionError(f"Unexpected request: {method} {url}")


def _outlook(http):
    return O.OutlookSender(client_id="cid", token_provider=lambda interactive: "token", http=http)


def test_outlook_send_creates_draft_then_sends():
    http = _FakeHttp(
        [
            ("POST", "/me/messages/draft123/send", _FakeResp(202)),
            ("POST", "/me/messages", _FakeResp(201, {"id": "draft123"})),
        ]
    )
    sender = _outlook(http)
    mid = sender.send(to="you@x.com", subject="S", body="B", message_id="<mid@outlook.com>")
    assert mid == "<mid@outlook.com>"
    methods = [(m, "send" in u) for m, u, _ in http.calls]
    assert methods == [("POST", False), ("POST", True)]  # draft created, then sent


def test_outlook_send_auth_error_maps():
    http = _FakeHttp([("POST", "/me/messages", _FakeResp(401))])
    with pytest.raises(AuthError):
        _outlook(http).send(to="you@x.com", subject="S", body="B", message_id="<m>")


def test_outlook_find_in_sent_true_when_value_present():
    http = _FakeHttp([("GET", "/me/mailFolders/sentitems/messages", _FakeResp(200, {"value": [{"id": "1"}]}))])
    assert _outlook(http).find_in_sent(message_id="<m>", recipient="you@x.com") is True


def test_outlook_find_in_sent_false_when_empty():
    http = _FakeHttp([("GET", "/me/mailFolders/sentitems/messages", _FakeResp(200, {"value": []}))])
    assert _outlook(http).find_in_sent(message_id="<m>", recipient="you@x.com") is False


def test_outlook_find_in_sent_uses_internet_message_id_filter():
    http = _FakeHttp([("GET", "/me/mailFolders/sentitems/messages", _FakeResp(200, {"value": []}))])
    _outlook(http).find_in_sent(message_id="<mid@outlook.com>", recipient="you@x.com")
    _, _, kwargs = http.calls[0]
    assert kwargs["params"]["$filter"] == "internetMessageId eq '<mid@outlook.com>'"


def test_outlook_test_connection_reports_address():
    http = _FakeHttp([("GET", "/me", _FakeResp(200, {"mail": "me@outlook.com"}))])
    result = _outlook(http).test_connection()
    assert result.ok and result.address == "me@outlook.com"


def test_outlook_not_signed_in_returns_not_ok():
    sender = O.OutlookSender(client_id="cid", token_provider=lambda interactive: None, http=_FakeHttp([]))
    result = sender.test_connection()
    assert not result.ok


def test_outlook_network_failure_is_network_error():
    class _Boom:
        def request(self, *a, **k):
            raise ConnectionError("no route")

    with pytest.raises(NetworkError):
        _outlook(_Boom()).find_in_sent(message_id="<m>", recipient="you@x.com")
