from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from shared.config import get_settings


def get_engine():
    return create_engine(get_settings().db.database_url)


def get_session() -> Session:
    Session = sessionmaker(bind=get_engine())
    return Session()
