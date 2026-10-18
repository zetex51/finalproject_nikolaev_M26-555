"""Domain exceptions raised by the wallet application."""


class UserAlreadyExistsError(ValueError):
    """Raised when an account name is already registered."""


class UserNotFoundError(ValueError):
    """Raised when an account name is not registered."""


class InvalidCredentialsError(ValueError):
    """Raised when a user's password does not match."""


class NotAuthenticatedError(ValueError):
    """Raised when an operation requires a logged-in user."""


class UnknownCurrencyError(ValueError):
    """Raised when no rate is known for a requested currency."""


class CurrencyNotFoundError(LookupError):
    """Raised when a currency code is missing from the currency registry."""

    def __init__(self, currency_code: str) -> None:
        self.currency_code = currency_code
        super().__init__(f"Валюта '{currency_code}' не найдена в реестре.")


class RateUnavailableError(ValueError):
    """Raised when neither the rate cache nor the local stub has a quote."""


class WalletNotFoundError(ValueError):
    """Raised when a portfolio does not contain the requested wallet."""


class InsufficientFundsError(ValueError):
    """Raised when a sale asks to withdraw more than a wallet contains."""

    def __init__(self, currency_code: str, available: float, required: float) -> None:
        self.currency_code = currency_code
        self.available = available
        self.required = required
        super().__init__(currency_code)
