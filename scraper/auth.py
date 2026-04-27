"""
KukuFM Authentication & Token Management
=========================================
Token is persisted at:
    metadata/captured_apis/token.json

Schema (updated):
{
    "token":                    "jwt eyJ...",   # Authorization header value (= jwt + access_token)
    "access_token":             "eyJ...",       # short-lived (~2–3 hrs)
    "refresh_token":            "eyJ...",       # long-lived (~30 days)
    "user_id":                  326912640,
    "unique_id":                "...",
    "phone":                    "+91XXXXXXXXXX",
    "access_token_expires_at":  "2026-04-27T13:48:25+00:00",
    "refresh_token_expires_at": "2026-05-27T12:21:46+00:00",
    "saved_at":                 "..."
}

Token Refresh Flow (reverse-engineered from mitmproxy):
-------------------------------------------------------
OTP login (first time):
  1. POST /api/v1.0/users/auth/send-otp/
       JSON {"phone_number": "+91...", "source": "phone_number"}
       → verification_id, otp_length

  2. POST /api/v1.0/users/auth/verify-otp/
       JSON {"email": phone, "otp": "XXXX", "phone_number": phone,
             "source": "phone_number", "verification_id": <id>}
       → Firebase custom token

  3. Exchange Firebase custom token for Firebase ID token via Firebase REST API:
       POST https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken
            ?key=AIzaSyBxpiXQ4LyOE2GtFCcZIRQbr4Z7V3KGwM0
       JSON {"token": <custom_token>, "returnSecureToken": true}
       → Firebase ID token (idToken)

  4. POST /api/v1.1/users/get-session-token/
       form-encoded: app_name=com.vlv.aravali.reels&os_type=android&
                     app_build_number=50706&installed_version=5.7.6&
                     firebase_token=<idToken>&is_upi_app_installed=false
       → access_token (2–3 hr) + refresh_token (30 day)

Silent refresh (no OTP needed, as long as refresh_token is valid):
  POST /api/v1.1/users/get-session-token/
       form-encoded: app_name=com.vlv.aravali.reels&os_type=android&
                     app_build_number=50706&installed_version=5.7.6&
                     access_token=<old>&refresh_token=<refresh>&
                     is_upi_app_installed=false
       → new access_token (refresh_token unchanged)

Public API
----------
load_token()                        -> str | None
save_token(data: dict)
get_auth_headers()                  -> dict           (auto-refreshes if expired)
refresh_token_silently()            -> str | None     (uses stored refresh_token)
refresh_token_via_otp(phone: str)   -> str            (waits for OTP input)
"""

import base64
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).resolve().parent.parent          # project root
TOKEN_FILE = BASE_DIR / "metadata" / "captured_apis" / "token.json"

# ── KukuFM API ─────────────────────────────────────────────────────────────────
API_BASE = "https://api.kukufm.com"
BASE_HEADERS = {
    "content-type":   "application/json",
    "client-country": "IN",
    "install-source": "google_play",
    "lang":           "english",
    "app-version":    "50706",
    "user-agent":     "kukufm-android-reels/5.7.6",
    "package-name":   "com.vlv.aravali.reels",
    "build-number":   "5070600",
    "accept-encoding": "gzip",
}

# App constants (from captured traffic)
APP_FORM_BASE = {
    "app_name":          "com.vlv.aravali.reels",
    "os_type":           "android",
    "app_build_number":  "50706",
    "installed_version": "5.7.6",
    "is_upi_app_installed": "false",
}

# Firebase project API key (from captured traffic)
FIREBASE_API_KEY = "AIzaSyBxpiXQ4LyOE2GtFCcZIRQbr4Z7V3KGwM0"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _decode_jwt_payload(jwt_str: str) -> dict:
    """Decode JWT payload (no signature verification)."""
    try:
        token = jwt_str.replace("jwt ", "", 1)
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        padded = parts[1] + "=="
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return {}


def _is_expired(token_str: str, buffer_secs: int = 60) -> bool:
    """Return True if the JWT is expired (or expiring within buffer_secs)."""
    payload = _decode_jwt_payload(token_str)
    exp = payload.get("exp")
    if not exp:
        return False
    return time.time() > (exp - buffer_secs)


# ── Token persistence ──────────────────────────────────────────────────────────

def load_token_data() -> dict:
    """Return the full token dict from TOKEN_FILE, or {}."""
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_token() -> str | None:
    """Return the stored JWT authorization string (e.g. 'jwt eyJ...') or None."""
    return load_token_data().get("token")


def save_token(data: dict):
    """Persist token data dict to TOKEN_FILE."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["saved_at"] = datetime.now(timezone.utc).isoformat()
    # Keep token = "jwt {access_token}" in sync
    if "access_token" in data and not data.get("token"):
        data["token"] = f"jwt {data['access_token']}"
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[auth] Token saved → {TOKEN_FILE}")


def _extract_token_from_traffic() -> str | None:
    """Fallback: read latest JWT from captured mitmproxy traffic."""
    traffic_file = BASE_DIR / "metadata" / "captured_apis" / "api_traffic.jsonl"
    last_token = None
    try:
        with open(traffic_file) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if "api.kukufm.com" not in d.get("host", ""):
                        continue
                    auth = d.get("req_hdrs", {}).get("authorization", "")
                    if auth.startswith("jwt "):
                        last_token = auth
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return last_token


# ── Silent token refresh ───────────────────────────────────────────────────────

def refresh_token_silently() -> str | None:
    """
    Exchange the stored refresh_token for a new access_token — no OTP needed.

    Uses:  POST /api/v1.1/users/get-session-token/
           form: app_name + access_token + refresh_token

    Valid as long as refresh_token hasn't expired (~30 days from last OTP login).

    Returns the new 'jwt eyJ...' string, or None on failure.
    """
    data = load_token_data()
    access_token  = data.get("access_token")
    refresh_token = data.get("refresh_token")

    if not refresh_token:
        print("[auth] No refresh_token stored — cannot silently refresh.")
        return None

    if _is_expired(refresh_token):
        print("[auth] refresh_token is expired — OTP login required.")
        return None

    print("[auth] Silently refreshing access_token using refresh_token…")
    headers = {k: v for k, v in BASE_HEADERS.items() if k != "content-type"}
    headers["content-type"] = "application/x-www-form-urlencoded"

    form = dict(APP_FORM_BASE)
    if access_token:
        form["access_token"] = access_token
    form["refresh_token"] = refresh_token

    try:
        resp = requests.post(
            f"{API_BASE}/api/v1.1/users/get-session-token/",
            data=form,
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        print(f"[auth] Silent refresh failed: {exc}")
        return None

    new_access  = body.get("access_token")
    new_refresh = body.get("refresh_token", refresh_token)

    if not new_access:
        print(f"[auth] Silent refresh — unexpected response: {body}")
        return None

    # Decode expiry timestamps
    acc_payload = _decode_jwt_payload(new_access)
    ref_payload = _decode_jwt_payload(new_refresh)

    new_data = dict(data)
    new_data.update({
        "token":                    f"jwt {new_access}",
        "access_token":             new_access,
        "refresh_token":            new_refresh,
        "access_token_expires_at":  datetime.fromtimestamp(
            acc_payload.get("exp", 0), tz=timezone.utc).isoformat() if acc_payload.get("exp") else None,
        "refresh_token_expires_at": datetime.fromtimestamp(
            ref_payload.get("exp", 0), tz=timezone.utc).isoformat() if ref_payload.get("exp") else None,
    })
    if body.get("user", {}).get("phone"):
        new_data["phone"] = body["user"]["phone"]
    save_token(new_data)

    exp_str = new_data.get("access_token_expires_at", "?")
    print(f"[auth] ✓ New access_token obtained (expires {exp_str})")
    return new_data["token"]


# ── Headers with auto-refresh ──────────────────────────────────────────────────

def get_auth_headers() -> dict:
    """
    Return request headers including Authorization.
    Auto-refreshes the access_token silently if it has expired.
    """
    headers = dict(BASE_HEADERS)

    data = load_token_data()
    access_token = data.get("access_token") or data.get("token", "").replace("jwt ", "", 1)

    # Auto-refresh if expired
    if access_token and _is_expired(access_token):
        print("[auth] access_token expired — attempting silent refresh…")
        new_jwt = refresh_token_silently()
        if new_jwt:
            headers["authorization"] = new_jwt
            return headers
        # Fall through to use whatever we have (server may still accept it)

    token = data.get("token") or _extract_token_from_traffic()
    if token:
        headers["authorization"] = token

    return headers


# ── OTP-based login ────────────────────────────────────────────────────────────

def refresh_token_via_otp(phone: str) -> str:
    """
    Full OTP login flow (verified from mitmproxy traffic capture).

    Flow:
      1. send-otp   → verification_id
      2. verify-otp → Firebase custom token
      3. Firebase REST API exchange → Firebase ID token
      4. get-session-token (form POST, firebase_token) → access_token + refresh_token

    Parameters
    ----------
    phone : str
        E.164 format, e.g. "+919876543210"

    Returns
    -------
    str  JWT string "jwt eyJ..." saved to disk.
    """
    session = requests.Session()
    session.headers.update(BASE_HEADERS)

    # ── Step 1: send-otp ──────────────────────────────────────────────────────
    print(f"[auth] Sending OTP to {phone}…")
    resp = session.post(
        f"{API_BASE}/api/v1.0/users/auth/send-otp/",
        json={"phone_number": phone, "source": "phone_number"},
        timeout=15,
    )
    resp.raise_for_status()
    otp_data = resp.json()

    # Rate-limit: wait 30 s and retry once if first attempt already fired
    if otp_data.get("error_code") == "WAIT_FOR_RESEND":
        print("[auth] Rate-limited — waiting 31 s then retrying…")
        time.sleep(31)
        resp = session.post(
            f"{API_BASE}/api/v1.0/users/auth/send-otp/",
            json={"phone_number": phone, "source": "phone_number"},
            timeout=15,
        )
        resp.raise_for_status()
        otp_data = resp.json()

    verification_id = otp_data.get("verification_id")
    otp_length      = otp_data.get("otp_length", 4)
    print(f"[auth] OTP sent (verification_id={verification_id}, length={otp_length})")

    # ── Step 2: verify-otp ───────────────────────────────────────────────────
    otp = input(f"[auth] Enter {otp_length}-digit OTP: ").strip()
    resp = session.post(
        f"{API_BASE}/api/v1.0/users/auth/verify-otp/",
        json={
            "email":           phone,
            "otp":             otp,
            "phone_number":    phone,
            "source":          "phone_number",
            "verification_id": verification_id,
        },
        timeout=15,
    )
    resp.raise_for_status()
    verify_data = resp.json()
    custom_token = verify_data.get("token")
    if not custom_token:
        raise RuntimeError(f"verify-otp: no token in response: {verify_data}")

    # ── Step 3: Firebase custom token → ID token ─────────────────────────────
    print("[auth] Exchanging Firebase custom token for ID token…")
    fb_resp = requests.post(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken",
        params={"key": FIREBASE_API_KEY},
        json={"token": custom_token, "returnSecureToken": True},
        timeout=15,
    )
    fb_resp.raise_for_status()
    firebase_id_token = fb_resp.json().get("idToken")
    if not firebase_id_token:
        raise RuntimeError(f"Firebase exchange failed: {fb_resp.json()}")

    # ── Step 4: get-session-token ─────────────────────────────────────────────
    form_hdrs = {k: v for k, v in BASE_HEADERS.items() if k != "content-type"}
    form_hdrs["content-type"] = "application/x-www-form-urlencoded"

    form = dict(APP_FORM_BASE)
    form["firebase_token"] = firebase_id_token

    resp = requests.post(
        f"{API_BASE}/api/v1.1/users/get-session-token/",
        data=form,
        headers=form_hdrs,
        timeout=15,
    )
    resp.raise_for_status()
    sess = resp.json()

    access_token  = sess.get("access_token")
    refresh_token = sess.get("refresh_token")
    if not access_token:
        raise RuntimeError(f"get-session-token: no access_token: {sess}")

    acc_payload = _decode_jwt_payload(access_token)
    ref_payload = _decode_jwt_payload(refresh_token or "")

    save_token({
        "token":                    f"jwt {access_token}",
        "access_token":             access_token,
        "refresh_token":            refresh_token,
        "user_id":                  acc_payload.get("user_id") or sess.get("user", {}).get("id"),
        "unique_id":                acc_payload.get("unique_id"),
        "phone":                    phone,
        "access_token_expires_at":  datetime.fromtimestamp(
            acc_payload.get("exp", 0), tz=timezone.utc).isoformat() if acc_payload.get("exp") else None,
        "refresh_token_expires_at": datetime.fromtimestamp(
            ref_payload.get("exp", 0), tz=timezone.utc).isoformat() if ref_payload.get("exp") else None,
    })

    print(f"[auth] ✓ Logged in as user_id={acc_payload.get('user_id')}  phone={phone}")
    return f"jwt {access_token}"


# ── Legacy alias ───────────────────────────────────────────────────────────────

def refresh_token_without_otp() -> str | None:
    """
    Attempt to get a valid token without OTP.
    Order: stored token → silent refresh → traffic fallback.
    """
    data = load_token_data()

    # 1. Try stored access_token (if not expired)
    access_token = data.get("access_token")
    if access_token and not _is_expired(access_token):
        print("[auth] Stored access_token is still valid.")
        return data.get("token") or f"jwt {access_token}"

    # 2. Silent refresh using refresh_token
    if data.get("refresh_token"):
        result = refresh_token_silently()
        if result:
            return result

    # 3. Fallback to traffic log
    traffic_token = _extract_token_from_traffic()
    if traffic_token:
        print("[auth] Using token from traffic log (fallback).")
        return traffic_token

    print("[auth] WARNING: No valid token available.")
    return None


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="KukuFM token management")
    ap.add_argument("--status",  action="store_true", help="Show current token status")
    ap.add_argument("--login",   metavar="PHONE",     help="Login via OTP (e.g. +919876543210)")
    ap.add_argument("--refresh", action="store_true", help="Silently refresh access_token using stored refresh_token")
    ap.add_argument("--guest",   action="store_true", help="Get best available token without OTP")
    args = ap.parse_args()

    if args.status:
        d = load_token_data()
        if d:
            print(json.dumps({k: v if k not in ("token","access_token","refresh_token")
                               else v[:60]+"…" for k, v in d.items()}, indent=2))
        else:
            print("No token stored.")

    elif args.login:
        refresh_token_via_otp(args.login)

    elif args.refresh:
        tok = refresh_token_silently()
        print(f"Token: {tok}")

    elif args.guest:
        tok = refresh_token_without_otp()
        print(f"Token: {tok}")

