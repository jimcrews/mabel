from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator


def validate_account_number(v: str) -> str:
    """Reject any account that is not exactly 16 numeric digits."""
    if not v.isdigit() or len(v) != 16:
        raise ValueError(f"Account number must be exactly 16 digits, got '{v}'")
    return v


class BalanceRecord(BaseModel):
    account: str
    balance: Decimal

    @field_validator("account")
    @classmethod
    def account_must_be_16_digits(cls, v: str) -> str:
        return validate_account_number(v.strip())


class Transaction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    from_account: str
    to_account: str
    amount: Decimal

    @field_validator("from_account", "to_account")
    @classmethod
    def account_must_be_16_digits(cls, v: str) -> str:
        return validate_account_number(v)

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Amount must be positive")
        return v


class TransactionLogEntry(BaseModel):
    from_account: str
    to_account: str
    amount: Decimal
    status: str
    timestamp: str
