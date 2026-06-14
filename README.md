# Banking Transaction Processor

A crash-resilient batch transaction processor for a simple banking service. Each day, a company drops a CSV file of transfers into an inbox folder. The processor validates each transfer against live account balances, rejects any that would cause an overdraft, writes results atomically to disk, and archives the input file.

---

## Getting started

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — manages the Python version and virtual environment

### Install

```bash
git clone <repository-url>
cd mable
uv sync
```

`uv sync` reads `pyproject.toml`, installs the correct Python version if needed, and creates a `.venv` with all dependencies.

### Commands

| Command | What it does |
|---|---|
| `make reset` | Restore inbox and balances to the initial sample state |
| `make process` | Run the processor for tenant `mable` |
| `make test` | Run the pytest unit test suite |

To process a different tenant, call the script directly:

```bash
uv run python process.py tenant=<name>
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

## How it works

The processor runs four sequential phases. **No data is written to disk until all transactions have been simulated successfully in memory.**

### Phase 1 — Validation and setup

- Checks for a crash-recovery sentinel from any previous run and completes it if found (see [Crash recovery](#crash-recovery)).
- Scans `data/inbox/` for `{tenant}_transactions.csv`. Exits with an error if zero or more than one file is found.
- Creates `{tenant}_account_balances.csv` and `{tenant}_transaction_log.csv` with headers if they do not exist.

### Phase 2 — Load balances into memory

- Reads `{tenant}_account_balances.csv` into a Python `dict`.
- Account numbers are stored as strings to protect leading digits.
- Balances are stored as `decimal.Decimal` to avoid floating-point precision loss.

### Phase 3 — In-memory simulation

Streams each row of the transaction file and evaluates it in order:

| Check | Outcome |
|---|---|
| Row has fewer than 3 columns, amount is non-numeric / non-positive, or an account number is not exactly 16 digits | Transaction fails — `FAILED - MALFORMED` |
| Either account number is not present in the balance dict | Transaction fails — `FAILED - INVALID_ACCOUNT` |
| Sender balance after transfer would drop below $0 | Transaction fails — `FAILED - OVERDRAWN` |
| All checks pass | Transaction succeeds — `SUCCESS`, balances mutated in memory |

**Transaction failure vs process failure:**

- A **transaction failure** is a per-row outcome. The failed row is recorded in the log with the appropriate status and processing continues with the next row. No balances are changed for that row.
- A **process failure** is a fatal condition that stops the script entirely before any data is written to disk. Process failures occur during Phase 1 (missing or ambiguous inbox file) or if an unhandled exception is raised at any point. The balance file and transaction log remain exactly as they were before the script started.

### Phase 4 — Atomic commit and archive

Only reached if Phase 3 completes without an unhandled exception:

1. **Append log** — all log entries are appended to `{tenant}_transaction_log.csv` in a single pass.
2. **Atomic balance swap** — updated balances are written to a `.tmp` staging file, then `os.replace()` swaps it over the live file in one atomic filesystem operation.
3. **Write sentinel** — a `{tenant}.committed` file is written recording the inbox path, guarding against the crash window between the balance swap and the archive.
4. **Archive** — the inbox file is moved to `data/archive/` with a UTC timestamp suffix.
5. **Clear sentinel** — the sentinel file is deleted, marking the run as fully complete.

---

## Crash recovery

The design guarantees that a crash at any point leaves the system in a safe, restartable state.

### During Phase 3 (simulation)

All work happens in memory. If the process dies mid-simulation — power loss, OOM kill, unhandled exception — nothing has been written to disk. The balance file and transaction log are untouched. Restarting the script reruns the full simulation from scratch with no side effects.

### During Phase 4, step 2 (balance write)

Balances are written to a `.tmp` file before the swap. If the process dies after writing the temp file but before `os.replace()`, the live balance file is intact. The orphaned `.tmp` file is overwritten on the next run. If the process dies mid-write of the `.tmp` file, the partial file is never swapped in.

### During Phase 4, between balance write and archive (sentinel)

This is the only crash window where balances have been updated but the inbox file has not yet been moved. Without protection, the next run would find the inbox file still present, reload the already-updated balances, and apply the same transactions a second time — corrupting every balance.

This is prevented by the sentinel file (`data/{tenant}.committed`). On startup, before Phase 1, the script checks for a sentinel. If one exists, the previous run committed balances but did not finish archiving. The script completes the archive step, deletes the sentinel, logs a `[RECOVERY]` message, and then proceeds normally with a clean inbox.

```
[append log] → [write .tmp] → [os.replace] → [write sentinel] → [archive] → [clear sentinel]
                    ↑               ↑                ↑                ↑
               .tmp file      Balance file      Next run         Next run
               discarded       stays intact     detects &        proceeds
                                                recovers         normally
```

---

## Data models

Defined in [models.py](models.py) using Pydantic v2.

| Model | Purpose |
|---|---|
| `BalanceRecord` | Validates a single row from the account balances CSV |
| `Transaction` | Validates a single transaction row — enforces 16-digit account numbers and a positive amount |
| `TransactionLogEntry` | Represents one row written to the audit log |

Unit tests cover the four core logic functions — `parse_tenant_arg`, `parse_balance_row`, `parse_transaction_row`, and `process_transaction` — with no filesystem dependencies.
