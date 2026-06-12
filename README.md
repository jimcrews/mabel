# Banking Transaction Processor

A crash-resilient batch transaction processor for a simple banking service. Each day, a company drops a CSV file of transfers into an inbox folder. The processor validates each transfer against live account balances, rejects any that would cause an overdraft, writes results atomically to disk, and archives the input file.

---

## Getting started

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — used to manage the Python version and virtual environment

### Install

```bash
git clone <repository-url>
cd mable
uv sync
```

`uv sync` reads `pyproject.toml`, installs the correct Python version if needed, and creates a `.venv` with all dependencies.

### Run

Load the sample data then process the transactions:

```bash
make process  # run the processor for tenant=mable
```

To process a different tenant, call the script directly:

```bash
uv run python process.py tenant=<name>
```

### Test

```bash
make test
```

### Reset

Reset all data to initial state

```bash
make reset
```

---

## Directory structure

```
.
├── process.py                               # Main processing script
├── reset.py                                 # Development reset script
├── models.py                                # Pydantic data models
├── test_process.py                          # Unit tests
├── Makefile                                 # Convenience targets
└── data/
    ├── inbox/
    │   └── {tenant}_transactions.csv        # Drop incoming transaction files here
    ├── archive/
    │   └── {tenant}_transactions_YYYYMMDD_HHMMSS.csv  # Processed files (UTC timestamp)
    ├── {tenant}_account_balances.csv        # Live account balances (source of truth)
    └── {tenant}_transaction_log.csv         # Append-only audit log
```

---

## Make targets

| Command | What it does |
|---|---|
| `make process` | Run the processor for tenant `mable` |
| `make reset` | Restore inbox and balances to the initial sample state |
| `make test` | Run the pytest unit test suite |

Run any target from the project root:

```bash
make reset      # restore sample data
make process    # process today's transactions
make test       # run unit tests
```

To process a different tenant, call the script directly:

```bash
uv run python process.py tenant=acme
```

---

## How it works

The processor runs four sequential phases. **No data is written to disk until all transactions have been simulated successfully in memory.**

### Phase 1 — Validation and setup

- Scans `data/inbox/` for `{tenant}_transactions.csv`.
- Exits with an error if zero or more than one file is found.
- Creates `{tenant}_account_balances.csv` and `{tenant}_transaction_log.csv` with headers if they do not exist.

### Phase 2 — Load balances into memory

- Reads `{tenant}_account_balances.csv` into a Python `dict`.
- Account numbers are stored as strings to protect leading digits.
- Balances are stored as `decimal.Decimal` to avoid floating-point precision loss.

### Phase 3 — In-memory simulation

Streams each row of the transaction file and evaluates it in order:

| Check | Outcome |
|---|---|
| Row has fewer than 3 columns, or amount is non-numeric / non-positive, or an account number is not exactly 16 digits | Transaction fails — `FAILED - MALFORMED` |
| Either account number is not present in the balance dict | Transaction fails — `FAILED - INVALID_ACCOUNT` |
| Sender balance after transfer would drop below $0 | Transaction fails — `FAILED - OVERDRAWN` |
| All checks pass | Transaction succeeds — `SUCCESS`, balances mutated in memory |

**Transaction failure vs process failure:**

- A **transaction failure** is a per-row outcome. The failed row is recorded in the log with the appropriate status, and processing continues with the next row. No balances are changed for that row.
- A **process failure** is a fatal condition that stops the script entirely before any data is written to disk. Process failures occur during Phase 1 (missing or ambiguous inbox file) or if an unhandled exception is raised at any point. Because Phase 4 has not run, the balance file and transaction log remain exactly as they were before the script started.

### Phase 4 — Atomic commit and archive

Only reached if Phase 3 completes without an unhandled exception:

1. **Append log** — all `TransactionLogEntry` records are appended to `{tenant}_transaction_log.csv` in a single pass.
2. **Atomic balance swap** — updated balances are written to a `.tmp` staging file, then `os.replace()` swaps it over the live file in one atomic filesystem operation.
3. **Archive** — the inbox file is moved to `data/archive/` with a UTC timestamp suffix.

---

## Crash recovery

The design guarantees that a crash at any point leaves the system in a safe, restartable state.

### During Phase 3 (simulation)

All work happens in memory. If the process dies mid-simulation — power loss, OOM kill, unhandled exception — nothing has been written to disk. The balance file and transaction log are untouched. Restarting the script reruns the full simulation from scratch with no side effects.

### During Phase 4, step 2 (balance write)

Balances are written to a `.tmp` file before the swap. If the process dies after writing the temp file but before `os.replace()`, the live balance file is intact. The orphaned `.tmp` file is overwritten on the next run. If the process dies mid-write of the `.tmp` file, the partial file is never swapped in.

### Idempotency

The inbox file is only moved to `archive/` as the final step of Phase 4, after the balance swap has completed. A crashed script always leaves the inbox file in place. Re-running the script reprocesses the same input from a consistent starting state — no duplicate transactions, no data corruption.

```
Crash here:    [Phase 3 sim] → [append log] → [write .tmp] → [os.replace] → [archive]
                     ↑               ↑               ↑              ↑
               Safe to rerun   Log may be      .tmp file      Balance file
               from scratch    incomplete      discarded       stays intact
```

---

## Data models

Defined in [models.py](models.py) using Pydantic v2.

| Model | Purpose |
|---|---|
| `BalanceRecord` | Validates a single row from the account balances CSV |
| `Transaction` | Validates a single transaction row — rejects empty accounts and non-positive amounts |
| `TransactionLogEntry` | Represents one row written to the audit log |

---

## Running tests

```bash
make test
```

Unit tests cover the four core logic functions: `parse_tenant_arg`, `parse_balance_row`, `parse_transaction_row`, and `process_transaction`. These functions have no filesystem dependencies and can be called directly with plain Python values.
