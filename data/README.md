# Data directory

The application creates the runtime files `users.json`, `portfolios.json`,
`rates.json`, and `exchange_rates.json` here when they are needed. Runtime JSON
files are ignored by Git because they contain account and portfolio data.

The `*.json.example` files document each file's initial JSON shape and contain
no user data. They are examples only; the application does not read them.
