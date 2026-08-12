# Tests

## Run

```bash
# Local suite. no provider calls
.venv/bin/python -m pytest -m "not live"

# Live smoke tests
RUN_LIVE_TESTS=1 APP_ENV=development \
  .venv/bin/python -m pytest -m live
```

Live tests and evaluations share `env/.env.development` and `config/environments/development.toml`. API credentials must be provided for all providers in the `.toml`. Research evaluations additionally require `TAVILY_API_KEY`.

## Coverage

The suite covers input boundaries and provider fallback, context resolution and reframe safety, conversation persistence and concurrency, evidence-gap research, answer auditing and citation rendering, and the Gradio progress boundary.

Markers describe test scope: `unit`, `integration`, `e2e`, `live`, `smoke`, `regression`, `security`, and `evaluation`. Live tests are opt-in and skipped unless `RUN_LIVE_TESTS=1`.
