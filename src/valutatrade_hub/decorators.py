"""Decorators used to trace domain operations without changing their errors."""

import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from valutatrade_hub.logging_config import configure_logging

P = ParamSpec("P")
R = TypeVar("R")


def log_action(
    action: str | Callable[..., Any] | None = None,
    *,
    verbose: bool = False,
) -> Any:
    """Log an operation result or exception, then return or re-raise unchanged."""

    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        operation = (
            action.upper() if isinstance(action, str) else function.__name__.upper()
        )
        signature = inspect.signature(function)

        @wraps(function)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            logger = configure_logging()
            bound = signature.bind_partial(*args, **kwargs)
            context = dict(bound.arguments)
            instance = args[0] if args else None
            try:
                result = function(*args, **kwargs)
            except Exception as error:
                logger.info(
                    _format_record(
                        operation,
                        context,
                        instance=instance,
                        error=error,
                    )
                )
                raise
            logger.info(
                _format_record(
                    operation,
                    context,
                    instance=instance,
                    result=result,
                    verbose=verbose,
                )
            )
            return result

        return wrapper

    if callable(action):
        return decorate(action)
    return decorate


def _format_record(
    action: str,
    context: dict[str, Any],
    *,
    instance: object | None,
    result: object | None = None,
    error: Exception | None = None,
    verbose: bool = False,
) -> str:
    """Render safe operation context; password arguments are never included."""
    fields = [action]
    username = context.get("username")
    if not username and instance is not None:
        username = getattr(instance, "current_username", None)
    user_id = context.get("user_id")
    if result is not None and hasattr(result, "user_id"):
        user_id = getattr(result, "user_id")
    if username:
        fields.append(f"user={username!r}")
    elif user_id is not None:
        fields.append(f"user_id={user_id}")

    currency_code = context.get("currency_code")
    if currency_code:
        fields.append(f"currency_code={str(currency_code).upper()!r}")
    elif context.get("from_currency") or context.get("to_currency"):
        source = context.get("from_currency", "?")
        target = context.get("to_currency", "?")
        fields.append(f"currency_code={f'{source}→{target}'!r}")

    amount = context.get("amount")
    if amount is not None:
        fields.append(f"amount={_number(amount, precision=4)}")

    rate = getattr(result, "rate", None)
    if rate is not None:
        if hasattr(result, "from_currency"):
            try:
                rate_text = f"{float(rate):.8g}"
            except (TypeError, ValueError, OverflowError):
                rate_text = repr(rate)
        else:
            rate_text = _number(rate, precision=2)
        fields.append(f"rate={rate_text}")
    if hasattr(result, "estimated_value_usd"):
        fields.append("base='USD'")
    elif context.get("base_currency"):
        fields.append(f"base={str(context['base_currency']).upper()!r}")
    elif context.get("to_currency"):
        fields.append(f"base={str(context['to_currency']).upper()!r}")

    if error is None:
        fields.append("result=OK")
        if verbose and hasattr(result, "previous_balance"):
            before = _number(getattr(result, "previous_balance"), precision=4)
            after = _number(getattr(result, "new_balance"), precision=4)
            fields.append(f"balance={before}->{after}")
        if verbose and hasattr(result, "updated_at"):
            fields.append(f"updated_at={getattr(result, 'updated_at').isoformat()}")
            fields.append(f"source={getattr(result, 'source', 'unknown')!r}")
    else:
        fields.extend(
            [
                "result=ERROR",
                f"error_type={type(error).__name__}",
                f"error_message={str(error).replace(chr(10), ' ')!r}",
            ]
        )
    return " ".join(fields)


def _number(value: object, *, precision: int) -> str:
    try:
        return f"{float(value):.{precision}f}"
    except (TypeError, ValueError, OverflowError):
        return repr(value)
