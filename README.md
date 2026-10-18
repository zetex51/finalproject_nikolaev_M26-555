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

Для получения актуальных курсов фиата задайте ключ ExchangeRate-API в окружении
перед запуском:

```sh
export EXCHANGERATE_API_KEY="<ваш ключ>"
uv run wallet
```

Ключ CoinGecko не обязателен. При наличии Pro-ключа его можно задать в
`COINGECKO_API_KEY`; значение отправляется заголовком запроса и не записывается
в файлы проекта.

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
`VALUTATRADE_DATA_DIR`. Parser Service использует CoinGecko для BTC, ETH и SOL,
а ExchangeRate-API — для USD, EUR, GBP и RUB. CoinGecko запрашивается пакетно
по ID монет, а не по тикерам. Ответы нормализуются в пары курсов, сохраняются в
`rates.json` вместе с источником и временем обновления. Кеш действует пять
минут; `get-rate` показывает курс, время обновления и обратный курс. Если
`EXCHANGERATE_API_KEY` не задан, для фиатных валют из поддерживаемого набора
используются демонстрационные локальные значения; криптовалюты всё равно
запрашиваются у CoinGecko.

ExchangeRate-API при базовой валюте USD возвращает количество единиц фиата за
1 USD. Поэтому сервис берёт обратное значение, чтобы хранить курс в принятом в
проекте направлении: USD за одну единицу валюты (например, EUR→USD). Временная
метка берётся из ответа провайдера. При ошибках API CLI выводит причину; при
HTTP 429 сообщает о лимите запросов.

Настройки `data_dir`, `rates_ttl_seconds`, `rates_timeout_seconds`,
`default_base_currency`, а также путь и параметры логирования задаются в
`[tool.valutatrade]` в `pyproject.toml`. Таймаут сетевых запросов можно изменить
через `VALUTATRADE_RATES_TIMEOUT_SECONDS`.
`SettingsLoader` и `DatabaseManager` используют Singleton через `__new__` и
создаются при первом обращении. JSON-хранилище сериализует операции изменения
данных внутри процесса и заменяет файлы атомарно.

## Ошибки и журнал действий

- При нехватке валюты `InsufficientFundsError` сообщает доступный и требуемый
  балансы.
- Неизвестный или некорректный код вызывает `CurrencyNotFoundError`; CLI выводит
  поддерживаемые коды и пример команды `get-rate`.
- Если провайдер недоступен или вернул некорректный ответ, `ApiRequestError`
  содержит источник и причину; CLI предлагает повторить запрос позже и
  проверить подключение. Для ExchangeRate-API CLI также подсказывает, когда
  нужно задать или проверить API-ключ.

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

Реестр содержит USD, EUR, GBP, RUB, BTC, ETH и SOL. Он расширяется функцией
`register_currency()`. Если корректный код отсутствует в реестре,
`get_currency()` поднимает `CurrencyNotFoundError`. Рыночная капитализация в
реестре демонстрационная.

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
├── services/
│   └── parser.py      # курсы CoinGecko и ExchangeRate-API
├── decorators.py      # логирование операций
├── logging_config.py  # настройка ротации журнала
└── cli/
    └── interface.py  # интерактивная командная оболочка
main.py                # запуск из исходников
```
