# ValutaTrade Hub

Консольное приложение для регистрации пользователей и управления портфелем
фиатных и криптовалют. Проект оформлен как устанавливаемый Python-пакет.

## Требования

- Python 3.12 или новее
- [`uv`](https://docs.astral.sh/uv/)

## Подготовка и запуск

```sh
uv sync
uv run wallet
```

После запуска введите `help`, чтобы увидеть список команд. Пример работы:

```text
register --username alice --password 1234
login --username alice --password 1234
buy --currency BTC --amount 0.05
show-portfolio
get-rate --from USD --to BTC
sell --currency BTC --amount 0.01
exit
```

Команды `buy`, `sell` и `show-portfolio` доступны после входа. Валюта
автоматически добавляется в портфель при первой покупке. Продажа уменьшает
баланс кошелька и зачисляет оценочную выручку в USD.

## Данные и курсы

При первом запуске приложение создаёт JSON-файлы в каталоге `data/`:

- `users.json` — ID и имена пользователей, хеши паролей с солью и даты
  регистрации;
- `portfolios.json` — кошельки и балансы пользователей;
- `rates.json` — кеш курсов и время обновления.

Путь к каталогу данных можно изменить переменной окружения
`VALUTATRADE_DATA_DIR`. Пока Parser Service не подключён, используются
демонстрационные локальные курсы из `valutatrade_hub.core.utils`. Кеш курсов
действует пять минут; `get-rate` показывает курс, время обновления и обратный
курс.

Валюта задаётся кодом из 2–10 латинских букв или цифр. Количество при покупке и
продаже должно быть конечным числом больше нуля. Пароль должен содержать не
менее четырёх символов. Пользовательские данные и портфели не добавляются в Git.

## Использование моделей из Python

```python
from valutatrade_hub.core import Portfolio, User, buy_currency

user = User.register(1, "alice", "secret")
portfolio = Portfolio(user)
portfolio.add_currency("USD").deposit(1_000)
buy_currency(portfolio, "BTC", 0.01)
print(portfolio.get_total_value("USD"))
```

`User`, `Wallet` и `Portfolio` проверяют входные данные и поддерживают
преобразование в/из JSON-совместимых словарей. Пароли сохраняются как SHA-256
хеши с индивидуальной случайной солью. CLI использует `shlex` для безопасного
разбора команд, а `prettytable` — для вывода портфеля.

## Контроль качества и сборка

```sh
uv run ruff check .
uv run ruff format --check .
uv build
```

## Структура

```text
src/valutatrade_hub/
├── core/
│   ├── models.py      # пользователи, кошельки и портфели
│   ├── storage.py     # JSON-хранилище
│   ├── usecases.py    # сервис аккаунтов, курсов и операций
│   └── utils.py       # хеширование, проверки и курсы
└── cli/
    └── interface.py  # интерактивная командная оболочка
main.py                # запуск из исходников
```
