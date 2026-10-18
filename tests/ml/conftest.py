import pytest

from finsight.generator import GeneratorConfig, generate
from finsight.ml.data import build_table, frame_from_dataset

FAST_XGB = dict(n_estimators=60, max_depth=3)


@pytest.fixture(scope="session")
def mds():
    return generate(GeneratorConfig(seed=21, n_customers=250, days=30, fraud_prevalence=0.03))


@pytest.fixture(scope="session")
def mframe(mds):
    return frame_from_dataset(mds)


@pytest.fixture(scope="session")
def table(mframe):
    return build_table(mframe)


@pytest.fixture()
def mlflow_uri(tmp_path):
    return f"sqlite:///{tmp_path}/mlflow.db", str(tmp_path / "artifacts")
