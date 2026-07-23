# Contributing

Thanks for your interest in improving `mac-vendors-client`.

## Development setup

```bash
git clone https://github.com/mac-vendors/mac-vendors-client.git
cd mac-vendors-client
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install
```

## Before opening a pull request

Run the same checks CI runs:

```bash
ruff check .
ruff format --check .
mypy src/
pytest
```

- Keep the code fully typed. The package ships a `py.typed` marker and CI runs
  `mypy` in strict mode.
- Add or update tests for any behavior change.
- Follow the existing commit-message style (`type: summary`).

## Reporting issues

Use the GitHub issue tracker. For security reports, see [SECURITY.md](SECURITY.md).
