from pathlib import Path

from pydantic_settings import BaseSettings

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "packages" / "database" / "migrations"


class Settings(BaseSettings):
    database_url: str = "postgresql://finsight:finsight_dev_password@localhost:5433/finsight"
    kafka_bootstrap: str = "localhost:19092"
    kafka_topic: str = "finsight.transactions.v1"
    consumer_group: str = "finsight-fraud-consumer"
    alert_threshold: float = 0.5
    model_version: str = "rules-v1"
    anthropic_api_key: str | None = None
    bi_model: str = "claude-sonnet-5-5"
    bi_database_url: str = "postgresql://finsight_bi:finsight_bi_dev_password@localhost:5433/finsight"
    investigator_database_url: str = "postgresql://finsight_investigator:finsight_inv_dev_password@localhost:5433/finsight"
    investigator_model: str = "claude-sonnet-5-5"
    bi_row_limit: int = 200
    pii_mask_key: str = "dev-only-mask-key-change-me"
    auth_keys: str | None = None      # "key:role:identity,..." (roles: viewer, analyst)
    auth_disabled: bool = False       # explicit local-demo switch: every caller acts as 'local-demo' analyst
    consumer_metrics_port: int = 0  # 0 = no metrics endpoint in the consumer process
    scoring_mode: str = "rules"  # "rules" | "ml" (ml uses the approved registry model, else rules-only)
    mlflow_tracking_uri: str | None = None
    ml_threshold: float | None = None


def get_settings() -> Settings:
    return Settings()
