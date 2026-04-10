"""Tests for dashboard auth — signed cookies."""
import time

import pytest

from aiproxy.auth.dashboard_auth import (
    issue_session_token,
    verify_session_token,
)


def test_valid_token_roundtrips() -> None:
    token = issue_session_token(secret="s3cret", ttl_seconds=3600)
    result = verify_session_token(token, secret="s3cret")
    assert result.valid is True
    assert result.expired is False
    assert result.payload is not None
    assert "exp" in result.payload
    assert "iat" in result.payload


def test_wrong_secret_fails() -> None:
    token = issue_session_token(secret="s3cret", ttl_seconds=3600)
    result = verify_session_token(token, secret="different")
    assert result.valid is False


def test_expired_token_flagged() -> None:
    # Issue a token that expired 1 second ago
    token = issue_session_token(secret="s3cret", ttl_seconds=-1)
    result = verify_session_token(token, secret="s3cret")
    assert result.valid is False
    assert result.expired is True


def test_malformed_token_fails() -> None:
    result = verify_session_token("not.a.jwt", secret="s3cret")
    assert result.valid is False


def test_none_token_fails() -> None:
    result = verify_session_token(None, secret="s3cret")
    assert result.valid is False
