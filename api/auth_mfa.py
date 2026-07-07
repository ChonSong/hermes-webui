"""
TOTP Multi-Factor Authentication for Hermes WebUI.

Provides server-side TOTP (RFC 6238) secret management, code verification,
provisioning-URI generation, and a signed temp-token for the two-step login
flow (password → TOTP code → session cookie).

Dependencies: ``pyotp``, ``qrcode[pil]``
"""
import base64
import hashlib
import hmac
import io
import logging
import secrets
import time

from api.config import load_settings, save_settings

logger = logging.getLogger(__name__)

# Settings keys
_TOTP_SECRET_KEY = "totp_secret"
_TOTP_ENABLED_KEY = "totp_enabled"

# Clock-drift tolerance for TOTP verification (number of 30 s intervals)
_TOTP_WINDOW = 1  # ±1 interval → ±30 s

# Temp token TTL (seconds) — how long the user has to enter their TOTP code
# after submitting a valid password.
_MFA_TEMP_TOKEN_TTL = 180  # 3 minutes


# ── Signing key (reuses the auth module's PBKDF2 key to avoid a second key) ──

def _signing_key() -> bytes:
    """Return the PBKDF2 key shared with ``api.auth``."""
    from api.auth import _pbkdf2_key  # deferred import to avoid bootstrap cycle
    return _pbkdf2_key()


# ── Secret management ────────────────────────────────────────────────────────

def generate_totp_secret() -> str:
    """Generate a new RFC 4648 base32-encoded TOTP secret (160 bit)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def _get_totp_secret_raw() -> str | None:
    """Read the stored TOTP secret from settings.json (may also be disabled)."""
    return load_settings().get(_TOTP_SECRET_KEY)


# ── Code verification ────────────────────────────────────────────────────────

def verify_totp_code(code: str) -> bool:
    """Verify a 6-digit TOTP code against the currently stored secret.

    Accepts ±``_TOTP_WINDOW`` intervals of clock drift.
    Returns ``False`` when no secret is configured, the code is malformed,
    or verification fails.
    """
    secret = _get_totp_secret_raw()
    return verify_totp_code_for_secret(secret, code)


def verify_totp_code_for_secret(secret: str | None, code: str) -> bool:
    """Verify a TOTP code against a specific secret (used during setup)."""
    if not secret:
        return False
    # Sanity: reject non-numeric / wrong-length input before calling pyotp
    code = (code or "").strip()
    if not code.isdigit() or len(code) != 6:
        return False
    try:
        import pyotp

        totp = pyotp.TOTP(secret)
        return totp.verify(code, valid_window=_TOTP_WINDOW)
    except Exception as exc:
        logger.debug("TOTP verification error: %s", exc)
        return False


# ── Enable / disable ─────────────────────────────────────────────────────────

def is_mfa_enabled() -> bool:
    """Return ``True`` when MFA is turned on **and** a secret exists."""
    s = load_settings()
    secret = s.get(_TOTP_SECRET_KEY)
    enabled = s.get(_TOTP_ENABLED_KEY, False)
    return bool(secret and enabled)


def enable_mfa(secret: str) -> None:
    """Persist the TOTP secret and set the enabled flag."""
    save_settings({
        _TOTP_SECRET_KEY: secret,
        _TOTP_ENABLED_KEY: True,
    })


def disable_mfa() -> None:
    """Remove the TOTP secret and clear the enabled flag."""
    save_settings({
        _TOTP_SECRET_KEY: None,
        _TOTP_ENABLED_KEY: False,
    })


# ── Provisioning URI / QR code ───────────────────────────────────────────────

def provisioning_uri(secret: str, label: str = "Hermes WebUI") -> str:
    """Return an ``otpauth://`` URI for the given secret.

    *label* is what the authenticator app shows (e.g. the user's email).
    """
    import pyotp

    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=label, issuer_name="Hermes WebUI")


def generate_qr_code_data_url(uri: str) -> str:
    """Return a ``data:image/png;base64,…`` URL for the provisioning *uri*.

    Returns an empty string when QR generation fails (e.g. missing PIL).
    """
    try:
        import qrcode

        qr = qrcode.QRCode(border=2, box_size=8)
        qr.add_data(uri)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#1a1a2e", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception as exc:
        logger.debug("QR code generation failed: %s", exc)
        return ""


# ── Temp token for two‑step login ────────────────────────────────────────────
# A signed, time-limited token carried from the password step to the TOTP step.
# No server-side session state required — verified purely through HMAC.

def create_mfa_temp_token(client_ip: str) -> str:
    """Create a signed, short-lived token for the MFA verification step.

    The token encodes *client_ip* and an expiry timestamp.  It is HMAC‑signed
    so the verify step can trust it without server‑side session storage.
    """
    expiry = int(time.time()) + _MFA_TEMP_TOKEN_TTL
    payload = f"{client_ip}:{expiry}".encode()
    sig = hmac.new(_signing_key(), payload, hashlib.sha256).hexdigest()
    token_b64 = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{token_b64}.{sig}"


def verify_mfa_temp_token(token: str, client_ip: str) -> bool:
    """Verify a signed MFA temp token.

    Returns ``True`` iff the token is valid, not expired, and bound to the
    given *client_ip*.
    """
    if not token or "." not in token:
        return False
    try:
        payload_b64, sig = token.rsplit(".", 1)
        # Restore padding stripped by urlsafe_b64encode
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = base64.urlsafe_b64decode(payload_b64)

        # Verify signature
        expected_sig = hmac.new(_signing_key(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return False

        # Parse payload: "client_ip:expiry"
        text = payload.decode("utf-8")
        colon = text.rfind(":")
        if colon < 1:
            return False
        token_ip = text[:colon]
        expiry = int(text[colon + 1:])

        if token_ip != client_ip:
            return False
        if time.time() > expiry:
            return False
        return True
    except (ValueError, Exception) as exc:
        logger.debug("MFA temp token verification error: %s", exc)
        return False
