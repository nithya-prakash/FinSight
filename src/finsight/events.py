"""Event contract for topic finsight.transactions.v1 (see docs/event-contracts.md)."""
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from .generator.models import Transaction

SCHEMA_VERSION: Literal[1] = 1


class TransactionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str                    # stable: "evt_" + transaction_id -> idempotency key
    schema_version: Literal[1]
    event_time: datetime             # when the transaction happened (event time)
    produced_at: datetime            # when the producer emitted it (processing-side time)
    transaction: Transaction

    @model_validator(mode="after")
    def _consistent(self):
        if self.event_id != f"evt_{self.transaction.transaction_id}":
            raise ValueError("event_id must equal evt_<transaction_id>")
        if self.event_time != self.transaction.event_time:
            raise ValueError("event_time must equal transaction.event_time")
        return self


def make_event(txn: Transaction, produced_at: datetime | None = None) -> TransactionEvent:
    return TransactionEvent(event_id=f"evt_{txn.transaction_id}", schema_version=SCHEMA_VERSION,
                            event_time=txn.event_time, produced_at=produced_at or datetime.now(UTC),
                            transaction=txn)
