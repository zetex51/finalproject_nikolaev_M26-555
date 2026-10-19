"""Application operations for accounts, portfolios, trades, and exchange rates."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from valutatrade_hub.core.currencies import get_currency
from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    InsufficientFundsError,
    InvalidCredentialsError,
    NotAuthenticatedError,
    RateUnavailableError,
    UserAlreadyExistsError,
    UserNotFoundError,
    WalletNotFoundError,
)
from valutatrade_hub.core.models import Portfolio, User
from valutatrade_hub.core.storage import JsonStorage
from valutatrade_hub.core.utils import (
    convert_currency,
    resolve_exchange_rates,
    validate_amount,
)
from valutatrade_hub.decorators import log_action
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader


@dataclass(frozen=True)
class RateQuote:
    """A rate, its pair, timestamp, and source."""

    from_currency: str
    to_currency: str
    rate: float
    updated_at: datetime
    source: str
    cached_at: datetime | None = None


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
    code = get_currency(currency_code).code
    quantity = validate_amount(amount)
    rates = resolve_exchange_rates(exchange_rates)
    if code not in rates:
        raise RateUnavailableError(f"Не удалось получить курс для {code}→USD.")

    estimated_value = convert_currency(
        quantity,
        code,
        "USD",
        {"USD": rates["USD"], code: rates[code]},
    )
    target_wallet = portfolio.wallets.get(code)
    validate_amount(
        (target_wallet.balance if target_wallet is not None else 0.0) + quantity,
        allow_zero=True,
    )
    if target_wallet is None:
        target_wallet = portfolio.add_currency(code)
    target_wallet.deposit(quantity)
    return estimated_value


def sell_currency(
    portfolio: Portfolio,
    currency_code: str,
    amount: int | float,
    exchange_rates: Mapping[str, int | float] | None = None,
) -> float:
    """Remove a quantity, credit USD, and return the estimated proceeds."""
    code = get_currency(currency_code).code
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

    proceeds = convert_currency(
        quantity,
        code,
        "USD",
        {"USD": rates["USD"], code: rates[code]},
    )
    usd_wallet = portfolio.wallets.get("USD")
    usd_balance = validate_amount(
        (usd_wallet.balance if usd_wallet is not None else 0.0) + proceeds,
        allow_zero=True,
    )
    source_wallet.withdraw(quantity)
    if usd_wallet is None:
        usd_wallet = portfolio.add_currency("USD")
    usd_wallet.balance = usd_balance
    return proceeds


class WalletService:
    """Coordinate persistence, login state, wallet operations, and rate cache."""

    RATE_TTL = timedelta(minutes=5)

    def __init__(self, storage: JsonStorage | DatabaseManager | None = None) -> None:
        self.settings = SettingsLoader()
        self.storage = DatabaseManager() if storage is None else storage
        self.RATE_TTL = timedelta(seconds=self.settings.get("rates_ttl_seconds", 300))
        self._current_user_id: int | None = None
        self._current_username: str | None = None

    @property
    def current_username(self) -> str | None:
        """Return the active username for log context without reading storage."""
        return self._current_username

    @log_action("REGISTER")
    def register(self, username: str, password: str) -> User:
        """Create a user and an empty portfolio, persisting both."""
        with self.storage.transaction():
            return self._register(username, password)

    def _register(self, username: str, password: str) -> User:
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

    @log_action("LOGIN")
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
        self._current_username = user.username
        return user

    @log_action("GET_RATE", verbose=True)
    def get_rate(self, from_currency: str, to_currency: str) -> RateQuote:
        """Return a fresh quote from the local rate cache."""
        source = get_currency(from_currency).code
        target = get_currency(to_currency).code
        now = datetime.now(timezone.utc)
        if source == target:
            return RateQuote(source, target, 1.0, now, "Identity")

        pair_key = f"{source}_{target}"
        reverse_key = f"{target}_{source}"
        try:
            with self.storage.transaction():
                cached_rates = self._load_rates_safely()
                cached_quote = self._fresh_quote(
                    cached_rates,
                    pair_key,
                    source,
                    target,
                    now,
                )
                if cached_quote is not None:
                    return cached_quote

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
                        reverse_quote.cached_at,
                    )
                    self._cache_quote(cached_rates, quote)
                    return quote

                source_usd_quote = self._fresh_usd_quote(cached_rates, source, now)
                target_usd_quote = self._fresh_usd_quote(cached_rates, target, now)
                if source_usd_quote is not None and target_usd_quote is not None:
                    quote_rate = validate_amount(
                        source_usd_quote.rate / target_usd_quote.rate
                    )
                    providers = dict.fromkeys(
                        provider
                        for usd_quote in (source_usd_quote, target_usd_quote)
                        for provider in usd_quote.source.split("+")
                        if provider != "Identity"
                    )
                    quote_source = "+".join(providers) if providers else "Identity"
                    timestamps = [
                        usd_quote.updated_at
                        for usd_quote in (source_usd_quote, target_usd_quote)
                        if usd_quote.source != "Identity"
                    ]
                    quote_updated_at = (
                        min(timestamps) if timestamps else datetime.now(timezone.utc)
                    )
                    cache_timestamps = [
                        usd_quote.cached_at or usd_quote.updated_at
                        for usd_quote in (source_usd_quote, target_usd_quote)
                        if usd_quote.source != "Identity"
                    ]
                    quote = RateQuote(
                        source,
                        target,
                        quote_rate,
                        quote_updated_at,
                        quote_source,
                        min(cache_timestamps) if cache_timestamps else None,
                    )
                    self._cache_quote(cached_rates, quote)
                    return quote
        except (OSError, ValueError) as error:
            raise ApiRequestError(
                f"не удалось прочитать кеш курсов: {error}"
            ) from error
        raise RateUnavailableError(
            f"Курс {source}→{target} отсутствует или устарел в локальном кеше. "
            "Выполните update-rates."
        )

    def ensure_authenticated(self) -> User:
        """Return the active user or raise when no session is logged in."""
        return self._require_user()

    def show_portfolio(self, base_currency: str | None = None) -> PortfolioReport:
        """Load the current user's portfolio and value each wallet."""
        user = self._require_user()
        base_currency = base_currency or self.settings.get(
            "default_base_currency",
            "USD",
        )
        base = get_currency(base_currency).code
        self.get_rate(base, "USD")

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

    @log_action("BUY", verbose=True)
    def buy(
        self,
        currency_code: str,
        amount: int | float,
        *,
        user_id: int | None = None,
    ) -> TradeReport:
        """Buy currency for the current user and persist the new balance."""
        user = self._require_user(user_id)
        code = get_currency(currency_code).code
        quantity = validate_amount(amount)
        quote = self.get_rate(code, "USD")
        with self.storage.transaction():
            return self._buy(code, quantity, user.user_id, quote)

    def _buy(
        self,
        currency_code: str,
        amount: int | float,
        user_id: int | None,
        quote: RateQuote,
    ) -> TradeReport:
        user = self._require_user(user_id)
        code = get_currency(currency_code).code
        quantity = validate_amount(amount)

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

    @log_action("SELL", verbose=True)
    def sell(
        self,
        currency_code: str,
        amount: int | float,
        *,
        user_id: int | None = None,
    ) -> TradeReport:
        """Sell currency for the current user and persist the new balances."""
        user = self._require_user(user_id)
        code = get_currency(currency_code).code
        quantity = validate_amount(amount)
        portfolio = self._load_portfolio(user)
        try:
            wallet = portfolio.get_wallet(code)
        except KeyError as error:
            raise WalletNotFoundError(code) from error
        if quantity > wallet.balance:
            raise InsufficientFundsError(code, wallet.balance, quantity)
        quote = self.get_rate(code, "USD")
        with self.storage.transaction():
            return self._sell(code, quantity, user.user_id, quote)

    def _sell(
        self,
        currency_code: str,
        amount: int | float,
        user_id: int | None,
        quote: RateQuote,
    ) -> TradeReport:
        user = self._require_user(user_id)
        code = get_currency(currency_code).code
        quantity = validate_amount(amount)
        portfolio = self._load_portfolio(user)
        try:
            wallet = portfolio.get_wallet(code)
        except KeyError as error:
            raise WalletNotFoundError(code) from error
        if quantity > wallet.balance:
            raise InsufficientFundsError(code, wallet.balance, quantity)

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

    def _require_user(self, user_id: int | None = None) -> User:
        selected_user_id = self._current_user_id if user_id is None else user_id
        if selected_user_id is None:
            raise NotAuthenticatedError("Сначала выполните login")
        if (
            self._current_user_id is not None
            and selected_user_id != self._current_user_id
        ):
            raise NotAuthenticatedError(
                "Нельзя выполнить операцию от имени другого пользователя"
            )
        users = self.storage.load_users()
        user = next(
            (candidate for candidate in users if candidate.user_id == selected_user_id),
            None,
        )
        if user is None:
            if selected_user_id == self._current_user_id:
                self._current_user_id = None
                self._current_username = None
                raise NotAuthenticatedError("Сначала выполните login")
            raise UserNotFoundError(str(selected_user_id))
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
        cache_time_value = entry.get("cached_at", updated_at_value)
        try:
            cached_at = datetime.fromisoformat(cache_time_value)
        except (TypeError, ValueError):
            cached_at = updated_at
        if cached_at.tzinfo is None:
            cached_at = cached_at.replace(tzinfo=timezone.utc)
        if now - cached_at > self.RATE_TTL:
            return None
        source = entry.get("source", rates.get("source", "RateCache"))
        return RateQuote(
            from_currency,
            to_currency,
            rate,
            updated_at,
            str(source),
            cached_at,
        )

    def _fresh_usd_quote(
        self,
        rates: Mapping[str, object],
        currency_code: str,
        now: datetime,
    ) -> RateQuote | None:
        if currency_code == "USD":
            return RateQuote("USD", "USD", 1.0, now, "Identity")
        direct_quote = self._fresh_quote(
            rates,
            f"{currency_code}_USD",
            currency_code,
            "USD",
            now,
        )
        if direct_quote is not None:
            return direct_quote
        reverse_quote = self._fresh_quote(
            rates,
            f"USD_{currency_code}",
            "USD",
            currency_code,
            now,
        )
        if reverse_quote is None:
            return None
        return RateQuote(
            currency_code,
            "USD",
            1.0 / reverse_quote.rate,
            reverse_quote.updated_at,
            reverse_quote.source,
            reverse_quote.cached_at,
        )

    def _load_rates_safely(self) -> dict[str, object]:
        try:
            return self.storage.load_rates()
        except (OSError, ValueError):
            return {}

    def _cache_quote(self, rates: dict[str, object], quote: RateQuote) -> None:
        updated_at = quote.updated_at.isoformat()
        cache_timestamp = quote.cached_at or datetime.now(timezone.utc)
        cached_at = cache_timestamp.isoformat()
        rates[f"{quote.from_currency}_{quote.to_currency}"] = {
            "rate": quote.rate,
            "updated_at": updated_at,
            "cached_at": cached_at,
            "source": quote.source,
        }
        rates[f"{quote.to_currency}_{quote.from_currency}"] = {
            "rate": 1.0 / quote.rate,
            "updated_at": updated_at,
            "cached_at": cached_at,
            "source": quote.source,
        }
        rates["source"] = quote.source
        rates["last_refresh"] = updated_at
        self.storage.save_rates(rates)
