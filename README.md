# ValutaTrade Hub

Консольная платформа для управления портфелем фиатных и криптовалют.
Репозиторий оформлен как устанавливаемый Python-пакет.

## Требования

- Python 3.12 или новее
- [`uv`](https://docs.astral.sh/uv/)

## Подготовка окружения

```sh
uv sync
```

Запуск приложения из исходников:

```sh
uv run wallet
```

В текущем CLI-каркасе доступны команды `help` и `exit`. Модели и операции
покупки/продажи также доступны из Python-кода.

## Модели и операции

```python
from valutatrade_hub.core import Portfolio, User, Wallet, buy_currency

user = User.register(1, "alice", "secret")
portfolio = Portfolio(user)
portfolio.add_currency("USD").deposit(1_000)
buy_currency(portfolio, "BTC", 0.01)
print(portfolio.get_total_value("USD"))
```

`User`, `Wallet` и `Portfolio` проверяют входные данные и поддерживают
преобразование в/из JSON-совместимых словарей. Пароли хранятся как SHA-256-хеши
с индивидуальной случайной солью. Фиктивные курсы для демонстрации заданы в
`valutatrade_hub.core.utils`; интерфейс получения актуальных курсов будет
подключён отдельным сервисом.

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
│   ├── models.py      # User, Wallet и Portfolio
│   ├── usecases.py    # операции покупки и продажи
│   └── utils.py       # хеширование, проверки и курсы
└── cli/
    └── interface.py  # интерактивная командная оболочка
main.py                # запуск из исходников
```

CLI разбирает пользовательский ввод через `shlex`. Для табличного вывода
подключён `prettytable`.
