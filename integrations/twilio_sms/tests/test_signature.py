"""Twilio X-Twilio-Signature validation, proven both ways.

The known-answer vector below is pinned against the *official* Twilio SDK:
`twilio.request_validator.RequestValidator("12345").compute_signature(URL, PARAMS)`
produces exactly this base64 string (verified against twilio-python at build
time). That pins our from-scratch implementation to Twilio's algorithm, not
merely to itself, without taking the SDK on as a dependency.
"""

from brokerops_twilio_sms.signature import compute_signature, signature_is_valid

AUTH_TOKEN = "12345"
URL = "https://mycompany.com/myapp.php?foo=1&bar=2"
PARAMS = {
    "CallSid": "CA1234567890ABCDE",
    "Caller": "+14158675310",
    "Digits": "1234",
    "From": "+14158675310",
    "To": "+18005551212",
}
# twilio-python RequestValidator's answer for the exact inputs above.
SDK_SIGNATURE = "GvWf1cFY/Q7PnoempGyD5oXAezc="


def test_matches_the_official_twilio_validator() -> None:
    assert compute_signature(AUTH_TOKEN, URL, PARAMS) == SDK_SIGNATURE
    assert signature_is_valid(AUTH_TOKEN, URL, PARAMS, SDK_SIGNATURE)


def test_rejects_a_tampered_or_missing_signature() -> None:
    assert not signature_is_valid(AUTH_TOKEN, URL, PARAMS, "forged==")
    assert not signature_is_valid(AUTH_TOKEN, URL, PARAMS, "")


def test_rejects_tampered_params_or_url() -> None:
    tampered = {**PARAMS, "Digits": "9999"}
    assert not signature_is_valid(AUTH_TOKEN, URL, tampered, SDK_SIGNATURE)
    assert not signature_is_valid(
        AUTH_TOKEN, "https://evil.example/myapp.php?foo=1&bar=2", PARAMS, SDK_SIGNATURE
    )


def test_rejects_the_wrong_auth_token() -> None:
    assert not signature_is_valid("54321", URL, PARAMS, SDK_SIGNATURE)
