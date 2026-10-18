"""Application operations for accounts, portfolios, trades, and exchange rates."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from valutatrade_hub.core.exceptions import (
    InsufficientFundsError,
    InvalidCredentialsError,
    NotAuthenticatedError,
    RateUnavailableError,
    UnknownCurrencyError,
    UserAlreadyExistsError,
    UserNotFoundError,
    WalletNotFoundError,
)
from valutatrade_hub.core.models import Portfolio, User
from valutatrade_hub.core.storage import JsonStorage
from valutatrade_hub.core.utils import (
    DEFAULT_EXCHANGE_RATES,
    normalize_currency_code,
    resolve_exchange_rates,
    validate_amount,
)


@dataclass(frozen=True)
class RateQuote:
    """A rate, its pair, timestamp, and source."""

    from_currency: str
    to_currency: str
    rate: float
    updated_at: datetime
    source: str


@dataclass(frozen=True)
class TradeReport:
    """Details needed to display a completed buy or sell."""

    action: str
    currency_code: str
    amount: float
    previous_balance: float
    new_balance: float
    rate: float
    estimated_value_usd: float


@dataclass(frozen=True)
class PortfolioLine:
    """One wallet and its value in a selected base currency."""

    currency_code: str
    balance: float
    value: float


@dataclass(frozen=True)
class PortfolioReport:
    """A user's portfolio valued in the selected base currency."""

    username: str
    base_currency: str
    lines: tuple[PortfolioLine, ...]
    total_value: float


def buy_currency(
    portfolio: Portfolio,
    currency_code: str,
    amount: int | float,
    exchange_rates: Mapping[str, int | float] | None = None,
) -> float:
    """Add a purchased quantity and return its estimated value in USD."""
    code = normalize_currency_code(currency_code)
    quantity = validate_amount(amount)
    rates = resolve_exchange_rates(exchange_rates)
    if code not in rates:
        raise RateUnavailableError(f"Не удалось получить курс для {code}→USD.")

    target_wallet = portfolio.wallets.get(code)
    new_balance = validate_amount(
        (target_wallet.balance if target_wallet is not None else 0.0) + quantity,
        allow_zero=True,
    )
    if target_wallet is None:
        target_wallet = portfolio.add_currency(code)
    target_wallet.balance = new_balance
    return validate_amount(quantity * rates[code] / rates["USD"])


def sell_currency(
    portfolio: Portfolio,
    currency_code: str,
    amount: int | float,
    exchange_rates: Mapping[str, int | float] | None = None,
) -> float:
    """Remove a quantity, credit USD, and return the estimated proceeds."""
    code = normalize_currency_code(currency_code)
    quantity = validate_amount(amount)
    if code == "USD":
        raise ValueError("Нельзя продать USD за USD.")

    rates = resolve_exchange_rates(exchange_rates)
    if code not in rates:
        raise RateUnavailableError(f"Не удалось получить курс для {code}→USD.")
    try:
        source_wallet = portfolio.get_wallet(code)
    except KeyError as error:
        raise WalletNotFoundError(code) from error

    if quantity > source_wallet.balance:
        raise InsufficientFundsError(code, source_wallet.balance, quantity)

    proceeds = validate_amount(quantity * rates[code] / rates["USD"])
    usd_wallet = portfolio.wallets.get("USD")
    usd_balance = validate_amount(
        (usd_wallet.balance if usd_wallet is not None else 0.0) + proceeds,
        allow_zero=True,
    )
    source_balance = validate_amount(source_wallet.balance - quantity, allow_zero=True)
    if usd_wallet is None:
        usd_wallet = portfolio.add_currency("USD")
    source_wallet.balance = source_balance
    usd_wallet.balance = usd_balance
    return proceeds


class WalletService:
    """Coordinate persistence, login state, wallet operations, and rate cache."""

    RATE_TTL = timedelta(minutes=5)

    def __init__(self, storage: JsonStorage | None = None) -> None:
        self.storage = storage or JsonStorage()
        self._current_user_id: int | None = None

    def register(self, username: str, password: str) -> User:
        """Create a user and an empty portfolio, persisting both."""
        if not isinstance(username, str):
            raise TypeError("Имя пользователя должно быть строкой.")
        normalized_username = username.strip()
        if not normalized_username:
            raise ValueError("Имя пользователя не может быть пустым.")

        users = self.storage.load_users()
        if any(
            user.username.casefold() == normalized_username.casefold() for user in users
        ):
            raise UserAlreadyExistsError(normalized_username)

        user_id = max((user.user_id for user in users), default=0) + 1
        user = User.register(user_id, normalized_username, password)
        portfolios = self.storage.load_portfolios(
            {existing.user_id: existing for existing in users}
        )
        portfolios.append(Portfolio(user))
        self.storage.save_users([*users, user])
        self.storage.save_portfolios(portfolios)
        return user

    def login(self, username: str, password: str) -> User:
        """Verify credentials and retain the user's ID for this CLI session."""
        users = self.storage.load_users()
        user = next(
            (
                candidate
                for candidate in users
                if candidate.username.casefold() == username.strip().casefold()
            ),
            None,
        )
        if user is None:
            raise UserNotFoundError(username)
        if not user.verify_password(password):
            raise InvalidCredentialsError("Неверный пароль")
        self._current_user_id = user.user_id
        return user

    def get_rate(self, from_currency: str, to_currency: str) -> RateQuote:
        """Return a fresh cached rate or refresh it from the local rate stub."""
        source = normalize_currency_code(from_currency)
        target = normalize_currency_code(to_currency)
        now = datetime.now(timezone.utc)
        if source == target:
            return RateQuote(source, target, 1.0, now, "Identity")

        cached_rates = self.storage.load_rates()
        pair_key = f"{source}_{target}"
        cached_quote = self._fresh_quote(cached_rates, pair_key, source, target, now)
        if cached_quote is not None:
            return cached_quote

        reverse_key = f"{target}_{source}"
        reverse_quote = self._fresh_quote(
            cached_rates,
            reverse_key,
            target,
            source,
            now,
        )
        if reverse_quote is not None:
            quote = RateQuote(
                source,
                target,
                1.0 / reverse_quote.rate,
                reverse_quote.updated_at,
                reverse_quote.source,
            )
            self._cache_quote(cached_rates, quote)
            return quote

        rates = resolve_exchange_rates(DEFAULT_EXCHANGE_RATES)
        if source not in rates or target not in rates:
            raise RateUnavailableError(f"Курс {source}→{target} недоступен.")
        quote = RateQuote(
            source,
            target,
            rates[source] / rates[target],
            now,
            "LocalStub",
        )
        self._cache_quote(cached_rates, quote)
        return quote

    def ensure_authenticated(self) -> User:
        """Return the active user or raise when no session is logged in."""
        return self._require_user()

    def show_portfolio(self, base_currency: str = "USD") -> PortfolioReport:
        """Load the current user's portfolio and value each wallet."""
        user = self._require_user()
        base = normalize_currency_code(base_currency)
        try:
            self.get_rate(base, "USD")
        except RateUnavailableError as error:
            raise UnknownCurrencyError(base) from error

        portfolio = self._load_portfolio(user)
        lines: list[PortfolioLine] = []
        for wallet in portfolio.wallets.values():
            quote = self.get_rate(wallet.currency_code, base)
            lines.append(
                PortfolioLine(
                    currency_code=wallet.currency_code,
                    balance=wallet.balance,
                    value=wallet.balance * quote.rate,
                )
            )
        return PortfolioReport(
            username=user.username,
            base_currency=base,
            lines=tuple(lines),
            total_value=sum(line.value for line in lines),
        )

    def buy(self, currency_code: str, amount: int | float) -> TradeReport:
        """Buy currency for the current user and persist the new balance."""
        user = self._require_user()
        code = normalize_currency_code(currency_code)
        quantity = validate_amount(amount)
        try:
            quote = self.get_rate(code, "USD")
        except RateUnavailableError as error:
            raise RateUnavailableError(
                f"Не удалось получить курс для {code}→USD."
            ) from error

        portfolio = self._load_portfolio(user)
        wallet = portfolio.wallets.get(code)
        previous_balance = wallet.balance if wallet is not None else 0.0
        estimated_value = buy_currency(
            portfolio,
            code,
            quantity,
            {"USD": 1.0, code: quote.rate},
        )
        updated_wallet = portfolio.get_wallet(code)
        self._save_portfolio(portfolio)
        return TradeReport(
            action="Покупка",
            currency_code=code,
            amount=quantity,
            previous_balance=previous_balance,
            new_balance=updated_wallet.balance,
            rate=quote.rate,
            estimated_value_usd=estimated_value,
        )

    def sell(self, currency_code: str, amount: int | float) -> TradeReport:
        """Sell currency for the current user and persist the new balances."""
        user = self._require_user()
        code = normalize_currency_code(currency_code)
        quantity = validate_amount(amount)
        portfolio = self._load_portfolio(user)
        try:
            wallet = portfolio.get_wallet(code)
        except KeyError as error:
            raise WalletNotFoundError(code) from error
        if quantity > wallet.balance:
            raise InsufficientFundsError(code, wallet.balance, quantity)
        try:
            quote = self.get_rate(code, "USD")
        except RateUnavailableError as error:
            raise RateUnavailableError(
                f"Не удалось получить курс для {code}→USD."
            ) from error

        previous_balance = wallet.balance
        estimated_value = sell_currency(
            portfolio,
            code,
            quantity,
            {"USD": 1.0, code: quote.rate},
        )
        self._save_portfolio(portfolio)
        return TradeReport(
            action="Продажа",
            currency_code=code,
            amount=quantity,
            previous_balance=previous_balance,
            new_balance=portfolio.get_wallet(code).balance,
            rate=quote.rate,
            estimated_value_usd=estimated_value,
        )

    def _require_user(self) -> User:
        if self._current_user_id is None:
            raise NotAuthenticatedError("Сначала выполните login")
        users = self.storage.load_users()
        user = next(
            (
                candidate
                for candidate in users
                if candidate.user_id == self._current_user_id
            ),
            None,
        )
        if user is None:
            self._current_user_id = None
            raise NotAuthenticatedError("Сначала выполните login")
        return user

    def _load_portfolio(self, user: User) -> Portfolio:
        users = self.storage.load_users()
        users_by_id = {candidate.user_id: candidate for candidate in users}
        portfolios = self.storage.load_portfolios(users_by_id)
        portfolio = next(
            (item for item in portfolios if item.user_id == user.user_id),
            None,
        )
        if portfolio is None:
            raise ValueError(f"Портфель пользователя '{user.username}' не найден.")
        return portfolio

    def _save_portfolio(self, portfolio: Portfolio) -> None:
        users = self.storage.load_users()
        users_by_id = {candidate.user_id: candidate for candidate in users}
        portfolios = self.storage.load_portfolios(users_by_id)
        for index, stored in enumerate(portfolios):
            if stored.user_id == portfolio.user_id:
                portfolios[index] = portfolio
                break
        else:
            portfolios.append(portfolio)
        self.storage.save_portfolios(portfolios)

    def _fresh_quote(
        self,
        rates: Mapping[str, object],
        key: str,
        from_currency: str,
        to_currency: str,
        now: datetime,
    ) -> RateQuote | None:
        entry = rates.get(key)
        if not isinstance(entry, dict):
            return None
        try:
            rate = validate_amount(entry["rate"])
            updated_at_value = entry["updated_at"]
            updated_at = datetime.fromisoformat(updated_at_value)
        except (KeyError, TypeError, ValueError):
            return None
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        if now - updated_at > self.RATE_TTL:
            return None
        source = entry.get("source", rates.get("source", "RateCache"))
        return RateQuote(from_currency, to_currency, rate, updated_at, str(source))

    def _cache_quote(self, rates: dict[str, object], quote: RateQuote) -> None:
        updated_at = quote.updated_at.isoformat()
        rates[f"{quote.from_currency}_{quote.to_currency}"] = {
            "rate": quote.rate,
            "updated_at": updated_at,
            "source": quote.source,
        }
        rates[f"{quote.to_currency}_{quote.from_currency}"] = {
            "rate": 1.0 / quote.rate,
            "updated_at": updated_at,
            "source": quote.source,
        }
        rates["source"] = quote.source
        rates["last_refresh"] = updated_at
        self.storage.save_rates(rates)
