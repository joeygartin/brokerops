"""Try each enabled verifier; the first to accept the bearer wins.

A deployment may enable several login methods (magic link, Google OIDC). Each
mints or carries a different bearer, but all resolve to a Principal through the
core IdentityVerifier Protocol. This composite lets one `verify()` accept any of
them: a session JWT and a Google ID token simply fail each other's verifier and
fall through to their own. If every verifier rejects, surface a 403 when any
said "forbidden" (a valid identity off the allowlist), else a 401.
"""

from brokerops_core.ports.identity import AuthError, IdentityVerifier, Principal


class CompositeIdentityVerifier:
    def __init__(self, verifiers: list[IdentityVerifier]) -> None:
        if not verifiers:
            raise ValueError("CompositeIdentityVerifier needs at least one verifier")
        self._verifiers = verifiers

    async def verify(self, token: str | None) -> Principal:
        forbidden: AuthError | None = None
        last: AuthError | None = None
        for verifier in self._verifiers:
            try:
                return await verifier.verify(token)
            except AuthError as exc:
                last = exc
                if exc.forbidden:
                    forbidden = exc
        raise forbidden or last or AuthError("no identity verifier accepted the token")
