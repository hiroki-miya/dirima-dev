import os
from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.orm import Session as OrmSession


class Base(DeclarativeBase):
    pass


def engine() -> Engine:
    return _engine_for_url(database_url())


def database_url() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    return URL.create(
        "postgresql+psycopg",
        username=os.environ.get("DIRIMA_DB_USER", "dirima"),
        password=os.environ.get("DIRIMA_DB_PASSWORD", "dirima"),
        host=os.environ.get("DIRIMA_DB_HOST", "localhost"),
        port=int(os.environ.get("DIRIMA_DB_PORT", "5432")),
        database=os.environ.get("DIRIMA_DB_NAME", "dirima"),
    ).render_as_string(hide_password=False)


@lru_cache(maxsize=4)
def _engine_for_url(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, connect_args={"options": "-c timezone=UTC"})


Session = sessionmaker(autoflush=False, expire_on_commit=False)


def session_scope() -> Generator[OrmSession]:
    db = Session(bind=engine())
    try:
        yield db
    finally:
        db.close()
