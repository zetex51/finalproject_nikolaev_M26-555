"""Business operations for buying and selling portfolio currencies."""

from collections.abc import Mapping

from valutatrade_hub.core.models import Portfolio, Wallet
from valutatrade_hub.core.utils import (
    normalize_currency_code,
    resolve_exchange_rates,
    validate_amount,
)


def _usd_wallet(portfolio: Portfolio) -> Wallet:
    try:
        return portfolio.get_wallet("USD")
    except KeyError as error:
        raise ValueError("Для сделки сначала добавьте USD-кошелёк.") from error


def buy_currency(
    portfolio: Portfolio,
    currency_code: str,
    amount: int | float,
    exchange_rates: Mapping[str, int | float] | None = None,
) -> float:
    """Buy a quantity of currency, debit USD, and return the USD cost."""
    code = normalize_currency_code(currency_code)
    quantity = validate_amount(amount)
    if code == "USD":
        raise ValueError("USD-кошелёк нельзя купить за USD.")

    rates = resolve_exchange_rates(exchange_rates)
    if code not in rates:
        raise ValueError(f"Нет курса для валюты {code}.")
    usd_wallet = _usd_wallet(portfolio)
    cost = validate_amount(quantity * rates[code] / rates["USD"])
    if cost > usd_wallet.balance:
        raise ValueError("Недостаточно средств в USD-кошельке.")

    target_wallet = portfolio.wallets.get(code)
    target_balance = validate_amount(
        (target_wallet.balance if target_wallet is not None else 0.0) + quantity,
        allow_zero=True,
    )
    usd_balance = validate_amount(usd_wallet.balance - cost, allow_zero=True)
    if target_wallet is None:
        target_wallet = portfolio.add_currency(code)
    usd_wallet.balance = usd_balance
    target_wallet.balance = target_balance
    return cost


def sell_currency(
    portfolio: Portfolio,
    currency_code: str,
    amount: int | float,
    exchange_rates: Mapping[str, int | float] | None = None,
) -> float:
    """Sell a quantity of currency, credit USD, and return the USD proceeds."""
    code = normalize_currency_code(currency_code)
    quantity = validate_amount(amount)
    if code == "USD":
        raise ValueError("Нельзя продать USD за USD.")

    rates = resolve_exchange_rates(exchange_rates)
    if code not in rates:
        raise ValueError(f"Нет курса для валюты {code}.")
    try:
        source_wallet = portfolio.get_wallet(code)
    except KeyError as error:
        raise ValueError(f"Кошелёк {code} не найден.") from error

    proceeds = validate_amount(quantity * rates[code] / rates["USD"])
    usd_wallet = portfolio.wallets.get("USD")
    usd_balance = validate_amount(
        (usd_wallet.balance if usd_wallet is not None else 0.0) + proceeds,
        allow_zero=True,
    )
    if quantity > source_wallet.balance:
        raise ValueError(f"Недостаточно средств в кошельке {code}.")
    source_balance = validate_amount(source_wallet.balance - quantity, allow_zero=True)
    if usd_wallet is None:
        usd_wallet = portfolio.add_currency("USD")
    source_wallet.balance = source_balance
    usd_wallet.balance = usd_balance
    return proceeds
