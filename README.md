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

Настройки `data_dir`, `rates_ttl_seconds`, `default_base_currency`, а также
путь и параметры логирования задаются в `[tool.valutatrade]` в `pyproject.toml`.
`SettingsLoader` и `DatabaseManager` используют Singleton через `__new__` и
создаются при первом обращении. JSON-хранилище сериализует операции изменения
данных внутри процесса и заменяет файлы атомарно.

## Ошибки и журнал действий

- При нехватке валюты `InsufficientFundsError` сообщает доступный и требуемый
  балансы.
- Неизвестный или некорректный код вызывает `CurrencyNotFoundError`; CLI выводит
  поддерживаемые коды и пример команды `get-rate`.
- Если свежего курса нет и локальная заглушка не может его предоставить,
  `ApiRequestError` сообщает причину и предлагает повторить позже и проверить
  подключение.

Операции регистрации, входа, покупки, продажи и получения курса записываются в
`logs/actions.log`. Формат строковый, человекочитаемый, с ISO-временем и полями
операции; пароли в лог не попадают. Уровень по умолчанию — `INFO`, ротация — 1
МБ с тремя резервными файлами. Уровень можно переключить на `DEBUG` через
`log_level` в `[tool.valutatrade]` или `VALUTATRADE_LOG_LEVEL`.

Валюта задаётся кодом из 2–5 латинских букв или цифр. Количество при покупке и
продаже должно быть конечным числом больше нуля. Пароль должен содержать не
менее четырёх символов. Пользовательские данные и портфели не добавляются в Git.

## Типы валют

В модуле `valutatrade_hub.core.currencies` есть абстрактный класс `Currency` и
его наследники `FiatCurrency` и `CryptoCurrency`. Каждый объект проверяет
название и код, а `get_display_info()` возвращает строку для интерфейса и логов.
Кошелёк предоставляет метаданные валюты через свойство `currency`.

```python
from valutatrade_hub.core import CryptoCurrency, get_currency

bitcoin = get_currency("BTC")
print(bitcoin.get_display_info())

custom_coin = CryptoCurrency("Example Coin", "EXM", "SHA-256", 1.12e12)
print(custom_coin.get_display_info())
```

Реестр содержит USD, EUR, RUB, BTC и ETH. Он расширяется функцией
`register_currency()`. Если корректный код отсутствует в реестре,
`get_currency()` поднимает `CurrencyNotFoundError`. Рыночная капитализация в
реестре демонстрационная; данные Parser Service пока не подключены.

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
│   ├── currencies.py  # базовый тип, классы валют и реестр
│   ├── exceptions.py  # предметные исключения
│   ├── storage.py     # JSON-хранилище
│   ├── usecases.py    # сервис аккаунтов, курсов и операций
│   └── utils.py       # хеширование, проверки и курсы
├── infra/
│   ├── settings.py    # Singleton конфигурации
│   └── database.py    # Singleton доступа к JSON-хранилищу
├── decorators.py      # логирование операций
├── logging_config.py  # настройка ротации журнала
└── cli/
    └── interface.py  # интерактивная командная оболочка
main.py                # запуск из исходников
```
