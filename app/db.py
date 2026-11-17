"""数据库连接与会话。

单用户本地应用，SQLite 足够。check_same_thread=False 是因为
FastAPI 的同步路由跑在线程池里，连接会被跨线程使用。
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import DB_URL


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


engine = create_engine(
    DB_URL,
    echo=False,
    connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """建表。开发期直接 create_all，不引 Alembic。"""
    from . import models  # noqa: F401  仅为注册元数据

    Base.metadata.create_all(engine)
    _ensure_columns()


def _ensure_columns() -> None:
    """给已存在的表补上后加的列。

    `create_all` 只建缺失的**表**，对已存在的表一个字段都不动——所以给模型加列之后，
    老库不会报错，只会在第一次查询时抛 "no such column: jobs.hr_facts_json"，
    而那句话不会告诉你少了哪次改动。这里主动对齐，省掉手工 ALTER。

    只做加法：加列、不改类型、不删列。改类型和删列必须人工判断，
    自动猜会悄无声息地丢数据。默认值只支持简单字面量，其余一律留 NULL。
    """
    insp = inspect(engine)
    existing = {t for t in insp.get_table_names()}
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}"
                default = getattr(col, "default", None)
                value = getattr(default, "arg", None)
                if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                    ddl += f" DEFAULT '{value}'" if isinstance(value, str) else f" DEFAULT {value}"
                conn.execute(text(ddl))


def get_db() -> Iterator[Session]:
    """FastAPI 依赖注入用。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
