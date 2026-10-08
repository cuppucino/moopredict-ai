"""Small additive migrations for existing installations; no historical backfill."""
from sqlalchemy import inspect, text


def ensure_news_publication_column(engine):
    inspector = inspect(engine)
    if not inspector.has_table("news_intel"):
        return
    if "published_at" in {c["name"] for c in inspector.get_columns("news_intel")}:
        return
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("ALTER TABLE news_intel ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ NULL"))
        elif engine.dialect.name == "sqlite":
            connection.execute(text("ALTER TABLE news_intel ADD COLUMN published_at DATETIME NULL"))
        else:
            raise RuntimeError("unsupported_news_schema_migration")
