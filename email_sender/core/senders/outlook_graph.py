"""Outlook backend: MSAL (OAuth) + Microsoft Graph.

No basic-auth SMTP (Microsoft is switching it off). Sending uses a public-client
interactive sign-in once; MSAL then keeps a refresh token in Windows Credential
Manager and renews access tokens silently.

To make the power-cut failsafe work, each send sets a controlled
``internetMessageId`` on a draft, then sends the draft. :meth:`find_in_sent`
looks that id up in the Sent Items folder.

Network/auth plumbing is injectable (``token_provider`` and ``http``) so the
message-building and error-mapping logic can be unit-tested without a real
Microsoft account.
"""

from __future__ import annotations

from email.utils import make_msgid
from typing import Any, Callable, Optional, Protocol

from .. import config, secrets
from .base import AuthError, NetworkError, PermanentError, Sender, SenderError, TestResult

#: A token provider takes ``interactive`` and returns an access token or None.
TokenProvider = Callable[[bool], Optional[str]]


class _Http(Protocol):  # pragma: no cover - typing only
    def request(self, method: str, url: str, **kwargs: Any) -> Any: ...


def build_graph_message(*, to: str, subject: str, body: str, internet_message_id: str) -> dict:
    """Build the Graph draft-message payload (plain-text body)."""
    return {
        "subject": subject,
        "body": {"contentType": "Text", "content": body},
        "toRecipients": [{"emailAddress": {"address": to}}],
        "internetMessageId": internet_message_id,
    }


def classify_graph_error(status_code: int, text: str = "") -> SenderError:
    """Map an HTTP status from Graph to the sender error taxonomy."""
    detail = (text or "").strip()
    if status_code in (401, 403):
        return AuthError("Microsoft sign-in has expired. Open Settings and sign in again.")
    if status_code == 429 or 500 <= status_code < 600:
        return NetworkError(f"Microsoft Graph is temporarily unavailable ({status_code}). {detail}")
    if 400 <= status_code < 500:
        return PermanentError(f"Message rejected ({status_code}). {detail}")
    return NetworkError(f"Unexpected Graph response ({status_code}). {detail}")


class OutlookSender(Sender):
    """Send and verify through a user's Outlook/Microsoft 365 account via Graph."""

    provider = "Outlook"

    def __init__(
        self,
        *,
        client_id: str | None = None,
        token_provider: TokenProvider | None = None,
        http: _Http | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._client_id = client_id if client_id is not None else config.outlook_client_id()
        self._timeout = timeout
        self._token_provider = token_provider
        self._http = http
        self._msal_app = None  # built lazily only when needed

    # --- interactive account management --------------------------------------
    def sign_in(self) -> TestResult:
        """Interactively sign the user in (opens the system browser once)."""
        try:
            token = self._get_token(interactive=True)
        except AuthError as exc:
            return TestResult(ok=False, message=str(exc))
        if not token:
            return TestResult(ok=False, message="Microsoft sign-in was cancelled or failed.")
        return self._verify(token)

    def sign_out(self) -> None:
        """Forget the stored account/token cache."""
        secrets.delete_secret(secrets.OUTLOOK_TOKEN_CACHE)
        self._msal_app = None

    def test_connection(self) -> TestResult:
        """Verify we hold a valid (silently renewable) token and can call Graph."""
        try:
            token = self._get_token(interactive=False)
        except AuthError as exc:
            return TestResult(ok=False, message=str(exc))
        if not token:
            return TestResult(ok=False, message="Not signed in. Click 'Sign in with Microsoft'.")
        return self._verify(token)

    # --- Sender interface -----------------------------------------------------
    def new_message_id(self) -> str:
        return make_msgid(domain="outlook.com")

    def send(self, *, to: str, subject: str, body: str, message_id: str) -> str:
        token = self._require_token()
        payload = build_graph_message(
            to=to, subject=subject, body=body, internet_message_id=message_id
        )
        # 1) Create the draft (lets us pin internetMessageId for later lookup).
        draft = self._graph("POST", "/me/messages", token, json=payload)
        draft_id = draft.get("id")
        if not draft_id:
            raise NetworkError("Graph did not return a draft id; will retry.")
        # 2) Send it (moves it to Sent Items).
        self._graph("POST", f"/me/messages/{draft_id}/send", token, expect_json=False)
        return message_id

    def find_in_sent(self, *, message_id: str, recipient: str, subject: str | None = None) -> bool:
        token = self._require_token()
        params = {
            "$filter": f"internetMessageId eq '{message_id}'",
            "$select": "id",
            "$top": "1",
        }
        data = self._graph("GET", "/me/mailFolders/sentitems/messages", token, params=params)
        return bool(data.get("value"))

    # --- internals ------------------------------------------------------------
    def _verify(self, token: str) -> TestResult:
        try:
            me = self._graph("GET", "/me", token, params={"$select": "mail,userPrincipalName"})
        except SenderError as exc:
            return TestResult(ok=False, message=str(exc))
        address = me.get("mail") or me.get("userPrincipalName") or ""
        return TestResult(ok=True, address=address, message=f"Signed in as {address}.")

    def _require_token(self) -> str:
        token = self._get_token(interactive=False)
        if not token:
            raise AuthError("Not signed in to Microsoft. Open Settings and sign in.")
        return token

    def _get_token(self, *, interactive: bool) -> str | None:
        if self._token_provider is not None:
            return self._token_provider(interactive)
        return self._msal_token(interactive=interactive)

    def _msal_token(self, *, interactive: bool) -> str | None:
        import msal  # imported lazily; heavy and only needed for real sign-in

        if not self._client_id:
            raise AuthError(
                "Outlook is not configured in this build (missing Microsoft client ID)."
            )
        cache = msal.SerializableTokenCache()
        stored = secrets.get_secret(secrets.OUTLOOK_TOKEN_CACHE)
        if stored:
            cache.deserialize(stored)
        app = msal.PublicClientApplication(
            self._client_id, authority=config.OUTLOOK_AUTHORITY, token_cache=cache
        )
        scopes = list(config.OUTLOOK_SCOPES)
        result = None
        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(scopes, account=accounts[0])
        if result is None and interactive:
            result = app.acquire_token_interactive(scopes)

        if cache.has_state_changed:
            secrets.set_secret(secrets.OUTLOOK_TOKEN_CACHE, cache.serialize())

        if not result:
            return None
        if "access_token" in result:
            return result["access_token"]
        raise AuthError(result.get("error_description") or "Microsoft sign-in failed.")

    def _graph(
        self,
        method: str,
        path: str,
        token: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        expect_json: bool = True,
    ) -> dict:
        http = self._http
        if http is None:
            import requests  # lazy

            http = requests.Session()
            self._http = http

        url = path if path.startswith("http") else f"{config.GRAPH_BASE}{path}"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = http.request(
                method, url, headers=headers, json=json, params=params, timeout=self._timeout
            )
        except Exception as exc:  # noqa: BLE001 - network layer
            raise NetworkError(f"Could not reach Microsoft Graph: {exc}") from exc

        status = getattr(resp, "status_code", 0)
        if status >= 400:
            text = ""
            try:
                text = resp.text
            except Exception:  # noqa: BLE001
                pass
            raise classify_graph_error(status, text)
        if not expect_json:
            return {}
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            return {}
