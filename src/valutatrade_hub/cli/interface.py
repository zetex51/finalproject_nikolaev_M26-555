"""Interactive command line interface for ValutaTrade Hub."""

import math
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from prettytable import PrettyTable

from valutatrade_hub.core.currencies import (
    CryptoCurrency,
    get_currency,
    supported_currency_codes,
)
from valutatrade_hub.core.exceptions import ApiRequestError, CurrencyNotFoundError
from valutatrade_hub.core.usecases import (
    InsufficientFundsError,
    InvalidCredentialsError,
    NotAuthenticatedError,
    RateUnavailableError,
    TradeReport,
    UnknownCurrencyError,
    UserAlreadyExistsError,
    UserNotFoundError,
    WalletNotFoundError,
    WalletService,
)
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.parser_service.updater import RatesUpdater

_CACHE_PAIR = re.compile(r"^([A-Z0-9]{2,5})_([A-Z0-9]{2,5})$")


@dataclass(frozen=True)
class _RateRow:
    currency_code: str
    base_code: str
    rate: float
    updated_at: str
    source: str

    @property
    def pair(self) -> str:
        return f"{self.currency_code}_{self.base_code}"


class WalletApplication:
    """Own the console loop and dispatch user commands to core use cases."""

    def __init__(
        self,
        service: WalletService | None = None,
        rate_updater: RatesUpdater | None = None,
        database: DatabaseManager | None = None,
    ) -> None:
        self.service = service if service is not None else WalletService()
        self.rate_updater = rate_updater if rate_updater is not None else RatesUpdater()
        updater_storage = getattr(self.rate_updater, "storage", None)
        if database is not None:
            self.database = database
        elif callable(getattr(updater_storage, "load_rates", None)):
            self.database = updater_storage
        else:
            self.database = DatabaseManager()
        self.default_base_currency = (
            str(SettingsLoader().get("default_base_currency", "USD")).strip().upper()
        )

    def run(self) -> None:
        """Read and process commands until the user exits."""
        print("ValutaTrade Hub. Введите help для справки.")
        while True:
            try:
                command = input("wallet> ")
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if self.execute(command):
                break

    def execute(self, command_line: str) -> bool:
        """Execute a command; return ``True`` when the shell should exit."""
        try:
            parts = shlex.split(command_line)
        except ValueError as error:
            print(f"Некорректная команда: {error}")
            return False

        if not parts:
            return False

        command = parts[0].lower()
        if command in {"exit", "quit"}:
            return True
        if command == "help":
            self._show_help()
            return False

        handlers = {
            "register": self._register,
            "login": self._login,
            "show-portfolio": self._show_portfolio,
            "buy": self._buy,
            "sell": self._sell,
            "get-rate": self._get_rate,
            "update-rates": self._update_rates,
            "show-rates": self._show_rates,
        }
        handler = handlers.get(command)
        if handler is None:
            print(f"Неизвестная команда: {command}")
            return False

        try:
            handler(parts[1:])
        except CurrencyNotFoundError as error:
            self._print_currency_error(error)
        except ApiRequestError as error:
            self._print_api_error(error)
        except (TypeError, ValueError) as error:
            message = str(error)
            if "положительн" in message or "конечным числом" in message:
                print("'amount' должен быть положительным числом")
            else:
                print(f"Ошибка: {error}")
        return False

    def _register(self, arguments: list[str]) -> None:
        options = self._parse_options(
            arguments,
            required={"--username", "--password"},
        )
        username = options["--username"]
        try:
            user = self.service.register(username, options["--password"])
        except UserAlreadyExistsError:
            print(f"Имя пользователя '{username}' уже занято")
            return
        except ValueError as error:
            print(error)
            return
        print(
            f"Пользователь '{user.username}' зарегистрирован (id={user.user_id}). "
            f"Войдите: login --username {user.username} --password ****"
        )

    def _login(self, arguments: list[str]) -> None:
        options = self._parse_options(
            arguments,
            required={"--username", "--password"},
        )
        username = options["--username"]
        try:
            user = self.service.login(username, options["--password"])
        except UserNotFoundError:
            print(f"Пользователь '{username}' не найден")
            return
        except InvalidCredentialsError:
            print("Неверный пароль")
            return
        print(f"Вы вошли как '{user.username}'")

    def _show_portfolio(self, arguments: list[str]) -> None:
        options = self._parse_options(arguments, optional={"--base"})
        base_currency = options.get("--base")
        try:
            report = self.service.show_portfolio(base_currency)
        except NotAuthenticatedError:
            print("Сначала выполните login")
            return
        except CurrencyNotFoundError as error:
            self._print_currency_error(error)
            return
        except UnknownCurrencyError:
            print(f"Неизвестная базовая валюта '{(base_currency or 'USD').upper()}'")
            return
        except RateUnavailableError as error:
            print(error)
            return

        print(
            f"Портфель пользователя '{report.username}' (база: {report.base_currency}):"
        )
        if not report.lines:
            print("Портфель пуст.")
        else:
            table = PrettyTable(
                ["Валюта", "Баланс", f"Стоимость, {report.base_currency}"]
            )
            table.align = "r"
            table.align["Валюта"] = "l"
            for line in report.lines:
                balance = self._format_amount(line.balance, line.currency_code)
                table.add_row(
                    [
                        line.currency_code,
                        balance,
                        f"{line.value:,.2f}",
                    ]
                )
            print(table)
        print("---------------------------------")
        print(f"ИТОГО: {report.total_value:,.2f} {report.base_currency}")

    def _buy(self, arguments: list[str]) -> None:
        options = self._parse_options(
            arguments,
            required={"--currency", "--amount"},
        )
        try:
            user = self.service.ensure_authenticated()
        except NotAuthenticatedError:
            print("Сначала выполните login")
            return
        currency_code = options["--currency"]
        amount = self._parse_amount(options["--amount"])
        try:
            report = self.service.buy(currency_code, amount, user_id=user.user_id)
        except NotAuthenticatedError:
            print("Сначала выполните login")
            return
        except CurrencyNotFoundError as error:
            self._print_currency_error(error)
            return
        except ApiRequestError as error:
            self._print_api_error(error)
            return
        except RateUnavailableError:
            code = currency_code.strip().upper()
            print(f"Не удалось получить курс для {code}→USD. Выполните update-rates.")
            return

        self._print_trade(report, purchase=True)

    def _sell(self, arguments: list[str]) -> None:
        options = self._parse_options(
            arguments,
            required={"--currency", "--amount"},
        )
        try:
            user = self.service.ensure_authenticated()
        except NotAuthenticatedError:
            print("Сначала выполните login")
            return
        currency_code = options["--currency"]
        amount = self._parse_amount(options["--amount"])
        try:
            report = self.service.sell(currency_code, amount, user_id=user.user_id)
        except NotAuthenticatedError:
            print("Сначала выполните login")
            return
        except CurrencyNotFoundError as error:
            self._print_currency_error(error)
            return
        except WalletNotFoundError:
            code = currency_code.strip().upper()
            print(
                f"У вас нет кошелька '{code}'. Добавьте валюту: она создаётся "
                "автоматически при первой покупке."
            )
            return
        except InsufficientFundsError as error:
            print(error)
            return
        except ApiRequestError as error:
            self._print_api_error(error)
            return
        except RateUnavailableError:
            code = currency_code.strip().upper()
            print(f"Не удалось получить курс для {code}→USD. Выполните update-rates.")
            return

        self._print_trade(report, purchase=False)

    def _get_rate(self, arguments: list[str]) -> None:
        options = self._parse_options(
            arguments,
            required={"--from", "--to"},
        )
        from_currency = options["--from"]
        to_currency = options["--to"]
        try:
            quote = self.service.get_rate(from_currency, to_currency)
            reverse_quote = self.service.get_rate(to_currency, from_currency)
        except CurrencyNotFoundError as error:
            self._print_currency_error(error)
            return
        except ApiRequestError as error:
            self._print_api_error(error)
            return
        except RateUnavailableError as error:
            print(error)
            return

        updated_at = quote.updated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"Курс {quote.from_currency}→{quote.to_currency}: {quote.rate:.8f} "
            f"(обновлено: {updated_at}, источник: {quote.source})"
        )
        print(
            f"Обратный курс {reverse_quote.from_currency}→"
            f"{reverse_quote.to_currency}: {reverse_quote.rate:.8f}"
        )

    def _print_trade(self, report: TradeReport, *, purchase: bool) -> None:
        amount = self._format_amount(report.amount, report.currency_code)
        previous = self._format_amount(
            report.previous_balance,
            report.currency_code,
        )
        current = self._format_amount(report.new_balance, report.currency_code)
        action = "Покупка" if purchase else "Продажа"
        estimate_label = "покупки" if purchase else "выручка"
        print(
            f"{action} выполнена: {amount} {report.currency_code} "
            f"по курсу {report.rate:,.2f} USD/{report.currency_code}"
        )
        print("Изменения в портфеле:")
        print(f"- {report.currency_code}: было {previous} → стало {current}")
        print(f"Оценочная {estimate_label}: {report.estimated_value_usd:,.2f} USD")

    def _update_rates(self, arguments: list[str]) -> None:
        options = self._parse_options(arguments, optional={"--source"})
        source = options.get("--source")
        if source is not None:
            source = source.strip().lower()
            if source not in {"coingecko", "exchangerate"}:
                raise ValueError(
                    "Для --source используйте 'coingecko' или 'exchangerate'."
                )
        try:
            run_update = getattr(self.rate_updater, "run_update", None)
            if not callable(run_update):
                run_update = self.rate_updater.update_rates
            report = run_update(source=source) if source else run_update()
        except ApiRequestError as error:
            self._print_api_error(error)
            return

        status = "завершено частично" if report.errors else "завершено"
        rates_fetched = getattr(report, "rates_fetched", report.cache_quotes_updated)
        last_refresh = getattr(report, "last_refresh", None) or "неизвестно"
        print(
            f"Обновление курсов {status}. Источник: {source or 'все'}. "
            f"Получено курсов — {rates_fetched}, "
            f"обновлено записей кеша — {report.cache_quotes_updated}. "
            f"Последнее обновление: {last_refresh}."
        )
        for reason in report.errors:
            self._print_api_error(ApiRequestError(reason))

    def _show_rates(self, arguments: list[str]) -> None:
        options = self._parse_options(
            arguments,
            optional={"--currency", "--top", "--base"},
        )
        currency = options.get("--currency")
        currency = currency.strip().upper() if currency else None
        base_currency = options.get("--base", self.default_base_currency)
        base_currency = base_currency.strip().upper()

        try:
            get_currency(base_currency)
        except CurrencyNotFoundError:
            print(f"Неизвестная базовая валюта '{base_currency}'.")
            return

        top_count: int | None = None
        if "--top" in options:
            try:
                top_count = int(options["--top"])
            except ValueError as error:
                raise ValueError(
                    "Для --top укажите положительное целое число."
                ) from error
            if top_count <= 0:
                raise ValueError("Для --top укажите положительное целое число.")

        try:
            snapshot = self.database.load_rates()
        except (OSError, TypeError, ValueError):
            self._print_empty_rates_cache()
            return

        pairs = snapshot.get("pairs") if isinstance(snapshot, Mapping) else None
        if not isinstance(pairs, Mapping) or not pairs:
            self._print_empty_rates_cache()
            return

        rows = self._rates_for_base(pairs, base_currency)
        if currency is not None:
            rows = [row for row in rows if row.currency_code == currency]
            if not rows:
                print(f"Курс для '{currency}' не найден в кеше.")
                return

        if top_count is not None:
            rows = [row for row in rows if self._is_crypto(row.currency_code)]
            rows.sort(key=lambda row: row.rate, reverse=True)
            rows = rows[:top_count]
            if not rows:
                print(f"В кеше нет курсов криптовалют относительно {base_currency}.")
                return
        else:
            rows.sort(key=lambda row: row.currency_code)
            if not rows:
                print(f"В кеше нет курсов относительно {base_currency}.")
                return

        last_refresh = snapshot.get("last_refresh") or "неизвестно"
        print(
            f"Курсы из локального кеша относительно {base_currency} "
            f"(последнее обновление: {last_refresh}):"
        )
        table = PrettyTable(["Пара", "Курс", "Обновлено", "Источник"])
        for row in rows:
            table.add_row(
                [
                    row.pair,
                    f"{row.rate:,.8g}",
                    row.updated_at,
                    row.source,
                ]
            )
        print(table)

    @staticmethod
    def _rates_for_base(
        pairs: Mapping[str, Any],
        base_currency: str,
    ) -> list[_RateRow]:
        quotes: dict[tuple[str, str], _RateRow] = {}
        for pair, value in pairs.items():
            match = _CACHE_PAIR.fullmatch(pair) if isinstance(pair, str) else None
            if match is None or not isinstance(value, Mapping):
                continue
            raw_rate = value.get("rate")
            if isinstance(raw_rate, bool) or not isinstance(raw_rate, (int, float)):
                continue
            try:
                rate = float(raw_rate)
            except (OverflowError, ValueError):
                continue
            if not math.isfinite(rate) or rate <= 0:
                continue
            from_code, to_code = match.groups()
            updated_at = value.get("updated_at")
            source = value.get("source")
            quotes[(from_code, to_code)] = _RateRow(
                currency_code=from_code,
                base_code=to_code,
                rate=rate,
                updated_at=updated_at if isinstance(updated_at, str) else "неизвестно",
                source=source.strip()
                if isinstance(source, str) and source.strip()
                else "неизвестен",
            )

        destinations: dict[str, dict[str, _RateRow]] = {}
        for (from_code, to_code), quote in quotes.items():
            destinations.setdefault(from_code, {})[to_code] = quote

        rows: dict[str, _RateRow] = {}
        for currency_code, available in destinations.items():
            if currency_code == base_currency:
                continue
            direct_quote = available.get(base_currency)
            if direct_quote is not None:
                rows[currency_code] = direct_quote
                continue

            common_anchors = set(available) & set(destinations.get(base_currency, {}))
            anchors = sorted(common_anchors, key=lambda code: (code != "USD", code))
            for anchor in anchors:
                numerator = available[anchor]
                denominator = destinations[base_currency][anchor]
                if denominator.rate <= 0:
                    continue
                derived_rate = numerator.rate / denominator.rate
                if not math.isfinite(derived_rate) or derived_rate <= 0:
                    continue
                source_names = list(
                    dict.fromkeys((numerator.source, denominator.source))
                )
                source = " + ".join(source_names)
                timestamps = [
                    timestamp
                    for timestamp in (numerator.updated_at, denominator.updated_at)
                    if timestamp != "неизвестно"
                ]
                rows[currency_code] = _RateRow(
                    currency_code=currency_code,
                    base_code=base_currency,
                    rate=derived_rate,
                    updated_at=min(timestamps) if timestamps else "неизвестно",
                    source=source,
                )
                break

        return list(rows.values())

    @staticmethod
    def _is_crypto(currency_code: str) -> bool:
        try:
            return isinstance(get_currency(currency_code), CryptoCurrency)
        except CurrencyNotFoundError:
            return False

    @staticmethod
    def _print_empty_rates_cache() -> None:
        print(
            "Локальный кеш курсов пуст. Выполните 'update-rates', "
            "чтобы загрузить данные."
        )

    @staticmethod
    def _parse_options(
        arguments: list[str],
        *,
        required: set[str] | None = None,
        optional: set[str] | None = None,
    ) -> dict[str, str]:
        """Parse ``--option value`` pairs without handling business rules."""
        required_options = required or set()
        allowed_options = required_options | (optional or set())
        parsed: dict[str, str] = {}
        index = 0
        while index < len(arguments):
            option = arguments[index]
            if option not in allowed_options:
                raise ValueError(f"Неизвестный аргумент: {option}")
            if option in parsed:
                raise ValueError(f"Аргумент {option} указан несколько раз.")
            index += 1
            if index >= len(arguments) or arguments[index].startswith("--"):
                raise ValueError(f"Для аргумента {option} требуется значение.")
            parsed[option] = arguments[index]
            index += 1

        missing = required_options - parsed.keys()
        if missing:
            raise ValueError(f"Не указан обязательный аргумент: {sorted(missing)[0]}.")
        return parsed

    @staticmethod
    def _parse_amount(value: str) -> float:
        try:
            return float(value)
        except ValueError as error:
            raise ValueError("'amount' должен быть положительным числом") from error

    @staticmethod
    def _format_amount(amount: float, currency_code: str) -> str:
        precision = 4 if currency_code in {"BTC", "ETH"} else 2
        return f"{amount:,.{precision}f}"

    @staticmethod
    def _print_currency_error(error: CurrencyNotFoundError) -> None:
        codes = ", ".join(supported_currency_codes())
        print(f"{error}. Поддерживаемые коды: {codes}.")
        print("Проверьте код в команде get-rate --from USD --to <код>.")

    @staticmethod
    def _print_api_error(error: ApiRequestError) -> None:
        print(error)
        if "EXCHANGERATE_API_KEY" in error.reason:
            print("Задайте ключ ExchangeRate-API в переменной окружения.")
        elif "invalid-key" in error.reason:
            print("Проверьте значение EXCHANGERATE_API_KEY.")
        elif "quota-reached" in error.reason:
            print("Лимит запросов исчерпан; повторите попытку позже.")
        else:
            print("Повторите попытку позже и проверьте подключение к сети.")

    @staticmethod
    def _show_help() -> None:
        print(
            "Доступные команды:\n"
            "  register --username <имя> --password <пароль>\n"
            "  login --username <имя> --password <пароль>\n"
            "  show-portfolio [--base <валюта>]\n"
            "  buy --currency <валюта> --amount <количество>\n"
            "  sell --currency <валюта> --amount <количество>\n"
            "  get-rate --from <валюта> --to <валюта>\n"
            "  update-rates [--source <coingecko|exchangerate>]\n"
            "  show-rates [--currency <код>] [--top <N>] [--base <код>]\n"
            "  help\n"
            "  exit"
        )


def main() -> None:
    """Start the interactive command line."""
    WalletApplication().run()
