"""관리자용 유저 등록 CLI.

Usage:
    python scripts/add_user.py <discord_id> <username> [match_channel_id]

match_channel_id: 이 사용자 전용 매칭 추천 채널 ID(관리자용). 보통은 사용자가 그 채널에서 /매칭채널로 직접 정한다.
없으면 매칭 알림을 보내지 않는다(비서 DM은 discord_id로 감).
이미 있는 사용자에 채널만 넣을 때도 같은 명령을 쓴다.

Example:
    python scripts/add_user.py 123456789012345678 "홍길동" 987654321098765432
"""
import sys

from db.init_db import init
from db.orm import User
from shared.db import get_session


def add_user(discord_id: str, username: str, match_channel_id: str | None = None) -> None:
    init()
    session = get_session()
    try:
        existing = session.query(User).filter_by(discord_id=discord_id).first()
        if existing:
            if match_channel_id:
                existing.match_channel_id = match_channel_id
                session.commit()
                print(f"매칭 채널 설정: {existing.username} (DB id: {existing.id})")
                return
            print(f"이미 존재하는 사용자: {username} (Discord ID: {discord_id}, DB id: {existing.id})")
            return
        user = User(discord_id=discord_id, username=username, match_channel_id=match_channel_id)
        session.add(user)
        session.commit()
        print(f"사용자 추가 완료: {username} (Discord ID: {discord_id}, DB id: {user.id})")
    finally:
        session.close()


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        print("Usage: python scripts/add_user.py <discord_id> <username> [match_channel_id]")
        sys.exit(1)
    add_user(*sys.argv[1:])
