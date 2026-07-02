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

engine = create_engine(DB_URL, pool_size=10, max_overflow=20,
                       pool_pre_ping=True, pool_recycle=1800, pool_timeout=20)
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


# 轻量加列迁移（无 Alembic）：create_all 只建缺失的表，不会给已存在的表加列。
# 新增的可空列在此用 ADD COLUMN IF NOT EXISTS 幂等补齐。
_COLUMN_MIGRATIONS = [
    "ALTER TABLE trade_log ADD COLUMN IF NOT EXISTS currency text",
    # 多账户支持：给已有表补 account_id（幂等，FK 引用 t212_accounts，create_all 已建表）
    "ALTER TABLE t212_custom_watchlist ADD COLUMN IF NOT EXISTS account_id integer REFERENCES t212_accounts(id) ON DELETE SET NULL",
    "ALTER TABLE quant_strategies ADD COLUMN IF NOT EXISTS account_id integer REFERENCES t212_accounts(id) ON DELETE SET NULL",
    "ALTER TABLE quant_trades ADD COLUMN IF NOT EXISTS account_id integer REFERENCES t212_accounts(id) ON DELETE SET NULL",
    # api_secret：Base64 鉴权预留字段
    "ALTER TABLE t212_accounts ADD COLUMN IF NOT EXISTS api_secret text",
    # t212_orders：真实成交字段（嵌套 schema 解析 + 多账户）
    "ALTER TABLE t212_orders ADD COLUMN IF NOT EXISTS side text",
    "ALTER TABLE t212_orders ADD COLUMN IF NOT EXISTS filled_at timestamptz",
    "ALTER TABLE t212_orders ADD COLUMN IF NOT EXISTS account_id integer",
    # news：来源质量分级 + 相关度
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS source_name text",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS source_tier smallint",
    "ALTER TABLE news ADD COLUMN IF NOT EXISTS relevance double precision",
    # job_runs 实时进度
    "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS progress text",
    # investment_analysis 报告期
    "ALTER TABLE investment_analysis ADD COLUMN IF NOT EXISTS report_period text",
]


def init_db():
    """建扩展 + 全部表 + hypertable + 增量列迁移,幂等"""
    import models  # noqa: F401  注册所有 ORM 表

    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
    models.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in _COLUMN_MIGRATIONS:
            try:
                conn.execute(text(stmt))
            except Exception as e:
                log.warning("迁移跳过 (%s): %s", stmt, e)
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
