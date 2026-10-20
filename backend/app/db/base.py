"""数据库连接与会话工厂。

engine / sessionmaker 都是懒加载：导入本模块不会连接数据库，
第一次真正用的时候才建连接，避免服务启动即受数据库可用性影响。
"""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.infra.config import get_settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url,
            pool_pre_ping=True,   # 取用前先探活，避免 MySQL 断开的空闲连接报错
            pool_recycle=1800,
        )
    return _engine


def get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(), autoflush=False, expire_on_commit=False
        )
    return _session_factory


@contextmanager
def session_scope():
    """事务上下文：正常提交、异常回滚、始终关闭。供 CLI 与后台线程使用。"""
    db = get_session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db():
    """FastAPI 依赖注入用的生成器。"""
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
