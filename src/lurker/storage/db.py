from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lurker.storage.models import Base


def init_db(path: str | Path):
    engine = create_engine(f"sqlite:///{Path(path)}", future=True)
    Base.metadata.create_all(engine)
    return engine


def create_session(engine) -> Session:
    return Session(engine)
