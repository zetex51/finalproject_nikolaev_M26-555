"""Validation and password helpers shared by core models and use cases."""

import hashlib
import hmac
import math
import re
import secrets
from collections.abc import Mapping
from types import MappingProxyType

PASSWORD_MIN_LENGTH = 4
_CURRENCY_CODE_PATTERN = re.compile(r"^[A-Z0-9]{2,5}$")

# Demonstration values expressed as USD per one unit of the named currency.
# A future parser service can supply live rates to the use cases instead.
DEFAULT_EXCHANGE_RATES = MappingProxyType(
    {
        "USD": 1.0,
        "EUR": 1.08,
        "RUB": 0.011,
        "BTC": 65000.0,
        "ETH": 3000.0,
    }
)


def validate_password(password: str) -> str:
    """Return a valid password or raise a clear validation error."""
    if not isinstance(password, str):
        raise TypeError("Пароль должен быть строкой.")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Пароль должен быть не короче {PASSWORD_MIN_LENGTH} символов")
    return password


def generate_salt() -> str:
    """Generate a cryptographically random per-user salt."""
    return secrets.token_hex(16)


def hash_password(password: str, salt: str) -> str:
    """Hash a password and salt with SHA-256 for this educational project."""
    password = validate_password(password)
    if not isinstance(salt, str) or not salt:
        raise ValueError("Соль должна быть непустой строкой.")
    return hashlib.sha256(f"{password}{salt}".encode("utf-8")).hexdigest()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    """Compare a candidate password hash without leaking timing information."""
    if not isinstance(password, str) or not isinstance(salt, str):
        return False
    if not isinstance(expected_hash, str) or not expected_hash:
        return False
    try:
        candidate_hash = hashlib.sha256(f"{password}{salt}".encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(candidate_hash, expected_hash)


def normalize_currency_code(currency_code: str) -> str:
    """Normalize and validate a two-to-five-character currency ticker."""
    if not isinstance(currency_code, str):
        raise TypeError("Код валюты должен быть строкой.")
    normalized = currency_code.strip().upper()
    if not normalized.isascii() or not _CURRENCY_CODE_PATTERN.fullmatch(normalized):
        raise ValueError("Код валюты должен содержать от 2 до 5 букв или цифр.")
    return normalized


def validate_amount(amount: int | float, *, allow_zero: bool = False) -> float:
    """Validate a finite numeric amount and return it as a float."""
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise TypeError("Сумма должна быть числом.")
    try:
        normalized = float(amount)
    except OverflowError as error:
        raise ValueError("Сумма должна быть конечным числом.") from error
    if not math.isfinite(normalized):
        raise ValueError("Сумма должна быть конечным числом.")
    if normalized < 0 or (normalized == 0 and not allow_zero):
        qualifier = "неотрицательной" if allow_zero else "положительной"
        raise ValueError(f"Сумма должна быть {qualifier}.")
    return normalized


def resolve_exchange_rates(
    exchange_rates: Mapping[str, int | float] | None = None,
) -> dict[str, float]:
    """Return defaults overridden by supplied USD-denominated rates."""
    resolved = dict(DEFAULT_EXCHANGE_RATES)
    if exchange_rates is None:
        return resolved
    if not isinstance(exchange_rates, Mapping):
        raise TypeError("Курсы валют должны быть отображением кодов и значений.")

    for currency_code, rate in exchange_rates.items():
        code = normalize_currency_code(currency_code)
        resolved[code] = validate_amount(rate)
    return resolved


def convert_currency(
    amount: int | float,
    from_currency: str,
    to_currency: str,
    exchange_rates: Mapping[str, int | float] | None = None,
) -> float:
    """Convert an amount using rates expressed as USD per currency unit."""
    quantity = validate_amount(amount, allow_zero=True)
    source = normalize_currency_code(from_currency)
    target = normalize_currency_code(to_currency)
    rates = resolve_exchange_rates(exchange_rates)
    if source not in rates:
        raise ValueError(f"Нет курса для валюты {source}.")
    if target not in rates:
        raise ValueError(f"Нет курса для валюты {target}.")
    return validate_amount(quantity * rates[source] / rates[target], allow_zero=True)
