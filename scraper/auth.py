"""
KukuFM Authentication & Token Management
=========================================
Token is persisted at:
    metadata/captured_apis/token.json

Schema:
{
    "token": "jwt eyJ...",     # full Authorization header value
    "user_id": 326912640,
    "unique_id": "...",
    "phone": "+91XXXXXXXXXX",  # optional
    "expires_at": "2026-04-27T10:04:26+00:00",
    "saved_at": "2026-04-27T08:04:26+00:00"
}

Public API
----------
load_token()                       -> str | None
save_token(data: dict)
get_auth_headers()                 -> dict
refresh_token_via_otp(phone: str)  -> str   (waits for OTP input)
refresh_token_without_otp()        -> str | None
"""

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

# ── KukuFM API base + headers template ────────────────────────────────────────
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


# ── Token persistence ──────────────────────────────────────────────────────────

def load_token() -> str | None:
    """Return the stored JWT token string (e.g. 'jwt eyJ...') or None."""
    try:
        with open(TOKEN_FILE) as f:
            data = json.load(f)
        return data.get("token")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_token(data: dict):
    """Persist token data dict to TOKEN_FILE."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    data.setdefault("saved_at", datetime.now(timezone.utc).isoformat())
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[auth] Token saved to {TOKEN_FILE}")


def _extract_token_from_traffic() -> str | None:
    """Fallback: read JWT from the captured mitmproxy traffic file."""
    traffic_file = BASE_DIR / "metadata" / "captured_apis" / "api_traffic.jsonl"
    try:
        with open(traffic_file) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if "api.kukufm.com" not in d.get("host", ""):
                        continue
                    for k, v in d.get("req_hdrs", {}).items():
                        if k.lower() == "authorization" and v.startswith("jwt "):
                            return v
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return None


def get_auth_headers() -> dict:
    """
    Return request headers including Authorization.
    Tries (in order):
      1. token.json
      2. captured traffic
    """
    headers = dict(BASE_HEADERS)
    token = load_token() or _extract_token_from_traffic()
    if token:
        headers["authorization"] = token
    return headers


# ── OTP-based login ────────────────────────────────────────────────────────────

def _send_otp(phone: str, session: requests.Session) -> bool:
    """
    POST /api/v1.0/users/auth/send-otp/ with mobile number.
    Returns True on success.
    """
    url = f"{API_BASE}/api/v1.0/users/auth/send-otp/"
    payload = {"mobile": phone}
    resp = session.post(url, json=payload, timeout=15)
    data = resp.json()
    success = resp.ok and data.get("status") not in ("error", "failed")
    if success:
        print(f"[auth] OTP sent to {phone}")
    else:
        print(f"[auth] OTP send failed: {data}")
    return success


def _verify_otp(phone: str, otp: str, session: requests.Session) -> dict | None:
    """
    POST /api/v1.0/users/auth/verify-otp/
    Returns response body dict or None.
    """
    url = f"{API_BASE}/api/v1.0/users/auth/verify-otp/"
    payload = {"mobile": phone, "otp": otp}
    resp = session.post(url, json=payload, timeout=15)
    if resp.ok:
        return resp.json()
    print(f"[auth] OTP verify error {resp.status_code}: {resp.text[:200]}")
    return None


def _get_session_token(user_token: str, session: requests.Session) -> dict | None:
    """
    POST /api/v1.1/users/get-session-token/
    Exchanges the short-lived verify-otp token for a longer-lived JWT.
    """
    url = f"{API_BASE}/api/v1.1/users/get-session-token/"
    hdrs = session.headers.copy()
    hdrs["Authorization"] = f"Token {user_token}"
    resp = session.post(url, headers=hdrs, json={}, timeout=15)
    if resp.ok:
        return resp.json()
    print(f"[auth] get-session-token error {resp.status_code}: {resp.text[:200]}")
    return None


def refresh_token_via_otp(phone: str) -> str:
    """
    Full OTP login flow.

    Strategy:
      - Request OTP up to 2 times (1st attempt = SMS; 2nd attempt = retry SMS).
      - After the 2nd attempt KukuFM will fall back to WhatsApp for the 3rd,
        so we stop requesting here and wait for the user to supply the code.
      - Prompts the user to input the received OTP.
      - Saves the resulting JWT to token.json.

    Parameters
    ----------
    phone : str
        Mobile number in E.164 format, e.g. "+919876543210" or "9876543210".

    Returns
    -------
    str
        The JWT token string (e.g. "jwt eyJ...") saved to disk.
    """
    session = requests.Session()
    session.headers.update(BASE_HEADERS)

    # --- Request OTP (max 2 SMS attempts to avoid WhatsApp fallback on 3rd) ---
    for attempt in range(1, 3):
        print(f"[auth] Requesting OTP (attempt {attempt}/2)…")
        ok = _send_otp(phone, session)
        if ok:
            break
        if attempt < 2:
            print("[auth] Retrying in 5 s…")
            time.sleep(5)

    # --- Prompt user for OTP ---
    otp = input("[auth] Enter OTP received (SMS or WhatsApp): ").strip()

    # --- Verify OTP ---
    verify_data = _verify_otp(phone, otp, session)
    if not verify_data:
        raise RuntimeError("OTP verification failed.")

    # Extract token from verify response
    raw_token = (
        verify_data.get("data", {}).get("token")
        or verify_data.get("token")
        or verify_data.get("data", {}).get("auth_token")
    )

    # Optionally exchange for a session token
    if raw_token:
        sess_data = _get_session_token(raw_token, session)
        if sess_data:
            jwt = (
                sess_data.get("data", {}).get("token")
                or sess_data.get("token")
                or raw_token
            )
        else:
            jwt = raw_token
    else:
        raise RuntimeError(f"Could not find token in verify response: {verify_data}")

    # Prepend "jwt " prefix if missing
    if not jwt.startswith("jwt "):
        jwt = f"jwt {jwt}"

    # Extract expiry / user info
    token_payload = {}
    try:
        import base64 as _b64
        parts = jwt.split(".")
        if len(parts) >= 2:
            padded = parts[1] + "=="
            token_payload = json.loads(_b64.urlsafe_b64decode(padded))
    except Exception:
        pass

    save_token({
        "token":      jwt,
        "user_id":    token_payload.get("user_id"),
        "unique_id":  token_payload.get("unique_id"),
        "phone":      phone,
        "expires_at": datetime.fromtimestamp(
            token_payload.get("exp", 0), tz=timezone.utc
        ).isoformat() if token_payload.get("exp") else None,
    })

    print(f"[auth] Logged in as user_id={token_payload.get('user_id')}. Token saved.")
    return jwt


def refresh_token_without_otp() -> str | None:
    """
    Attempt to obtain a guest / anonymous session token without OTP.

    KukuFM allows browse-level access (series listings, episode metadata)
    without a user account.  This tries:
      1. Reuse existing token from token.json (if present & likely valid).
      2. POST /api/v1.1/users/get-session-token/ with only device info.
      3. Fallback to the token captured in the mitmproxy traffic file.

    Returns the JWT string or None.
    """
    # 1. Try stored token
    stored = load_token()
    if stored:
        print("[auth] Using stored token from token.json")
        return stored

    # 2. Try device-only session
    session = requests.Session()
    session.headers.update(BASE_HEADERS)
    device_id = str(uuid.uuid4())

    url = f"{API_BASE}/api/v1.1/users/get-session-token/"
    payload = {
        "device_id":       device_id,
        "device_platform": "android",
    }
    try:
        resp = session.post(url, json=payload, timeout=15)
        if resp.ok:
            data = resp.json()
            jwt = (
                data.get("data", {}).get("token")
                or data.get("token")
                or data.get("data", {}).get("auth_token")
            )
            if jwt:
                if not jwt.startswith("jwt "):
                    jwt = f"jwt {jwt}"
                save_token({"token": jwt, "user_id": None, "phone": None})
                print(f"[auth] Guest token obtained: {jwt[:40]}…")
                return jwt
    except Exception as exc:
        print(f"[auth] Guest session request failed: {exc}")

    # 3. Fallback to captured traffic
    traffic_token = _extract_token_from_traffic()
    if traffic_token:
        print("[auth] Falling back to token captured in traffic log.")
        save_token({
            "token":   traffic_token,
            "user_id": None,
            "phone":   None,
            "note":    "extracted from mitmproxy traffic",
        })
        return traffic_token

    print("[auth] WARNING: No token available. Requests may fail or be rate-limited.")
    return None


# ── CLI helper ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="KukuFM token management")
    ap.add_argument("--status",  action="store_true",  help="Show current token status")
    ap.add_argument("--login",   metavar="PHONE",      help="Login via OTP (phone number)")
    ap.add_argument("--guest",   action="store_true",  help="Get anonymous/guest token")
    args = ap.parse_args()

    if args.status:
        t = load_token()
        if t:
            print(f"Token present: {t[:50]}…")
            try:
                with open(TOKEN_FILE) as f:
                    print(json.dumps(json.load(f), indent=2))
            except Exception:
                pass
        else:
            print("No token stored.")

    elif args.login:
        refresh_token_via_otp(args.login)

    elif args.guest:
        tok = refresh_token_without_otp()
        print(f"Token: {tok}")

