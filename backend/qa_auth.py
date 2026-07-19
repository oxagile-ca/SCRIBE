"""Server-side Cognito auth for unattended QA runs.

Uses the onboarding-stored test credentials (``environments.testAuth`` →
username + ``${secret:TEST_LOGIN_PASSWORD}``) to mint a fresh Cognito id_token via
``USER_PASSWORD_AUTH``, refresh it, and build the ``oidc.user`` localStorage blob
the SPA expects — so a headless QA browser can be authenticated without a human
logging in.

Security: the password and refresh_token never leave this process except to the
Cognito IdP endpoint. Nothing here logs token or password values.
"""
import os
import re
import time

import httpx

import instance_config as ic

# Cognito params for the test-login mint are per-customer, NOT hardcoded to any one
# app. They come from the instance config's environments.testAuth.cognito block
# (env vars override for local runs); a neutral region default + empty pool/client
# when unconfigured, so importing this module never assumes a specific customer.
_cognito_cfg = ((ic.load_instance_config() or {}).get("environments") or {}).get("testAuth", {}).get("cognito") or {}
COGNITO_REGION = os.environ.get("COGNITO_REGION") or _cognito_cfg.get("region") or "us-east-1"
COGNITO_POOL = os.environ.get("COGNITO_POOL") or _cognito_cfg.get("userPoolId") or ""
# SPA app client id (from the SPA's oidc.user storage key / Postman CLIENT_ID).
COGNITO_CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID") or _cognito_cfg.get("clientId") or ""
COGNITO_ISSUER = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_POOL}"
_IDP_URL = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"
_HEADERS = {
    "Content-Type": "application/x-amz-json-1.1",
    "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
}


def _resolve_secret(v):
    """Resolve a ``${secret:NAME}`` reference against the environment (.secrets.env)."""
    if v is None:
        return None
    m = re.match(r"^\$\{secret:([^}]+)\}$", str(v).strip())
    return os.environ.get(m.group(1)) if m else v


def load_test_credentials():
    """(username, password) from instance config + .secrets.env. None if absent."""
    cfg = ic.load_instance_config() or {}
    ic.load_secrets_env()
    auth = (cfg.get("environments") or {}).get("testAuth") or {}
    return auth.get("username"), _resolve_secret(auth.get("password"))


def _initiate_auth_body(flow, params):
    return {"AuthFlow": flow, "ClientId": COGNITO_CLIENT_ID, "AuthParameters": params}


def parse_auth_result(data):
    """Pull tokens out of a Cognito InitiateAuth response. None if no IdToken."""
    res = (data or {}).get("AuthenticationResult") or {}
    if not res.get("IdToken"):
        return None
    return {
        "id_token": res["IdToken"],
        "access_token": res.get("AccessToken"),
        "refresh_token": res.get("RefreshToken"),  # absent on REFRESH_TOKEN_AUTH
        "expires_in": res.get("ExpiresIn", 3600),
    }


async def _initiate(flow, params):
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post(_IDP_URL, headers=_HEADERS, json=_initiate_auth_body(flow, params))
    data = r.json()
    if r.status_code != 200:
        # Surface AWS error type but never the credential.
        raise RuntimeError(f"cognito {flow} failed: {data.get('__type')}: {(data.get('message') or '')[:120]}")
    toks = parse_auth_result(data)
    if not toks:
        raise RuntimeError(f"cognito {flow}: no IdToken (challenge={data.get('ChallengeName')})")
    return toks


async def mint_tokens(username=None, password=None):
    """USER_PASSWORD_AUTH → {id_token, access_token, refresh_token, expires_in}."""
    if username is None or password is None:
        username, password = load_test_credentials()
    if not username or not password:
        raise RuntimeError("no environments.testAuth username/password configured")
    return await _initiate("USER_PASSWORD_AUTH", {"USERNAME": username, "PASSWORD": password})


async def refresh_tokens(refresh_token):
    """REFRESH_TOKEN_AUTH → fresh id/access token (no new refresh_token)."""
    return await _initiate("REFRESH_TOKEN_AUTH", {"REFRESH_TOKEN": refresh_token})


def build_oidc_storage(tokens, *, profile=None, now=None):
    """The (localStorage key, value) the SPA reads, for headless browser injection.

    Omits the refresh_token by default unless present in ``tokens`` — callers that
    inject into a browser they don't fully trust should drop it first.
    """
    now = int(now if now is not None else time.time())
    key = f"oidc.user:{COGNITO_ISSUER}:{COGNITO_CLIENT_ID}"
    value = {
        "id_token": tokens["id_token"],
        "access_token": tokens.get("access_token"),
        "token_type": "Bearer",
        "scope": "profile openid email",
        "expires_at": now + int(tokens.get("expires_in", 3600)),
        "profile": profile or {},
    }
    if tokens.get("refresh_token"):
        value["refresh_token"] = tokens["refresh_token"]
    return key, value
