"""Unit tests for qa_auth pure logic (no network)."""
import qa_auth


def test_parse_auth_result_extracts_tokens():
    data = {"AuthenticationResult": {
        "IdToken": "id", "AccessToken": "ac", "RefreshToken": "rf", "ExpiresIn": 3600}}
    assert qa_auth.parse_auth_result(data) == {
        "id_token": "id", "access_token": "ac", "refresh_token": "rf", "expires_in": 3600}


def test_parse_auth_result_none_without_idtoken():
    assert qa_auth.parse_auth_result({"ChallengeName": "NEW_PASSWORD_REQUIRED"}) is None
    assert qa_auth.parse_auth_result({}) is None
    # refresh responses omit the refresh_token — that's fine
    r = qa_auth.parse_auth_result({"AuthenticationResult": {"IdToken": "id", "ExpiresIn": 3600}})
    assert r["id_token"] == "id" and r["refresh_token"] is None


def test_initiate_auth_body_shape():
    b = qa_auth._initiate_auth_body("USER_PASSWORD_AUTH", {"USERNAME": "u", "PASSWORD": "p"})
    assert b["AuthFlow"] == "USER_PASSWORD_AUTH"
    assert b["ClientId"] == qa_auth.COGNITO_CLIENT_ID
    assert b["AuthParameters"] == {"USERNAME": "u", "PASSWORD": "p"}


def test_resolve_secret(monkeypatch):
    monkeypatch.setenv("TEST_LOGIN_PASSWORD", "sekret")
    assert qa_auth._resolve_secret("${secret:TEST_LOGIN_PASSWORD}") == "sekret"
    assert qa_auth._resolve_secret("plain") == "plain"
    assert qa_auth._resolve_secret(None) is None


def test_build_oidc_storage_key_and_expiry():
    key, val = qa_auth.build_oidc_storage(
        {"id_token": "id", "access_token": "ac", "refresh_token": "rf", "expires_in": 3600},
        profile={"sub": "x"}, now=1000)
    assert key == f"oidc.user:{qa_auth.COGNITO_ISSUER}:{qa_auth.COGNITO_CLIENT_ID}"
    assert val["id_token"] == "id"
    assert val["access_token"] == "ac"
    assert val["token_type"] == "Bearer"
    assert val["expires_at"] == 1000 + 3600
    assert val["refresh_token"] == "rf"
    assert val["profile"] == {"sub": "x"}


def test_build_oidc_storage_omits_refresh_when_absent():
    _, val = qa_auth.build_oidc_storage({"id_token": "id", "expires_in": 3600}, now=0)
    assert "refresh_token" not in val
