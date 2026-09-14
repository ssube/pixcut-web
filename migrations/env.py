from alembic import context
from pixcut.db import data_dir, engine_for, metadata

engine = context.config.attributes.get("engine") or engine_for(data_dir())
with engine.connect() as connection:
    connection.exec_driver_sql("BEGIN IMMEDIATE")
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()
    connection.commit()
