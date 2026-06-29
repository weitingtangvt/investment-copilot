# Investment Copilot

Investment Copilot is a local-first research and portfolio analytics workspace for active equity investors. It combines watchlists, market research, weekly review workflows, portfolio performance analytics, broker trade import, and decision review tools in a Flask web app.

This repository is a sanitized English demo copy. It does not include any personal holdings, broker statements, uploaded files, API keys, cached market data, screenshots, or private account information.

## What It Does

- Tracks a watchlist and research pipeline for public equities.
- Imports broker trade files and builds a structured trade ledger.
- Reconstructs portfolio views and performance analytics from ledger data.
- Shows weekly portfolio review pages with holdings, P&L, trades, benchmarks, and decision notes.
- Provides sell-decision review and counterfactual analysis.
- Includes stock detail pages, market data charts, factor analysis, filings, news, and LLM-assisted research workflows.
- Stores data locally by default so private trading records are not committed to the repository.

## Core Screens

- **Portfolio Performance**: equity curve, drawdowns, benchmark comparison, risk metrics, contribution analysis, trade analytics, and turnover review.
- **Weekly Review**: weekly portfolio state, holdings table, P&L, trade summary, account NAV entry, and review notes.
- **Sell Decision Review**: counterfactual analysis across mark horizons such as 30D, 60D, 90D, and current mark.
- **Broker Import**: CSV-based trade ingestion for broker activity files.
- **Watchlist**: candidate tracking, filings, commentary, price charts, and research status.
- **US Screener**: market-mover and technical chart workspace.
- **Research Workspace**: LLM-assisted research workflows and saved research history.

## Tech Stack

- Python 3.11+
- Flask
- Pandas
- AkShare and other market-data adapters
- Chart.js / browser-side JavaScript
- SQLite-backed local caches where enabled

## Quick Start

```powershell
git clone https://github.com/weitingtangvt/investment-copilot.git
cd investment-copilot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python web/app.py
```

Open the local URL printed by Flask, usually:

```text
http://127.0.0.1:5001
```

## Configuration

Start from the example config:

```powershell
Copy-Item config.example.json config.json
```

Then add API keys or model settings as needed. Keep `config.json` local and do not commit personal credentials.

Useful environment variables:

```powershell
$env:FLASK_SECRET_KEY="replace-with-a-local-dev-secret"
$env:INVESTMENT_ASSISTANT_ENV="development"
```

## Data Privacy

The app is designed to keep user-specific data outside the repository. Runtime data belongs under `data/` or another local data directory.

This sanitized copy intentionally excludes:

- Personal holdings and broker ledgers
- Broker CSV uploads
- Weekly review records
- Account NAV history
- Cash-flow records
- Local caches and screenshots
- Logs and debug artifacts
- Private community/feed integrations
- API keys and local machine paths

The included `data/README.md` explains that runtime data is intentionally empty.

## Repository Layout

```text
core/                 Domain logic, analytics, storage, importers, market data
utils/                Data-source utilities and display helpers
web/                  Flask app, feature modules, templates, static assets
scripts/tools/        Utility scripts and validation helpers
data/                 Empty runtime-data placeholder
README-SANITIZED.md   Notes about the sanitized copy
```

## Verification

The sanitized copy was checked with:

```powershell
python scripts\tools\check_text_encoding.py
python -m compileall -q core utils web scripts
```

The Flask app import smoke test also passes:

```powershell
python - <<'PY'
from web.app import app
print(len(app.url_map._rules))
PY
```

## Notes

This is a demo/shareable version of a personal investment assistant. Some integrations are represented by compatibility stubs so the app can load without private community or account-specific services. Market-data availability depends on the configured providers and network access.
