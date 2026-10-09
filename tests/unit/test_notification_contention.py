"""仅可恢复的 SQLite 锁错误允许 SSE 重试。"""

import sqlite3
from contextlib import closing

from sqlalchemy.exc import OperationalError

from app.api.notifications import sqlite_contention


def test_real_lock_is_retryable_but_missing_table_is_not(tmp_path):
    path = tmp_path / "contention.db"
    with (
        closing(sqlite3.connect(path, timeout=0)) as holder,
        closing(sqlite3.connect(path, timeout=0)) as reader,
    ):
        holder.execute("BEGIN IMMEDIATE")
        try:
            reader.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            assert sqlite_contention(OperationalError("BEGIN IMMEDIATE", {}, error))
        else:
            raise AssertionError("真实写锁未触发竞争")
        holder.rollback()
        try:
            reader.execute("SELECT * FROM missing_table")
        except sqlite3.OperationalError as error:
            assert not sqlite_contention(OperationalError("SELECT", {}, error))
        else:
            raise AssertionError("缺失表未触发数据库错误")


def test_driver_error_without_sqlite_busy_code_is_not_retryable():
    assert not sqlite_contention(OperationalError("statement", {}, RuntimeError("locked")))
