"""FastAPI 공용 의존성."""
from collections.abc import Generator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from db.orm import User
from shared.db import get_session


def get_db() -> Generator[Session, None, None]:
    session = get_session()
    try:
        yield session
    finally:
        session.close()


class NotAuthenticated(Exception):
    """세션에 로그인 정보가 없을 때 발생 — web/main.py의 핸들러가 /login으로 리다이렉트한다."""


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise NotAuthenticated()

    user = db.query(User).filter_by(id=user_id).first()
    if not user:
        raise NotAuthenticated()
    return user
