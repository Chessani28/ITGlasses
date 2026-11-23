"""Password hashing with the standard library (hashlib.scrypt): no extra dependency.

The parameters are stored with every hash (users.pw_params), so the cost can be raised later and old hashes keep
verifying; needs_rehash() tells when a stored hash uses older parameters."""
import hashlib
import hmac
import secrets

N, R, P, DKLEN = 2 ** 15, 8, 1, 64
SALT_BYTES = 16
MIN_LENGTH, MAX_LENGTH = 14, 256
# Bounds for parameters read back from the database (a tampered row must not exhaust memory).
N_RANGE, R_RANGE, P_RANGE, DKLEN_RANGE = (2 ** 14, 2 ** 16), (1, 16), (1, 4), (32, 128)


class PasswordPolicyError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def params_text(n=N, r=R, p=P, dklen=DKLEN):
    return f"scrypt:n={n},r={r},p={p},dklen={dklen}"


def parse_params(text):
    """(n, r, p, dklen) from a stored parameter string; ValueError when it is not a bounded scrypt setting."""
    scheme, _, body = str(text or "").partition(":")
    if scheme != "scrypt":
        raise ValueError("unsupported password scheme")
    values = dict(item.split("=", 1) for item in body.split(",") if "=" in item)
    if set(values) != {"n", "r", "p", "dklen"}:
        raise ValueError("incomplete scrypt parameters")
    n, r, p, dklen = (int(values[key]) for key in ("n", "r", "p", "dklen"))
    for value, (low, high) in ((n, N_RANGE), (r, R_RANGE), (p, P_RANGE), (dklen, DKLEN_RANGE)):
        if not low <= value <= high:
            raise ValueError("scrypt parameters out of range")
    if n & (n - 1):
        raise ValueError("scrypt n must be a power of two")
    return n, r, p, dklen


def _scrypt(password, salt, n, r, p, dklen):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=dklen,
                          maxmem=max(64 * 1024 ** 2, 256 * n * r))


def hash_password(password):
    """(salt, digest, params) for a new password."""
    salt = secrets.token_bytes(SALT_BYTES)
    return salt, _scrypt(password, salt, N, R, P, DKLEN), params_text()


def verify_password(password, salt, digest, params):
    """Constant-time check; False (never an exception) for a wrong password or an unreadable stored hash."""
    try:
        candidate = _scrypt(password, bytes(salt), *parse_params(params))
    except (ValueError, TypeError, UnicodeError):
        return False
    return hmac.compare_digest(candidate, bytes(digest))


def needs_rehash(params):
    return params != params_text()


def check_policy(password, username=None):
    """14 to 256 characters, not only spaces, and not containing the username. No composition rules."""
    if not isinstance(password, str):
        raise PasswordPolicyError("password_invalid", "La contrasena no es valida.")
    if not MIN_LENGTH <= len(password) <= MAX_LENGTH:
        raise PasswordPolicyError("password_length",
                                  f"La contrasena debe tener entre {MIN_LENGTH} y {MAX_LENGTH} caracteres.")
    if not password.strip():
        raise PasswordPolicyError("password_blank", "La contrasena no puede ser solo espacios.")
    if username and username.lower() in password.lower():
        raise PasswordPolicyError("password_contains_username",
                                  "La contrasena no puede contener el nombre de usuario.")
