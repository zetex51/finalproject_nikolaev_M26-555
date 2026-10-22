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
в файлы проекта. В консоли выполните `update-rates`, чтобы получить начальные
курсы и обновить локальный кеш.

Для периодических обновлений запустите отдельный планировщик: он делает запрос
сразу при старте и затем повторяет его с заданным интервалом.

```sh
uv run wallet-rate-scheduler
```

После запуска введите `help`, чтобы увидеть список команд. Пример работы:

```text
register --username alice --password 1234
login --username alice --password 1234
update-rates
buy --currency BTC --amount 0.05
show-portfolio
get-rate --from USD --to BTC
sell --currency BTC --amount 0.01
exit
```

Команды `buy`, `sell` и `show-portfolio` доступны после входа. Валюта
автоматически добавляется в портфель при первой покупке. Продажа уменьшает
баланс кошелька и зачисляет оценочную выручку в USD.

Для управления кешем используйте `update-rates`; источник можно ограничить:

```text
update-rates --source coingecko
update-rates --source exchangerate
show-rates --top 2
show-rates --currency RUB
show-rates --base EUR
```

`show-rates` читает только локальный `rates.json`, показывает время и источник
курса. Для базы, отличной от USD, недостающие пары вычисляются по общим курсам
из кеша.

## Данные и курсы

При запуске приложение создаёт пользовательские файлы и кеш в каталоге `data/`;
Parser Service создаёт файл истории после первого успешного обновления:

- `users.json` — ID и имена пользователей, хеши паролей с солью и даты
  регистрации;
- `portfolios.json` — кошельки и балансы пользователей;
- `rates.json` — локальный кеш курсов для Core Service;
- `exchange_rates.json` — история успешных измерений Parser Service.

Путь к каталогу данных можно изменить переменной окружения
`VALUTATRADE_DATA_DIR`. Parser Service запрашивает BTC, ETH и SOL у CoinGecko
пакетно по ID монет, а EUR, GBP и RUB — у ExchangeRate-API. Курсы в истории
имеют формат JSON-массива; ID записи состоит из валютной пары и времени в UTC.
Повторный замер с тем же ID не добавляется. Запись истории выполняется через
временный файл с последующей атомарной заменой.

Пример элемента `exchange_rates.json`:

```json
{
  "id": "BTC_USD_2025-10-10T12:00:00Z",
  "from_currency": "BTC",
  "to_currency": "USD",
  "rate": 59337.21,
  "timestamp": "2025-10-10T12:00:00Z",
  "source": "CoinGecko",
  "meta": {
    "raw_id": "bitcoin",
    "request_ms": 124,
    "status_code": 200
  }
}
```

ExchangeRate-API при базовой валюте USD возвращает количество единиц фиата за
1 USD. Поэтому сервис берёт обратное значение, чтобы хранить курс в принятом в
проекте направлении: USD за одну единицу валюты (например, EUR→USD).

`rates.json` — снимок текущих курсов. В `pairs` для каждой пары остаётся одна
последняя запись, а `last_refresh` показывает время последнего обновления
снимка:

```json
{
  "pairs": {
    "EUR_USD": {
      "rate": 1.0786,
      "updated_at": "2025-10-10T12:00:00Z",
      "source": "ExchangeRate-API"
    },
    "BTC_USD": {
      "rate": 59337.21,
      "updated_at": "2025-10-10T12:00:00Z",
      "source": "CoinGecko"
    }
  },
  "last_refresh": "2025-10-10T12:00:01Z"
}
```

`updated_at` пары отмечает время, когда Parser Service положил курс в кеш.
При обновлении пара заменяется только если её `updated_at` новее сохранённого.
Parser Service записывает пары в обоих направлениях. Core Service читает только
этот файл; `get-rate`, `buy`, `sell` и `show-portfolio` не обращаются к внешним
API. TTL применяется к `updated_at` каждой пары и берётся из
`rates_ttl_seconds` в `SettingsLoader` (по умолчанию пять минут). Когда TTL
истёк, запустите `update-rates`.

Если `EXCHANGERATE_API_KEY` не задан или один из провайдеров недоступен,
обновление выполняется частично: успешные и проверенные ответы сохраняются,
ошибка записывается в `logs/actions.log` и выводится в CLI. Локальная история
не пополняется фиктивными значениями.

`ParserConfig` хранит URL API, базовую валюту, списки отслеживаемых кодов,
соответствия тикеров ID CoinGecko и пути к обоим файлам курсов. Ключи
`EXCHANGERATE_API_KEY` и `COINGECKO_API_KEY` читаются из окружения; ключ
ExchangeRate-API не хранится в исходном коде. Таймаут запроса по умолчанию —
10 секунд. `BaseApiClient.fetch_rates()` унифицирует ответ клиентов в словарь
`{"BTC_USD": 59337.21}`; HTTP-запросы выполняются через `requests`, а сетевые
ошибки и ответы с неуспешным статусом преобразуются в `ApiRequestError`.
`RatesUpdater.run_update()` последовательно опрашивает клиентов, объединяет
проверенные курсы и обновляет историю и снимок `rates.json`. Если один источник
недоступен, Parser Service сохраняет результаты остальных и возвращает отчёт о
частичном обновлении.

Настройки `data_dir`, `rates_ttl_seconds`, `rates_timeout_seconds`,
`rates_update_interval_seconds`, `default_base_currency`, а также путь и
параметры логирования задаются в `[tool.valutatrade]` в `pyproject.toml`.
Таймаут запроса можно изменить через `VALUTATRADE_RATES_TIMEOUT_SECONDS`, а
интервал планировщика — через `VALUTATRADE_RATES_UPDATE_INTERVAL_SECONDS`.
`SettingsLoader` и `DatabaseManager` используют Singleton через `__new__` и
создаются при первом обращении. JSON-хранилище сериализует операции изменения
данных внутри процесса и заменяет файлы атомарно.

## Ошибки и журнал действий

- При нехватке валюты `InsufficientFundsError` сообщает доступный и требуемый
  балансы.
- Неизвестный или некорректный код вызывает `CurrencyNotFoundError`; CLI выводит
  поддерживаемые коды и пример команды `get-rate`.
- Если курс отсутствует или устарел в `rates.json`, Core сообщает запустить
  `update-rates`.
- Если провайдер недоступен или вернул некорректный ответ, `ApiRequestError`
  содержит источник и причину; CLI предлагает повторить запрос позже и
  проверить подключение. Для ExchangeRate-API CLI подсказывает, когда нужно
  задать или проверить API-ключ.

Операции регистрации, входа, покупки, продажи, запросы курсов и обновления
Parser Service записываются в `logs/actions.log`. Журнал содержит время,
источник, результат, измеренные пары и ошибки API; пароли и ключи в него не
попадают. Уровень по умолчанию — `INFO`, ротация — 1 МБ с тремя резервными
файлами. Уровень можно переключить на `DEBUG` через `log_level` в
`[tool.valutatrade]` или `VALUTATRADE_LOG_LEVEL`.

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
├── parser_service/
│   ├── config.py      # API-ключи и параметры обновления
│   ├── api_clients.py # CoinGecko и ExchangeRate-API
│   ├── updater.py     # обновление истории и Core-кеша
│   ├── storage.py     # атомарное хранилище exchange_rates.json
│   └── scheduler.py   # периодический запуск
├── decorators.py      # логирование операций
├── logging_config.py  # настройка ротации журнала
└── cli/
    └── interface.py  # интерактивная командная оболочка
main.py                # запуск из исходников
```
