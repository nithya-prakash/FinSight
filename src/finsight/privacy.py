"""Identifier pseudonymisation shared by the API response layer, the BI executor and the investigator.
Pseudonyms are keyed HMACs (stable, not reversible without the key). Transaction ids are non-identifying event ids and are left as is."""

import re
from typing import Any

from .agents.text_to_sql.executor import pseudonym

# customer / account ids and the device ids that embed a customer number
ID_PATTERN = re.compile(r"\b(?:acc_\d{6}(?:_\d+)?|cus_\d{6}|dev_[md]_\d{6})\b")


def mask_text(s: str, key: str) -> str:
    return ID_PATTERN.sub(lambda m: pseudonym(m.group(0), key) or "", s)


def mask_obj(o: Any, key: str) -> Any:
    if isinstance(o, str):
        return mask_text(o, key)
    if isinstance(o, list):
        return [mask_obj(x, key) for x in o]
    if isinstance(o, dict):
        return {k: mask_obj(v, key) for k, v in o.items()}
    return o


def contains_raw_ids(o: Any) -> bool:
    if isinstance(o, str):
        return bool(ID_PATTERN.search(o))
    if isinstance(o, list):
        return any(contains_raw_ids(x) for x in o)
    if isinstance(o, dict):
        return any(contains_raw_ids(v) for v in o.values())
    return False
