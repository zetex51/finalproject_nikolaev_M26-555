"""Domain models and business use cases for ValutaTrade Hub."""

from valutatrade_hub.core.currencies import (
    CryptoCurrency,
    Currency,
    FiatCurrency,
    get_currency,
    register_currency,
    supported_currency_codes,
)
from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    CurrencyNotFoundError,
    InsufficientFundsError,
    InvalidCredentialsError,
    NotAuthenticatedError,
    RateUnavailableError,
    UnknownCurrencyError,
    UserAlreadyExistsError,
    UserNotFoundError,
    WalletNotFoundError,
)
from valutatrade_hub.core.models import Portfolio, User, Wallet
from valutatrade_hub.core.storage import JsonStorage
from valutatrade_hub.core.usecases import (
    PortfolioReport,
    RateQuote,
    TradeReport,
    WalletService,
    buy_currency,
    sell_currency,
)
from valutatrade_hub.core.utils import convert_currency

__all__ = [
    "ApiRequestError",
    "Currency",
    "CurrencyNotFoundError",
    "CryptoCurrency",
    "FiatCurrency",
    "InsufficientFundsError",
    "InvalidCredentialsError",
    "JsonStorage",
    "NotAuthenticatedError",
    "Portfolio",
    "PortfolioReport",
    "RateQuote",
    "RateUnavailableError",
    "TradeReport",
    "UnknownCurrencyError",
    "User",
    "UserAlreadyExistsError",
    "UserNotFoundError",
    "Wallet",
    "WalletNotFoundError",
    "WalletService",
    "buy_currency",
    "convert_currency",
    "get_currency",
    "register_currency",
    "sell_currency",
    "supported_currency_codes",
]
