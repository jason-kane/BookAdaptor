"""Alembic environment using the application's connection configuration."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from artifact_editor.db import Base, database_url
from artifact_editor.db import models  # Register every mapped table.

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)


def include_object(obj, name, type_, reflected, compare_to):
    # This database can contain user-managed tables created through pgAdmin.
    # Schema generation must never propose dropping tables the ORM doesn't own.
    return not (type_ == "table" and reflected and compare_to is None)


def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata,
                      compare_type=True, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=database_url(), target_metadata=Base.metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    run(config.attributes["connection"])
else:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        run(connection)
    engine.dispose()
