from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import declarative_base, sessionmaker

from .settings import get_settings

settings = get_settings()
is_sqlite = settings.database_url.startswith("sqlite")
engine_kwargs = {"pool_pre_ping": True}
if is_sqlite:
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.database_url, **engine_kwargs)

if is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
