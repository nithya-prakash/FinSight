from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

Strict = ConfigDict(extra="forbid")


class Customer(BaseModel):
    model_config = Strict
    customer_id: str
    full_name: str
    home_region: str
    segment: Literal["retail", "premium", "small_business"]
    created_at: datetime


class Account(BaseModel):
    model_config = Strict
    account_id: str
    customer_id: str
    account_type: Literal["checking", "savings", "credit_card"]
    currency: str
    opened_at: datetime


class MerchantCategory(BaseModel):
    model_config = Strict
    category_code: str
    description: str
    risk_tier: Literal["low", "medium", "high"]


class Merchant(BaseModel):
    model_config = Strict
    merchant_id: str
    merchant_name: str
    category_code: str
    region: str
    lat: float
    lon: float


class Device(BaseModel):
    model_config = Strict
    device_id: str
    customer_id: str | None
    device_type: Literal["mobile", "desktop", "pos_terminal", "atm"]


class Transaction(BaseModel):
    """Model-visible fields only. Ground truth lives in FraudLabel."""
    model_config = Strict
    transaction_id: str
    event_time: datetime
    customer_id: str
    account_id: str
    merchant_id: str | None
    counterparty_account_id: str | None
    amount: Decimal
    currency: str
    transaction_type: Literal["purchase", "transfer", "cash_withdrawal"]
    channel: Literal["online", "pos", "transfer", "atm"]
    device_id: str | None
    ip_region: str | None
    lat: float | None
    lon: float | None
    status: Literal["approved", "declined"]


class FraudLabel(BaseModel):
    model_config = Strict
    transaction_id: str
    is_fraud: bool
    scenario: str
    scenario_instance_id: str
    generator_version: str
    dataset_id: str
