"""短事务；锁等待有界，求解器从不进入此模块。"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, event

from app.storage.decoded_models import release_decoded_models


class UnitOfWork:
    def __init__(self, path: Path, *, busy_timeout_ms: int = 100) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            "sqlite:///" + self.path.as_posix(),
            connect_args={"timeout": busy_timeout_ms / 1000, "check_same_thread": False},
        )

        @event.listens_for(self.engine, "connect")
        def setup(connection: Any, record: Any) -> None:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=" + str(busy_timeout_ms))

    def migrate(self, revision: str = "head") -> None:
        with self.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            connection.commit()
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
            config.attributes["connection"] = connection
            command.upgrade(config, revision)

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def close(self) -> None:
        release_decoded_models(self.engine)
        self.engine.dispose()
