"""Interactive command line interface for ValutaTrade Hub."""

import shlex

from prettytable import PrettyTable

from valutatrade_hub.core.currencies import supported_currency_codes
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


class WalletApplication:
    """Own the console loop and dispatch user commands to core use cases."""

    def __init__(self, service: WalletService | None = None) -> None:
        self.service = service or WalletService()

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
            print(f"Не удалось получить курс для {code}→USD")
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
            print(f"Не удалось получить курс для {code}→USD")
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
            self._print_api_error(ApiRequestError(str(error)))
            return

        updated_at = quote.updated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"Курс {quote.from_currency}→{quote.to_currency}: {quote.rate:.8f} "
            f"(обновлено: {updated_at})"
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
        print(f"{error}. Повторите попытку позже и проверьте подключение к сети.")

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
            "  help\n"
            "  exit"
        )


def main() -> None:
    """Start the interactive command line."""
    WalletApplication().run()
