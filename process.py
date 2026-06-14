import csv
import glob
import os
import shutil
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from models import BalanceRecord, Transaction, TransactionLogEntry


def parse_tenant_arg(args: list[str]) -> str:
    """Extract the tenant name from a CLI args list in 'tenant=<name>' form.

    Decoupled from sys.argv so it can be called directly in tests.
    Exits with an error if the argument is missing, malformed, or uses the wrong key.
    """
    if len(args) < 2:
        print("[ERROR] Missing argument. Usage: python process.py tenant=<name>")
        sys.exit(1)
    arg = args[1]
    parts = arg.split("=", 1)
    if len(parts) != 2 or parts[0] != "tenant" or not parts[1]:
        print(f"[ERROR] Malformed argument '{arg}'. Expected format: tenant=<name>")
        sys.exit(1)
    return parts[1]


def parse_tenant() -> str:
    """Read the tenant name from sys.argv."""
    return parse_tenant_arg(sys.argv)


def ensure_file_with_header(path: str, header: str) -> None:
    """Create the file at path with a header row if it is missing or empty."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="") as f:
            f.write(header + "\n")


def validate_files(tenant: str) -> str:
    """Confirm exactly one inbox transaction file exists and initialise support files.

    Returns the path to the inbox transaction file.
    Exits with an error if zero or multiple transaction files are found.
    """
    matches = glob.glob(f"data/inbox/{tenant}_transactions.csv")
    if len(matches) == 0:
        print(f"[ERROR] No transaction file found for tenant '{tenant}'")
        sys.exit(1)
    if len(matches) > 1:
        print(f"[ERROR] Ambiguous transaction files found for tenant '{tenant}'")
        sys.exit(1)

    ensure_file_with_header(f"data/{tenant}_account_balances.csv", "Account,Balance")
    ensure_file_with_header(
        f"data/{tenant}_transaction_log.csv", "From,To,Amount,Status,Timestamp"
    )

    return matches[0]


def parse_balance_row(row: list[str]) -> BalanceRecord:
    """Parse a single CSV row into a BalanceRecord.

    Raises ValidationError or IndexError on malformed input.
    Extracted so the parsing logic can be unit tested independently of file I/O.
    """
    return BalanceRecord(account=row[0].strip(), balance=row[1].strip())


def load_account_balances(tenant: str) -> dict[str, Decimal]:
    """Load the tenant's balance CSV into a dict keyed by account number string.

    Account numbers are kept as strings to protect leading digits.
    Balances are stored as Decimal to avoid float precision loss.
    Rows that fail validation are skipped with a warning.
    """
    balance_dict: dict[str, Decimal] = {}
    balance_path = f"data/{tenant}_account_balances.csv"

    with open(balance_path, newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or row[0].strip().lower() == "account":
                continue
            try:
                record = parse_balance_row(row)
                balance_dict[record.account] = record.balance
            except (ValidationError, IndexError, InvalidOperation) as e:
                print(f"[WARNING] Skipping malformed balance row {row}: {e}")

    return balance_dict


def parse_transaction_row(row: list[str]) -> Transaction:
    """Parse and validate a three-element CSV row into a Transaction model.

    Raises ValidationError if any field is missing, empty, or the amount is non-positive.
    Extracted so parsing logic can be unit tested without touching the filesystem.
    """
    return Transaction(from_account=row[0], to_account=row[1], amount=row[2])


def process_transaction(
    tx: Transaction, balance_dict: dict[str, Decimal], timestamp: str
) -> TransactionLogEntry:
    """Apply a validated transaction against the in-memory balance dict.

    Checks account existence and the $0 floor constraint in order.
    On success, mutates balance_dict in place and returns a SUCCESS entry.
    On failure, leaves balance_dict unchanged and returns the appropriate FAILED entry.
    Contains no I/O — safe to call directly in unit tests with a plain dict.
    """
    if tx.from_account not in balance_dict or tx.to_account not in balance_dict:
        return TransactionLogEntry(
            from_account=tx.from_account,
            to_account=tx.to_account,
            amount=tx.amount,
            status="FAILED - INVALID_ACCOUNT",
            timestamp=timestamp,
        )

    if balance_dict[tx.from_account] - tx.amount < 0:
        return TransactionLogEntry(
            from_account=tx.from_account,
            to_account=tx.to_account,
            amount=tx.amount,
            status="FAILED - OVERDRAWN",
            timestamp=timestamp,
        )

    balance_dict[tx.from_account] -= tx.amount
    balance_dict[tx.to_account] += tx.amount
    return TransactionLogEntry(
        from_account=tx.from_account,
        to_account=tx.to_account,
        amount=tx.amount,
        status="SUCCESS",
        timestamp=timestamp,
    )


def simulate_transactions(
    inbox_file: str, balance_dict: dict[str, Decimal]
) -> list[TransactionLogEntry]:
    """Stream the inbox CSV and simulate each transaction against the in-memory ledger.

    Delegates row parsing to parse_transaction_row and execution to process_transaction.
    All outcomes (success and failure) are collected and returned; no disk writes occur here.
    """
    ledger_log_entries: list[TransactionLogEntry] = []

    with open(inbox_file, newline="") as f:
        reader = csv.reader(f)
        for row_num, row in enumerate(reader, start=1):
            if not row or row[0].strip().lower() == "from":
                continue

            timestamp = datetime.now(timezone.utc).isoformat()

            if len(row) < 3:
                print(f"[WARNING] Row {row_num} is structurally corrupt: '{','.join(row)}'")
                ledger_log_entries.append(
                    TransactionLogEntry(
                        from_account=row[0].strip() if row else "",
                        to_account=row[1].strip() if len(row) > 1 else "",
                        amount=Decimal("0"),
                        status="FAILED - MALFORMED",
                        timestamp=timestamp,
                    )
                )
                continue

            try:
                tx = parse_transaction_row(row)
            except (ValidationError, InvalidOperation) as e:
                print(f"[WARNING] Row {row_num} failed validation: {e}")
                ledger_log_entries.append(
                    TransactionLogEntry(
                        from_account=row[0].strip(),
                        to_account=row[1].strip(),
                        amount=Decimal("0"),
                        status="FAILED - MALFORMED",
                        timestamp=timestamp,
                    )
                )
                continue

            entry = process_transaction(tx, balance_dict, timestamp)
            if entry.status != "SUCCESS":
                print(f"[WARNING] Row {row_num} {entry.status}: {tx.from_account} -> {tx.to_account} ({tx.amount}). Transaction skipped.")
            ledger_log_entries.append(entry)

    return ledger_log_entries


def append_log_entries(log_path: str, entries: list[TransactionLogEntry]) -> None:
    """Append all log entries to the transaction log CSV in append-only mode."""
    with open(log_path, "a", newline="") as f:
        writer = csv.writer(f)
        for entry in entries:
            writer.writerow([
                entry.from_account,
                entry.to_account,
                entry.amount,
                entry.status,
                entry.timestamp,
            ])


def sentinel_path(tenant: str) -> str:
    """Return the path of the commit sentinel file for a tenant."""
    return f"data/{tenant}.committed"


def write_sentinel(tenant: str, inbox_file: str) -> None:
    """Write a sentinel file recording which inbox file has been committed to disk.

    Written immediately after the balance swap so that a crash between the swap
    and the archive step can be detected and recovered on the next run.
    The sentinel stores the inbox path so recovery knows exactly which file to archive.
    """
    with open(sentinel_path(tenant), "w") as f:
        f.write(inbox_file)


def clear_sentinel(tenant: str) -> None:
    """Remove the sentinel file once archiving completes successfully."""
    path = sentinel_path(tenant)
    if os.path.exists(path):
        os.remove(path)


def recover_if_needed(tenant: str) -> None:
    """Detect and recover from a previous run that committed balances but did not archive.

    If a sentinel file exists the previous run wrote updated balances to disk but
    crashed before moving the inbox file to archive. Without recovery the next run
    would reload the already-updated balances and apply the same transactions again,
    causing double-spending. Recovery completes the archive step and removes the
    sentinel so the current run can proceed with a clean inbox.
    """
    path = sentinel_path(tenant)
    if not os.path.exists(path):
        return

    with open(path) as f:
        orphaned_inbox = f.read().strip()

    if os.path.exists(orphaned_inbox):
        print(f"[RECOVERY] Sentinel found — previous run committed but did not archive.")
        archive_name = archive_inbox_file(orphaned_inbox, tenant)
        print(f"[RECOVERY] Archived orphaned inbox file to {archive_name}")
    else:
        print(f"[RECOVERY] Sentinel found but inbox file already gone — clearing sentinel.")

    clear_sentinel(tenant)


def write_balances_atomic(balance_path: str, balance_dict: dict[str, Decimal]) -> None:
    """Write the updated balance dict to disk via an atomic temp-file swap.

    Writes to a .tmp staging file first, then calls os.replace to prevent
    a half-written balance file if the process is interrupted mid-write.
    """
    tmp_path = balance_path + ".tmp"
    with open(tmp_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Account", "Balance"])
        for account, balance in balance_dict.items():
            writer.writerow([account, balance])
    os.replace(tmp_path, balance_path)


def archive_inbox_file(inbox_file: str, tenant: str) -> str:
    """Move the processed inbox file into data/archive/ with a UTC timestamp suffix.

    Returns the destination archive path.
    """
    archive_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive_name = f"data/archive/{tenant}_transactions_{archive_ts}.csv"
    os.makedirs("data/archive", exist_ok=True)
    shutil.move(inbox_file, archive_name)
    return archive_name


def commit_results(
    tenant: str,
    inbox_file: str,
    balance_dict: dict[str, Decimal],
    ledger_log_entries: list[TransactionLogEntry],
) -> None:
    """Persist all simulation results to disk, then archive the input file.

    Execution order: append log → atomic balance swap → sentinel → archive → clear sentinel.
    The sentinel written between the balance swap and the archive allows a crash in that
    window to be detected and safely completed on the next run.
    """
    append_log_entries(f"data/{tenant}_transaction_log.csv", ledger_log_entries)
    write_balances_atomic(f"data/{tenant}_account_balances.csv", balance_dict)
    write_sentinel(tenant, inbox_file)
    archive_name = archive_inbox_file(inbox_file, tenant)
    clear_sentinel(tenant)

    print(f"[INFO] Committed {len(ledger_log_entries)} log entries.")
    print("[INFO] Balances updated atomically.")
    print(f"[INFO] Archived input to {archive_name}")


def main() -> None:
    tenant = parse_tenant()
    print(f"[INFO] Processing tenant: {tenant}")

    # PHASE 1: Validation and Setup
    recover_if_needed(tenant)
    inbox_file = validate_files(tenant)
    print(f"[INFO] Phase 1 complete. Transaction file: {inbox_file}")

    # PHASE 2: Load Account Balances
    balance_dict = load_account_balances(tenant)
    print(f"[INFO] Phase 2 complete. Loaded {len(balance_dict)} accounts.")

    # PHASE 3: Simulate Transactions
    ledger_log_entries = simulate_transactions(inbox_file, balance_dict)
    successes = sum(1 for e in ledger_log_entries if e.status == "SUCCESS")
    failures = len(ledger_log_entries) - successes
    print(f"[INFO] Phase 3 complete. {successes} success(es), {failures} failure(s).")

    # PHASE 4: Commit Results
    commit_results(tenant, inbox_file, balance_dict, ledger_log_entries)
    print("[INFO] Phase 4 complete. Done.")


if __name__ == "__main__":
    main()
