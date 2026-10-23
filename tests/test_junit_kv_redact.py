"""Key=value secret redaction in junit_redact."""

from junit_redact import redact_text


def test_kv_secret_assignment_redacted() -> None:
    raw = "curl -H 'Authorization: token=abc123secret' password=hide_me api_key=ZZZ"
    out = redact_text(raw)
    assert "abc123secret" not in out
    assert "hide_me" not in out
    assert "token=<redacted>" in out
    assert "password=<redacted>" in out
    assert "api_key=<redacted>" in out
