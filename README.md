# Civic Assess

This is an agentic chatbot that researches and provides cited responses to your civic questions.

## Expected behavior

- Civic and political questions proceed to evidence-backed research.
- Follow-ups use conversation context without changing the user's framing.
- Ambiguous, loaded, or unsafe requests may require a clarified or reframed query;
  research continues only after explicit approval.
- Disallowed requests are refused, while unrelated requests are redirected toward the
  application's civic scope.
- Research checks local evidence before searching the web and follows material
  evidence gaps within bounded rounds.
- Answers distinguish supported findings from unresolved details and include an
  Evidence Strength explanation with linked sources.
- The Gradio sidebar shows live model, research, repair, and token-usage events.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the runtime structure and
[DESIGN.md](DESIGN.md) for the major decisions and tradeoffs.

## Quickstart

### Prerequisites

- Python 3.13 or 3.14
- An API key for the LLM provider selected in the TOML configuration
- A Tavily API key for live research

The included example configuration uses OpenAI. The application also contains
Groq and Anthropic adapters. To change a role's model, edit its `provider` and
`model` values in the TOML. To configure fallback models, add another
`[[routes.<role>]]` entry below the first; candidates are attempted from top to
bottom in priority order.

### Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp env/.env.example env/.env.development
cp config/environments/example.toml config/environments/development.toml
```

The run command uses `APP_ENV=development`, so both destination filenames must be
exactly as shown. Copy the examples rather than renaming them.

Edit `env/.env.development` and provide `OPENAI_API_KEY` and `TAVILY_API_KEY`.
If you change providers in `development.toml`, provide the corresponding provider
credentials as well. Startup fails when any provider referenced by an active or
fallback route lacks its configured API key. Treat `TAVILY_API_KEY` as required for
the quickstart; without it the UI can initialize, but a new conversation cannot
acquire web evidence.

For local use, disable login in `env/.env.development`:

```dotenv
REQUIRE_AUTHENTICATION=0
```

The default hosted configuration enables authentication and requires both
`GRADIO_USERNAME` and `GRADIO_PASSWORD`.

### Run

```bash
APP_ENV=development .venv/bin/python -m app.ui
```

Open `http://127.0.0.1:7860`. The embedding model is downloaded on first use, so
the initial startup may take longer than subsequent runs.

## Configuration

`APP_ENV` selects a matched pair of local files:

- `env/.env.<environment>` contains credentials and runtime settings.
- `config/environments/<environment>.toml` assigns ordered model candidates to
  each agent role.



Model candidates are attempted from top to bottom within each role:

```toml
[api_keys]
openai = "OPENAI_API_KEY"
groq = "GROQ_API_KEY"

[[routes.validator]]
provider = "openai"
model = "gpt-4.1-mini"
temperature = 0.0
timeout_seconds = 30
max_retries = 2

[[routes.validator]]
provider = "groq"
model = "llama-3.3-70b-versatile"
temperature = 0.0
timeout_seconds = 30
max_retries = 2
```

Both `OPENAI_API_KEY` and `GROQ_API_KEY` must then be present in
`env/.env.development`, even though Groq is only a fallback.

## Tests

Run the local suite without external provider calls:

```bash
.venv/bin/python -m pytest -m "not live"
```

Run the two opt-in provider smoke tests using the development configuration:

```bash
RUN_LIVE_TESTS=1 APP_ENV=development \
  .venv/bin/python -m pytest -m live
```

See [tests/README.md](tests/README.md) for test scope and markers.

## Evaluations

The evaluation harness separates inexpensive routing repeatability from expensive
end-to-end research quality:

```bash
APP_ENV=development .venv/bin/python -m evals.run routing --runs 3
APP_ENV=development .venv/bin/python -m evals.run scenarios --runs 1
```

The committed reports currently show:

- 75 routing decisions across 25 labeled cases
- 24/25 cases matching the expected modal disposition
- 24/25 cases producing a repeatable disposition across three runs
- 5/5 required project scenarios passing their scenario checks

Full results are available in
[the routing report](evals/reports/request_routing.md) and
[the scenario report](evals/reports/rubric_scenarios.md). See
[evals/README.md](evals/README.md) for valid scenario IDs and targeted commands.

## Quality checks

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m pytest -m "not live"
```

## Demonstration

- Live Gradio demo: _add before submission_
- Five-scenario Loom walkthrough: _add before submission_
