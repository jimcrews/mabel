from decimal import Decimal

import pytest
from pydantic import ValidationError

from process import (
    parse_tenant_arg,
    parse_balance_row,
    parse_transaction_row,
    process_transaction,
)
from models import Transaction


# ---------------------------------------------------------------------------
# parse_tenant_arg
# ---------------------------------------------------------------------------

def test_parse_tenant_arg_valid():
    assert parse_tenant_arg(["script.py", "tenant=mable"]) == "mable"


def test_parse_tenant_arg_accepts_any_tenant_name():
    assert parse_tenant_arg(["script.py", "tenant=company_x"]) == "company_x"


def test_parse_tenant_arg_missing_arg_exits():
    with pytest.raises(SystemExit):
        parse_tenant_arg(["script.py"])


def test_parse_tenant_arg_wrong_key_exits():
    with pytest.raises(SystemExit):
        parse_tenant_arg(["script.py", "company=mable"])


def test_parse_tenant_arg_empty_value_exits():
    with pytest.raises(SystemExit):
        parse_tenant_arg(["script.py", "tenant="])


def test_parse_tenant_arg_no_equals_exits():
    with pytest.raises(SystemExit):
        parse_tenant_arg(["script.py", "mable"])


# ---------------------------------------------------------------------------
# parse_balance_row
# ---------------------------------------------------------------------------

def test_parse_balance_row_valid():
    record = parse_balance_row(["1111234522226789", "5000.00"])
    assert record.account == "1111234522226789"
    assert record.balance == Decimal("5000.00")


def test_parse_balance_row_strips_whitespace():
    record = parse_balance_row(["  1111234522226789  ", "  5000.00  "])
    assert record.account == "1111234522226789"
    assert record.balance == Decimal("5000.00")


def test_parse_balance_row_bad_amount_raises():
    with pytest.raises(ValidationError):
        parse_balance_row(["1111234522226789", "not-a-number"])


def test_parse_balance_row_missing_column_raises():
    with pytest.raises((IndexError, ValidationError)):
        parse_balance_row(["1111234522226789"])


def test_parse_balance_row_account_too_short_raises():
    with pytest.raises(ValidationError):
        parse_balance_row(["12345", "5000.00"])


def test_parse_balance_row_account_non_numeric_raises():
    with pytest.raises(ValidationError):
        parse_balance_row(["ABCD1234EFGH5678", "5000.00"])


# ---------------------------------------------------------------------------
# parse_transaction_row
# ---------------------------------------------------------------------------

def test_parse_transaction_row_valid():
    tx = parse_transaction_row(["1111234522226789", "1212343433335665", "500.00"])
    assert tx.from_account == "1111234522226789"
    assert tx.to_account == "1212343433335665"
    assert tx.amount == Decimal("500.00")


def test_parse_transaction_row_strips_whitespace():
    tx = parse_transaction_row(["  1111234522226789  ", "  1212343433335665  ", "  500.00  "])
    assert tx.from_account == "1111234522226789"
    assert tx.to_account == "1212343433335665"


def test_parse_transaction_row_from_account_too_short_raises():
    with pytest.raises(ValidationError):
        parse_transaction_row(["12345", "1212343433335665", "500.00"])


def test_parse_transaction_row_to_account_too_short_raises():
    with pytest.raises(ValidationError):
        parse_transaction_row(["1111234522226789", "12345", "500.00"])


def test_parse_transaction_row_non_numeric_account_raises():
    with pytest.raises(ValidationError):
        parse_transaction_row(["ABCD1234EFGH5678", "1212343433335665", "500.00"])


def test_parse_transaction_row_zero_amount_raises():
    with pytest.raises(ValidationError):
        parse_transaction_row(["1111234522226789", "1212343433335665", "0"])


def test_parse_transaction_row_negative_amount_raises():
    with pytest.raises(ValidationError):
        parse_transaction_row(["1111234522226789", "1212343433335665", "-10.00"])


def test_parse_transaction_row_non_numeric_amount_raises():
    with pytest.raises(ValidationError):
        parse_transaction_row(["1111234522226789", "1212343433335665", "abc"])


# ---------------------------------------------------------------------------
# process_transaction
# ---------------------------------------------------------------------------

TIMESTAMP = "2026-01-01T00:00:00+00:00"

ACC_A = "1111234522226789"
ACC_B = "1212343433335665"
ACC_C = "3212343433335755"


def make_tx(from_acc: str, to_acc: str, amount: str) -> Transaction:
    return Transaction(from_account=from_acc, to_account=to_acc, amount=amount)


def test_process_transaction_success_updates_balances():
    balance_dict = {ACC_A: Decimal("1000.00"), ACC_B: Decimal("500.00")}
    tx = make_tx(ACC_A, ACC_B, "200.00")
    entry = process_transaction(tx, balance_dict, TIMESTAMP)

    assert entry.status == "SUCCESS"
    assert balance_dict[ACC_A] == Decimal("800.00")
    assert balance_dict[ACC_B] == Decimal("700.00")


def test_process_transaction_exact_balance_succeeds():
    # Transferring the exact available balance should be allowed ($0 floor, not below $0)
    balance_dict = {ACC_A: Decimal("500.00"), ACC_B: Decimal("0.00")}
    tx = make_tx(ACC_A, ACC_B, "500.00")
    entry = process_transaction(tx, balance_dict, TIMESTAMP)

    assert entry.status == "SUCCESS"
    assert balance_dict[ACC_A] == Decimal("0.00")
    assert balance_dict[ACC_B] == Decimal("500.00")


def test_process_transaction_overdrawn_leaves_balances_unchanged():
    balance_dict = {ACC_A: Decimal("100.00"), ACC_B: Decimal("0.00")}
    tx = make_tx(ACC_A, ACC_B, "150.00")
    entry = process_transaction(tx, balance_dict, TIMESTAMP)

    assert entry.status == "FAILED - OVERDRAWN"
    assert balance_dict[ACC_A] == Decimal("100.00")
    assert balance_dict[ACC_B] == Decimal("0.00")


def test_process_transaction_unknown_from_account():
    # ACC_C is a valid 16-digit account but absent from the balance dict
    balance_dict = {ACC_B: Decimal("500.00")}
    tx = make_tx(ACC_C, ACC_B, "100.00")
    entry = process_transaction(tx, balance_dict, TIMESTAMP)

    assert entry.status == "FAILED - INVALID_ACCOUNT"
    assert balance_dict[ACC_B] == Decimal("500.00")


def test_process_transaction_unknown_to_account():
    # ACC_C is a valid 16-digit account but absent from the balance dict
    balance_dict = {ACC_A: Decimal("500.00")}
    tx = make_tx(ACC_A, ACC_C, "100.00")
    entry = process_transaction(tx, balance_dict, TIMESTAMP)

    assert entry.status == "FAILED - INVALID_ACCOUNT"
    assert balance_dict[ACC_A] == Decimal("500.00")


def test_process_transaction_log_entry_fields():
    balance_dict = {ACC_A: Decimal("1000.00"), ACC_B: Decimal("0.00")}
    tx = make_tx(ACC_A, ACC_B, "250.00")
    entry = process_transaction(tx, balance_dict, TIMESTAMP)

    assert entry.from_account == ACC_A
    assert entry.to_account == ACC_B
    assert entry.amount == Decimal("250.00")
    assert entry.timestamp == TIMESTAMP
