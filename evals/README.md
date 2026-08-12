# Evaluation

Complete the root quickstart first. Evaluations require both `env/.env.development` and `config/environments/development.toml`. The environment file must contain credentials for every LLM provider referenced by the TOML configuration. Research scenarios also require `TAVILY_API_KEY`.

- `routing` runs labeled civic, boundary, safety, neutrality, and prompt-manipulation cases. Its report includes accuracy, repeatability, a Markdown decision matrix, failures, and proposed reframes.
- `scenarios` runs the five assignment scenarios end to end. Each turn has explicit expectations for routing, evidence, answer quality, unresolved gaps, and perspective-oriented research angles.

Use the same `APP_ENV` configuration as the application:

```bash
APP_ENV=development .venv/bin/python -m evals.run routing --runs 3
APP_ENV=development .venv/bin/python -m evals.run scenarios --runs 1
APP_ENV=development .venv/bin/python -m evals.run scenarios --case debt-ceiling-2023
```

`--case` may be repeated to run any selection of these five scenario IDs:

- `debt-ceiling-2023`
- `presidential-primaries-2024`
- `affirmative-action-decision`
- `boundary-conversation`
- `immigration-policy-debate`

Reports are written to `evals/reports/`. 