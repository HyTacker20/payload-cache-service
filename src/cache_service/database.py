import sqlite3
from pathlib import Path

from sqlalchemy import URL, Engine, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Transformation(Base):
    __tablename__ = "transformations"

    version: Mapped[str] = mapped_column(String, primary_key=True)
    original: Mapped[str] = mapped_column(Text, primary_key=True)
    transformed: Mapped[str] = mapped_column(Text)


class Payload(Base):
    __tablename__ = "payloads"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    output: Mapped[str] = mapped_column(Text)


def create_database(directory: Path) -> Engine:
    directory.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(directory / "cache.sqlite3")),
        hide_parameters=True,
        connect_args={
            "check_same_thread": False,
            "timeout": 30,
            "autocommit": sqlite3.LEGACY_TRANSACTION_CONTROL,
        },
    )
    Base.metadata.create_all(engine)
    return engine
