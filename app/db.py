import logging
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import settings

log = logging.getLogger(__name__)

DB_URL = (
    f"postgresql+psycopg://{settings.DB_USER}:{settings.DB_PASSWORD}"
    f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
)

engine = create_engine(DB_URL, pool_size=5, max_overflow=5, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def get_session():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db():
    """FastAPI 依赖"""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def init_db():
    """建扩展 + 全部表 + hypertable,幂等"""
    import models  # noqa: F401  注册所有 ORM 表

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
    models.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "SELECT create_hypertable('prices', 'ts',"
            " if_not_exists => TRUE, migrate_data => TRUE)"
        ))
        conn.execute(text(
            "SELECT create_hypertable('positions_snapshot', 'ts',"
            " if_not_exists => TRUE, migrate_data => TRUE)"
        ))
    log.info("database schema ready")
