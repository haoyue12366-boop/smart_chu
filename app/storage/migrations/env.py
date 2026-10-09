"""由应用提供连接的版本化运行库迁移。"""

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.storage.models import metadata

config = context.config
connection = config.attributes.get("connection")
if connection is not None:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
