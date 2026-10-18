"""Currency types and the application's currency registry."""

from abc import ABC, abstractmethod
from copy import copy

from valutatrade_hub.core.exceptions import CurrencyNotFoundError
from valutatrade_hub.core.utils import normalize_currency_code, validate_amount


class Currency(ABC):
    """Abstract currency with validated public name and ticker properties."""

    def __init__(self, name: str, code: str) -> None:
        self.name = name
        self.code = code

    @property
    def name(self) -> str:
        """Human-readable currency name."""
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("Название валюты должно быть строкой.")
        normalized = value.strip()
        if not normalized:
            raise ValueError("Название валюты не может быть пустым.")
        self._name = normalized

    @property
    def code(self) -> str:
        """Uppercase ticker without spaces, between two and five characters."""
        return self._code

    @code.setter
    def code(self, value: str) -> None:
        self._code = normalize_currency_code(value)

    @abstractmethod
    def get_display_info(self) -> str:
        """Return a description suitable for display or logging."""


class FiatCurrency(Currency):
    """A fiat currency issued by a country or monetary zone."""

    def __init__(self, name: str, code: str, issuing_country: str) -> None:
        super().__init__(name, code)
        self.issuing_country = issuing_country

    @property
    def issuing_country(self) -> str:
        """Country or monetary zone that issues the currency."""
        return self._issuing_country

    @issuing_country.setter
    def issuing_country(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("Страна или зона эмиссии должна быть строкой.")
        normalized = value.strip()
        if not normalized:
            raise ValueError("Страна или зона эмиссии не может быть пустой.")
        self._issuing_country = normalized

    def get_display_info(self) -> str:
        """Return the ticker, name, and issuing country or zone."""
        return f"[FIAT] {self.code} — {self.name} (Issuing: {self.issuing_country})"


class CryptoCurrency(Currency):
    """A cryptocurrency with algorithm and latest known market capitalization."""

    def __init__(
        self,
        name: str,
        code: str,
        algorithm: str,
        market_cap: int | float,
    ) -> None:
        super().__init__(name, code)
        self.algorithm = algorithm
        self.market_cap = market_cap

    @property
    def algorithm(self) -> str:
        """Consensus or hashing algorithm associated with the currency."""
        return self._algorithm

    @algorithm.setter
    def algorithm(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("Алгоритм должен быть строкой.")
        normalized = value.strip()
        if not normalized:
            raise ValueError("Алгоритм не может быть пустым.")
        self._algorithm = normalized

    @property
    def market_cap(self) -> float:
        """Latest known market capitalization, or zero when unavailable."""
        return self._market_cap

    @market_cap.setter
    def market_cap(self, value: int | float) -> None:
        self._market_cap = validate_amount(value, allow_zero=True)

    def get_display_info(self) -> str:
        """Return the ticker, name, algorithm, and compact capitalization."""
        mantissa, exponent = f"{self.market_cap:.2e}".split("e")
        compact_market_cap = f"{mantissa}e{int(exponent)}"
        return (
            f"[CRYPTO] {self.code} — {self.name} "
            f"(Algo: {self.algorithm}, MCAP: {compact_market_cap})"
        )


_CURRENCY_REGISTRY: dict[str, Currency] = {
    "USD": FiatCurrency("US Dollar", "USD", "United States"),
    "EUR": FiatCurrency("Euro", "EUR", "Eurozone"),
    "RUB": FiatCurrency("Russian Ruble", "RUB", "Russia"),
    "BTC": CryptoCurrency("Bitcoin", "BTC", "SHA-256", 1.12e12),
    "ETH": CryptoCurrency("Ethereum", "ETH", "Ethash", 0.0),
}


def get_currency(code: str) -> Currency:
    """Return a copy of the registered currency for a ticker.

    Unknown but syntactically valid tickers raise ``CurrencyNotFoundError``.
    """
    try:
        normalized_code = normalize_currency_code(code)
    except (TypeError, ValueError) as error:
        raise CurrencyNotFoundError(str(code)) from error
    try:
        return copy(_CURRENCY_REGISTRY[normalized_code])
    except KeyError as error:
        raise CurrencyNotFoundError(normalized_code) from error


def register_currency(currency: Currency, *, replace: bool = False) -> None:
    """Add a currency to the registry, optionally replacing an existing code."""
    if not isinstance(currency, Currency):
        raise TypeError("В реестр можно добавить только объект Currency.")
    if currency.code in _CURRENCY_REGISTRY and not replace:
        raise ValueError(f"Валюта {currency.code} уже есть в реестре.")
    _CURRENCY_REGISTRY[currency.code] = copy(currency)


def supported_currency_codes() -> tuple[str, ...]:
    """Return the registered currency codes in registry display order."""
    return tuple(_CURRENCY_REGISTRY)
