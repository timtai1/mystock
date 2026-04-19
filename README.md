# mystock - Taiwan Stock Target Price & K-Line Dashboard

<div align="center">

![mystock](https://img.shields.io/badge/mystock-Taiwan%20Stocks-blue)
![Python](https://img.shields.io/badge/Python-3.9+-green)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)

**A local-only web dashboard that tracks broker target prices and daily K-line charts for a curated list of Taiwan stocks (TWSE + TPEX).**

</div>

---

## Purpose

This project consolidates two independent Taiwan-market data pipelines into a single, privately-hosted web dashboard:

1. **Broker target prices** pulled from CMoney's mobile API (daily snapshots of analyst ratings, target prices, and rationale summaries across the last 90 days).
2. **Daily OHLCV K-line data** pulled from TWSE's `STOCK_DAY` OpenAPI (for listed stocks) and FinMind's `TaiwanStockPrice` dataset (for OTC / 上櫃 stocks).

The web portal is read-only, binds to `127.0.0.1` only, and does not require an internet connection to browse data once cached.

---

## Security Notice

**IMPORTANT: this repository is structured so that no credentials need to be committed.**

The CMoney Bearer JWT is read from the `CMONEY_AUTH_TOKEN` environment variable at runtime. The token is sensitive (it grants access to your CMoney account's data feed). Do **not**:

- Paste the token into any file inside the repo.
- Echo the token into a shell history file.
- Share the token with others.

If you accidentally leak a token, regenerate it by re-capturing a fresh request from the CMoney mobile app.

The Flask server (`serve.py`) rejects any connection whose `remote_addr` is not `127.0.0.1` or `::1`, so the dashboard cannot be reached from other machines on your network.

---

## Features

- Daily scheduled ingestion of broker target prices into per-day JSON folders.
- Incremental daily K-line append (TWSE) plus full-history bootstrap (FinMind for TPEX).
- Web dashboard with searchable / sortable stock table showing:
  - 市 / 櫃 tag (TWSE listed vs TPEX over-the-counter)
  - Latest close price (overridden by K-line data if available)
  - Latest target price + broker + rating
  - 90-day median target and potential return
  - Free-text broker summaries in an expandable side panel
- K-line chart modal powered by KLineChart with Bollinger Bands and volume, Taiwan-standard red-up / green-down colouring, and drawing tools (segments, Fibonacci, rectangles, notes, etc.).
- Per-stock "重抓 K 線" button that re-runs the bootstrap for a single ticker.
- Prev / Next arrow navigation in the K-line modal for rapid browsing.
- Flask service pinned to `127.0.0.1:8765`; no remote access, no authentication state on disk.
- 90-day log retention with automatic pruning of old daily folders.

---

## Requirements

- Python 3.9 or higher
- pip (Python package manager)
- Internet access to reach:
  - `dtno.cmoney.tw` (target prices)
  - `www.twse.com.tw` (TWSE listed stock K-line)
  - `api.finmindtrade.com` (TPEX K-line)
  - `isin.twse.com.tw` (weekly refresh of `tw_stock_list.csv`)
- A valid CMoney Bearer JWT (see `Configuration` below)

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/timtai1/mystock.git
cd mystock
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

On Windows (PowerShell):

```powershell
git clone https://github.com/timtai1/mystock.git
cd mystock
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure the CMoney token

Capture a fresh Bearer JWT from the CMoney mobile app's network traffic and export it:

```bash
export CMONEY_AUTH_TOKEN="eyJhbGciOi..."      # paste your actual token
```

Append the same line to `~/.zshrc` (or `~/.bashrc`) to make it persist across shells.

### 3. Run the daily jobs

```bash
python fetch_target_price.py stocklist.txt          # broker target prices
python fetch_daily_kline.py                         # incremental daily K-line
```

### 4. Bootstrap historical K-line (first time, or after a data reset)

```bash
python fetch_daily_kline.py --bootstrap --months 13
```

### 5. Launch the web portal

```bash
python serve.py
```

A browser tab will open at `http://127.0.0.1:8765/`.

---

## Configuration

All configuration lives either in environment variables (for secrets) or at the top of each Python script (for non-secrets).

### Environment variables

| Name                 | Required | Purpose                                                                                  |
| -------------------- | -------- | ---------------------------------------------------------------------------------------- |
| `CMONEY_AUTH_TOKEN`  | Yes      | Bearer JWT for CMoney's `dtno/MobileCsv` endpoint. Refresh when it expires.              |
| `FINMIND_TOKEN`      | No       | FinMind API token for TPEX historical data. Anonymous quota (300 req/hr) is enough for a ~40-stock bootstrap; register for 600 req/hr if you track more. |

### In-script knobs (open each file to tweak)

| File                     | Constant            | Meaning                                                         |
| ------------------------ | ------------------- | --------------------------------------------------------------- |
| `fetch_target_price.py`  | `RETENTION_DAYS`    | Keep last N daily folders under `法人目標價_log_file/`.         |
| `fetch_target_price.py`  | `VERIFY_SSL`        | Set to `False` on corporate networks with a MITM root CA.       |
| `fetch_target_price.py`  | `INTERVAL_MS`       | Throttle between CMoney requests (default 1000ms).              |
| `fetch_daily_kline.py`   | `BOOTSTRAP_MONTHS`  | Default history depth on first bootstrap.                       |
| `fetch_daily_kline.py`   | `TWSE_INTERVAL_SEC` | Per-month TWSE request gap (default 1s).                        |
| `serve.py`               | `PORT`              | Local Flask port (default 8765).                                |
| `serve.py`               | `RECENT_DAYS`       | Time window for median / max target computations (default 90).  |

### Stock universe

`stocklist.txt` is the whitelist of ticker codes to track (one code per line). Edit it to add or remove stocks; the fetchers pick up the new list on the next run.

`tw_stock_list.csv` is an auto-refreshed master list from TWSE (上市 + 上櫃) used to resolve code → name and market. It is regenerated weekly by `fetch_target_price.py`.

---

## Daily Workflow

Once the initial bootstrap is done, a typical day looks like this:

```bash
source venv/bin/activate
python fetch_target_price.py stocklist.txt          # ~1-2 minutes for a typical list
python fetch_daily_kline.py                         # ~30 seconds incremental
python serve.py                                     # browse the results
```

You can also wire these up via `cron` or macOS `launchd` for hands-off daily ingestion.

---

## Project Structure

```
mystock/
├── fetch_target_price.py        # CMoney target-price pipeline (daily)
├── fetch_daily_kline.py         # TWSE / FinMind K-line pipeline (daily + bootstrap)
├── serve.py                     # Local Flask web service (127.0.0.1:8765)
├── index.html                   # Single-file vanilla-JS dashboard UI
├── stocklist.txt                # Your tracked ticker codes (one per line)
├── tw_stock_list.csv            # Master TWSE/TPEX code -> name -> market map
├── requirements.txt             # Python dependencies
├── readme.txt                   # Quick-reference commands (Chinese)
├── PROJECT.md                   # Internal design notes / incident history
└── LICENSE                      # MIT
```

Data directories are created on first run and are excluded from git:

```
法人目標價_log_file/             # Daily broker target-price snapshots
  └── 20260418/
      └── 001_2330_台積電.json
日K線_log_file/                   # Per-stock OHLCV history
  └── 2330.json
```

---

## Data Sources

| Source                                       | Used for                      | Notes                                                                                 |
| -------------------------------------------- | ----------------------------- | ------------------------------------------------------------------------------------- |
| CMoney `dtno/MobileCsv` (mobile API)         | Broker target prices          | Requires an expiring Bearer JWT captured from the iOS app.                            |
| TWSE OpenAPI `STOCK_DAY`                     | TWSE listed K-line (history + daily) | One request per stock per month for bootstrap, one per stock per day for incremental. |
| TPEX OpenAPI `tpex_mainboard_daily_close_quotes` | TPEX daily snapshot (today)   | The `d=` parameter is silently ignored by the server, so this endpoint cannot be used for historical data. |
| FinMind `TaiwanStockPrice` dataset           | TPEX historical K-line         | One API call per stock returns the full range. Anonymous limit: 300 req/hr.           |
| TWSE ISIN CSVs (`strMode=2` / `strMode=4`)   | Code -> name -> market mapping | Refreshed weekly into `tw_stock_list.csv`.                                            |

---

## Troubleshooting

### `[錯誤] 環境變數 CMONEY_AUTH_TOKEN 未設定`

You forgot to `export CMONEY_AUTH_TOKEN="..."` in the current shell. Either re-export it or add it to your shell rc file.

### All target-price fetches return HTTP 401 / 403

Your Bearer token has expired. Re-capture a fresh one from the CMoney mobile app and re-export.

### SSL verification errors on a corporate network

Your company's proxy replaces the TLS certificate with an internal root CA. Set `VERIFY_SSL = False` at the top of `fetch_target_price.py` (and the equivalent flag in `fetch_daily_kline.py`), or point the constant at your company's CA bundle PEM file.

### TPEX stocks show no K-line history

If the bootstrap JSON looks malformed (all rows identical, or NaN), the file predates the FinMind switch. Delete it and re-run:

```bash
python fetch_daily_kline.py --bootstrap --stock 8299 --months 13
```

### Port 8765 is already in use

Edit `PORT` at the top of `serve.py`, or kill the other process that's bound to 8765.

---

## Development Notes

See `PROJECT.md` for a detailed design journal, including:

- Why TPEX historical data was migrated off the TPEX OpenAPI (the `d=` parameter bug).
- The target-price schema and field indices.
- Past incidents and how they were resolved.

The UI is deliberately a single-file `index.html` with vanilla JS so that no build step is required. All server state is file-based JSON.

---

## Contributing

This is primarily a personal tool, but pull requests are welcome. Please:

1. Fork the repository.
2. Create a feature branch (`git checkout -b feature/your-feature`).
3. Commit your changes (`git commit -m 'Add your feature'`).
4. Push to the branch (`git push origin feature/your-feature`).
5. Open a Pull Request.

---

## License

This project is licensed under the MIT License - see the `LICENSE` file for details.

---

## Disclaimer

This tool is provided as-is for personal research and educational use. It consumes third-party APIs (CMoney, TWSE, TPEX, FinMind) whose terms of service may change at any time. You are responsible for:

- Complying with each data provider's terms of use.
- Treating market data as informational only, not as financial advice.
- Safeguarding any credentials (including Bearer tokens) that you use to access those APIs.

The author accepts no responsibility for investment decisions made on the basis of data surfaced by this tool, or for API access being revoked due to terms-of-service violations.

---

<div align="center">

[Report Bug](https://github.com/timtai1/mystock/issues) · [Request Feature](https://github.com/timtai1/mystock/issues)

</div>
