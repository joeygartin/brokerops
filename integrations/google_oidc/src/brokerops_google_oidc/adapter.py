"""Google OIDC ID-token verifier (IdentityVerifier adapter).

The dashboard signs operators in with Google Identity Services and sends the
resulting ID token as a bearer. This adapter verifies that token against
Google's signing certs, enforces the audience (our OAuth client id), and then
applies a flat allowlist (hosted domain and/or explicit emails). A token we
cannot trust is a 401; a valid identity that is off the allowlist is a 403 —
carried on AuthError.forbidden so the API can map the status (ADR-0007).

core/ depends only on the IdentityVerifier Protocol; the google-auth dependency
and Google's token shape stay inside this package.
"""

import asyncio

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from brokerops_core.ports.identity import AuthError, Principal

# Google mints ID tokens with one of these two issuers; reject anything else
# even if the signature and audience check out.
_GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class GoogleOIDCVerifier:
    def __init__(
        self,
        client_id: str,
        allowed_domain: str | None = None,
        allowed_emails: frozenset[str] | None = None,
    ) -> None:
        self._client_id = client_id
        self._allowed_domain = allowed_domain or None
        # Compare case-insensitively; Google emails are already lowercase but a
        # hand-edited allowlist may not be.
        self._allowed_emails = (
            frozenset(e.strip().lower() for e in allowed_emails if e.strip())
            if allowed_emails
            else None
        )
        # One transport instance; google-auth caches Google's certs on it.
        self._transport = google_requests.Request()

    async def verify(self, token: str | None) -> Principal:
        if not token:
            raise AuthError("missing bearer token")
        # verify_oauth2_token is synchronous and may refresh Google's certs over
        # the network; keep it off the event loop.
        try:
            claims = await asyncio.to_thread(
                google_id_token.verify_oauth2_token,
                token,
                self._transport,
                self._client_id,
            )
        except ValueError as exc:  # bad signature, audience, or expiry
            raise AuthError(f"invalid id token: {exc}") from exc

        if claims.get("iss") not in _GOOGLE_ISSUERS:
            raise AuthError("unexpected token issuer")

        email = str(claims.get("email", "")).lower()
        if not email or not claims.get("email_verified", False):
            raise AuthError("token has no verified email")

        self._check_allowlist(email, claims.get("hd"))

        return Principal(
            subject=str(claims["sub"]),
            email=email,
            name=str(claims.get("name", "")),
            verified=True,
        )

    def _check_allowlist(self, email: str, hosted_domain: object) -> None:
        if self._allowed_emails is not None and email in self._allowed_emails:
            return
        if self._allowed_domain is not None:
            domain = email.rpartition("@")[2]
            if hosted_domain == self._allowed_domain or domain == self._allowed_domain:
                return
        # No allowlist configured at all → any verified Google account is in.
        if self._allowed_emails is None and self._allowed_domain is None:
            return
        raise AuthError(f"{email} is not permitted", forbidden=True)
