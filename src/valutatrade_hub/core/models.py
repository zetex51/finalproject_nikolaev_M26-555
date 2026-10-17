"""User, wallet, and portfolio domain models."""

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from valutatrade_hub.core.utils import (
    generate_salt,
    hash_password,
    normalize_currency_code,
    resolve_exchange_rates,
    validate_amount,
    verify_password,
)


def _validate_user_id(user_id: int) -> int:
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        raise TypeError("ID пользователя должен быть целым числом.")
    if user_id < 1:
        raise ValueError("ID пользователя должен быть положительным.")
    return user_id


def _parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError("Дата регистрации должна быть в формате ISO.") from error
    raise TypeError("Дата регистрации должна быть объектом datetime или ISO-строкой.")


class User:
    """A registered user with a salted password hash."""

    def __init__(
        self,
        user_id: int,
        username: str,
        hashed_password: str,
        salt: str,
        registration_date: datetime | str,
    ) -> None:
        self._user_id = _validate_user_id(user_id)
        self.username = username
        if not isinstance(hashed_password, str) or not hashed_password:
            raise ValueError("Хеш пароля должен быть непустой строкой.")
        if not isinstance(salt, str) or not salt:
            raise ValueError("Соль должна быть непустой строкой.")
        self._hashed_password = hashed_password
        self._salt = salt
        self._registration_date = _parse_datetime(registration_date)

    @classmethod
    def register(
        cls,
        user_id: int,
        username: str,
        password: str,
        registration_date: datetime | None = None,
    ) -> "User":
        """Create a user from a plain password, generating a unique salt."""
        salt = generate_salt()
        return cls(
            user_id=user_id,
            username=username,
            hashed_password=hash_password(password, salt),
            salt=salt,
            registration_date=(
                registration_date
                if registration_date is not None
                else datetime.now(timezone.utc)
            ),
        )

    @property
    def user_id(self) -> int:
        """Return the immutable user ID."""
        return self._user_id

    @property
    def username(self) -> str:
        """Return the account username."""
        return self._username

    @username.setter
    def username(self, username: str) -> None:
        if not isinstance(username, str):
            raise TypeError("Имя пользователя должно быть строкой.")
        normalized = username.strip()
        if not normalized:
            raise ValueError("Имя пользователя не может быть пустым.")
        self._username = normalized

    @property
    def hashed_password(self) -> str:
        """Return the stored password hash, never the plain password."""
        return self._hashed_password

    @property
    def salt(self) -> str:
        """Return the salt needed to verify the stored password hash."""
        return self._salt

    @property
    def registration_date(self) -> datetime:
        """Return the immutable registration timestamp."""
        return self._registration_date

    def get_user_info(self) -> dict[str, int | str]:
        """Return public user information without password material."""
        return {
            "user_id": self._user_id,
            "username": self._username,
            "registration_date": self._registration_date.isoformat(),
        }

    def change_password(self, new_password: str) -> None:
        """Replace the password hash and generate a fresh salt."""
        new_salt = generate_salt()
        new_hash = hash_password(new_password, new_salt)
        self._salt = new_salt
        self._hashed_password = new_hash

    def verify_password(self, password: str) -> bool:
        """Return whether a candidate password matches the saved hash."""
        return verify_password(password, self._salt, self._hashed_password)

    def to_dict(self) -> dict[str, int | str]:
        """Return a JSON-serializable record including hash and salt."""
        return {
            "user_id": self._user_id,
            "username": self._username,
            "hashed_password": self._hashed_password,
            "salt": self._salt,
            "registration_date": self._registration_date.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "User":
        """Build a user from the corresponding users.json record."""
        try:
            return cls(
                user_id=data["user_id"],
                username=data["username"],
                hashed_password=data["hashed_password"],
                salt=data["salt"],
                registration_date=data["registration_date"],
            )
        except KeyError as error:
            raise ValueError(
                f"В записи пользователя отсутствует поле {error}."
            ) from error


class Wallet:
    """A single-currency wallet with a validated non-negative balance."""

    def __init__(self, currency_code: str, balance: int | float = 0.0) -> None:
        self._currency_code = normalize_currency_code(currency_code)
        self.balance = balance

    @property
    def currency_code(self) -> str:
        """Return the immutable currency ticker."""
        return self._currency_code

    @property
    def balance(self) -> float:
        """Return the current balance."""
        return self._balance

    @balance.setter
    def balance(self, value: int | float) -> None:
        self._balance = validate_amount(value, allow_zero=True)

    def deposit(self, amount: int | float) -> float:
        """Add a positive amount and return the new balance."""
        normalized = validate_amount(amount)
        self.balance = self._balance + normalized
        return self._balance

    def withdraw(self, amount: int | float) -> float:
        """Withdraw a positive amount if the balance is sufficient."""
        normalized = validate_amount(amount)
        if normalized > self._balance:
            raise ValueError(f"Недостаточно средств в кошельке {self._currency_code}.")
        self.balance = self._balance - normalized
        return self._balance

    def get_balance_info(self) -> dict[str, str | float]:
        """Return the currency and current balance for display or JSON output."""
        return {"currency_code": self._currency_code, "balance": self._balance}

    def to_dict(self) -> dict[str, str | float]:
        """Return a JSON-serializable wallet record."""
        return self.get_balance_info()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Wallet":
        """Build a wallet from a portfolios.json wallet record."""
        try:
            return cls(
                currency_code=data["currency_code"],
                balance=data["balance"],
            )
        except KeyError as error:
            raise ValueError(f"В записи кошелька отсутствует поле {error}.") from error


class Portfolio:
    """A user's collection of currency wallets."""

    def __init__(
        self,
        user_id: int | User,
        wallets: Mapping[str, Wallet] | None = None,
        *,
        user: User | None = None,
    ) -> None:
        if user is not None and not isinstance(user, User):
            raise TypeError("Связанный пользователь должен быть объектом User.")
        if isinstance(user_id, User):
            if user is not None and user is not user_id:
                raise ValueError("Переданы два разных пользователя.")
            user = user_id
            normalized_user_id = user.user_id
        else:
            normalized_user_id = _validate_user_id(user_id)

        if user is not None and user.user_id != normalized_user_id:
            raise ValueError("ID портфеля не совпадает с ID пользователя.")

        self._user_id = normalized_user_id
        self._user = user
        self._wallets: dict[str, Wallet] = {}
        if wallets is not None and not isinstance(wallets, Mapping):
            raise TypeError(
                "Кошельки должны быть отображением кодов и объектов Wallet."
            )
        for currency_code, wallet in (wallets or {}).items():
            code = normalize_currency_code(currency_code)
            if not isinstance(wallet, Wallet):
                raise TypeError("Портфель может содержать только объекты Wallet.")
            if wallet.currency_code != code:
                raise ValueError("Код кошелька не совпадает с ключом портфеля.")
            if code in self._wallets:
                raise ValueError(f"Кошелёк {code} уже есть в портфеле.")
            self._wallets[code] = wallet

    @property
    def user(self) -> User | None:
        """Return the associated user; the association cannot be reassigned."""
        return self._user

    @property
    def user_id(self) -> int:
        """Return the immutable owner ID."""
        return self._user_id

    @property
    def wallets(self) -> dict[str, Wallet]:
        """Return a copy of the wallet mapping."""
        return self._wallets.copy()

    def add_currency(self, currency_code: str) -> Wallet:
        """Create and return a zero-balance wallet for a new currency."""
        code = normalize_currency_code(currency_code)
        if code in self._wallets:
            raise ValueError(f"Кошелёк {code} уже есть в портфеле.")
        wallet = Wallet(code)
        self._wallets[code] = wallet
        return wallet

    def get_wallet(self, currency_code: str) -> Wallet:
        """Return a wallet by ticker, raising ``KeyError`` if absent."""
        code = normalize_currency_code(currency_code)
        try:
            return self._wallets[code]
        except KeyError as error:
            raise KeyError(f"Кошелёк {code} не найден.") from error

    def get_total_value(
        self,
        base_currency: str = "USD",
        exchange_rates: Mapping[str, int | float] | None = None,
    ) -> float:
        """Convert every wallet balance to the requested base currency."""
        base_code = normalize_currency_code(base_currency)
        rates = resolve_exchange_rates(exchange_rates)
        if base_code not in rates:
            raise ValueError(f"Нет курса для базовой валюты {base_code}.")

        base_rate = rates[base_code]
        total = 0.0
        for code, wallet in self._wallets.items():
            if code not in rates:
                raise ValueError(f"Нет курса для валюты {code}.")
            total += wallet.balance * rates[code] / base_rate
        return total

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable portfolio record."""
        return {
            "user_id": self._user_id,
            "wallets": {
                code: wallet.to_dict() for code, wallet in self._wallets.items()
            },
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        user: User | None = None,
    ) -> "Portfolio":
        """Build a portfolio from a portfolios.json record."""
        try:
            wallets_data = data["wallets"]
            if not isinstance(wallets_data, Mapping):
                raise ValueError("Список кошельков должен быть объектом JSON.")
            wallets: dict[str, Wallet] = {}
            for code, wallet_data in wallets_data.items():
                if not isinstance(wallet_data, Mapping):
                    raise ValueError(f"Данные кошелька {code} должны быть объектом.")
                try:
                    currency_code = wallet_data.get("currency_code", code)
                    balance = wallet_data["balance"]
                except KeyError as error:
                    raise ValueError(
                        f"В записи кошелька {code} отсутствует поле {error}."
                    ) from error
                wallets[code] = Wallet(currency_code, balance)
            return cls(data["user_id"], wallets, user=user)
        except KeyError as error:
            raise ValueError(f"В записи портфеля отсутствует поле {error}.") from error
