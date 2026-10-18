from datetime import timedelta

import numpy as np
import pytest

from finsight.ml.data import Frame, build_table, temporal_split
from finsight.ml.features import FEATURES, FORBIDDEN


def test_feature_set_excludes_labels_and_scenario_metadata():
    assert not FORBIDDEN & set(FEATURES)
    assert not any("fraud" in f or "scenario" in f or "label" in f for f in FEATURES)


def test_flipping_labels_does_not_change_features(mframe, table):
    flipped = Frame(mframe.transactions, {k: not v for k, v in mframe.labels.items()}, mframe.merchants,
                    mframe.devices, mframe.dataset_id)
    other = build_table(flipped)
    assert np.allclose(table[FEATURES].to_numpy(float), other[FEATURES].to_numpy(float))
    assert (table.label != other.label).all()


def test_features_use_no_future_information(mframe, table):
    """Dropping every event after a cut-off must not change features of events before it."""
    cut = sorted(t.event_time for t in mframe.transactions)[len(mframe.transactions) // 2]
    trunc = Frame([t for t in mframe.transactions if t.event_time <= cut], mframe.labels, mframe.merchants,
                  mframe.devices, mframe.dataset_id)
    small = build_table(trunc).set_index("transaction_id")[FEATURES]
    full = table.set_index("transaction_id").loc[small.index, FEATURES]
    assert np.allclose(small.to_numpy(float), full.to_numpy(float))


def test_temporal_split_is_chronological_gapped_and_disjoint(table):
    tr, va, ho = temporal_split(table)
    assert tr.event_time.max() < va.event_time.min() - timedelta(hours=23)
    assert va.event_time.max() < ho.event_time.min() - timedelta(hours=23)
    assert not set(tr.transaction_id) & set(va.transaction_id) and not set(va.transaction_id) & set(ho.transaction_id)
    assert len(tr) > len(va) > 0 and len(ho) > 0
    assert tr.label.sum() > 0 and va.label.sum() > 0 and ho.label.sum() > 0


def test_online_and_offline_features_agree(conn, mds, mframe, table):
    """Stream everything through the real processor, then compare DB-derived contexts with offline ones."""
    from finsight.events import make_event
    from finsight.generator.seed import seed
    from finsight.ml.features import featurize
    from finsight.pipeline.processor import Meta, process_event
    from finsight.scoring.context import fetch_context
    seed(conn, mds, entities_only=True)
    conn.commit()
    for t in mds.transactions:
        process_event(conn, make_event(t).model_dump_json().encode(), Meta("t", 0, 0))
    off = table.set_index("transaction_id")
    rng = np.random.default_rng(0)
    pick = rng.choice(len(mds.transactions), 150, replace=False)
    # include every fraud-scenario event so the unusual code paths are covered
    pick = sorted(set(pick) | {i for i, l in enumerate(mds.labels) if l.is_fraud and i % 3 == 0})
    for i in pick:
        t = mds.transactions[i]
        on = featurize(fetch_context(conn, t))
        for f in FEATURES:
            assert on[f] == pytest.approx(off.loc[t.transaction_id, f], rel=1e-6, abs=1e-6), (t.transaction_id, f)
