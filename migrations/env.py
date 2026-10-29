import os
from alembic import context
from sqlalchemy import create_engine
from observatory.db import Base
from observatory import mandate  # noqa: F401  register the mandate model for migration metadata

url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, hide_parameters=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()

