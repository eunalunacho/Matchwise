"""관리자용 유저 등록 CLI.

Usage:
    python scripts/add_user.py <discord_id> <username>

Example:
    python scripts/add_user.py 123456789012345678 "홍길동"
"""
import sys

from db.init_db import init
from db.orm import User
from shared.db import get_session


def add_user(discord_id: str, username: str) -> None:
    init()
    session = get_session()
    try:
        existing = session.query(User).filter_by(discord_id=discord_id).first()
        if existing:
            print(f"이미 존재하는 사용자: {username} (Discord ID: {discord_id}, DB id: {existing.id})")
            return
        user = User(discord_id=discord_id, username=username)
        session.add(user)
        session.commit()
        print(f"사용자 추가 완료: {username} (Discord ID: {discord_id}, DB id: {user.id})")
    finally:
        session.close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python scripts/add_user.py <discord_id> <username>")
        sys.exit(1)
    add_user(sys.argv[1], sys.argv[2])
