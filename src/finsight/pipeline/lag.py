"""Real consumer lag from the broker: log-end offset minus the group's committed offset, per partition."""

from confluent_kafka import Consumer, TopicPartition

from ..observability import CONSUMER_LAG


def consumer_lag(bootstrap: str, group: str, topic: str, timeout: float = 5.0) -> dict:
    c = Consumer({"bootstrap.servers": bootstrap, "group.id": group, "enable.auto.commit": False})
    try:
        md = c.list_topics(topic, timeout=timeout)
        if topic not in md.topics or md.topics[topic].error is not None:
            raise RuntimeError(f"topic {topic} not found")
        tps = [TopicPartition(topic, p) for p in md.topics[topic].partitions]
        parts: dict[str, dict] = {}
        for tp in c.committed(tps, timeout=timeout):
            low, high = c.get_watermark_offsets(tp, timeout=timeout)
            committed = (
                tp.offset if tp.offset >= 0 else low
            )  # nothing committed yet: everything from the start is unread
            parts[str(tp.partition)] = {
                "committed": committed,
                "end": high,
                "lag": max(high - committed, 0),
            }
    finally:
        c.close()
    for part, v in parts.items():
        CONSUMER_LAG.labels(part).set(v["lag"])
    return {"total": sum(v["lag"] for v in parts.values()), "partitions": parts}
