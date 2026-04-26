from __future__ import annotations

from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine


def make_engine(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args)


def init_db(engine) -> None:
    SQLModel.metadata.create_all(engine)


def session_dependency(engine):
    def _get_session() -> Generator[Session, None, None]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    return _get_session
