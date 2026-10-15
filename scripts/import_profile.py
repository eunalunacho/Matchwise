"""profile.json(개인 이력) → users.basic_info + experiences 적재 CLI.

profile.json은 개인 정보라 .gitignore 대상이다. 형식:
    {
      "basic_info": {"이름": ..., "병역": ..., "대학교": [...], "자격증": [...], ...},
      "experiences": [{"kind": "career", "title": ..., "period": ..., "content": ..., "tags": [...]}, ...]
    }
kind 목록은 analysis/profile.py::KIND_LABELS.

다시 실행하면 그 유저의 basic_info와 experiences를 파일 내용으로 통째로 바꾼다(중복으로 쌓이지 않음).
이미 매긴 job_scores는 그대로라, 프로필을 바꾼 뒤 다시 채점하려면 그 점수를 따로 지워야 한다.

Usage:
    python -m scripts.import_profile [profile.json 경로] [--user-id N]   (기본: ./profile.json, 1)
"""
import argparse
import json
from pathlib import Path

from db.init_db import init
from db.orm import Experience, User
from shared.db import get_session


def import_profile(session, user_id: int, data: dict) -> int:
    """유저의 basic_info를 덮어쓰고 experiences를 data 내용으로 교체한다. 커밋까지 한다.

    Returns:
        새로 넣은 experiences 수.

    Raises:
        ValueError: user_id에 해당하는 유저가 없을 때.
    """
    user = session.get(User, user_id)
    if user is None:
        raise ValueError(f"유저 id={user_id} 없음")

    user.basic_info = data.get("basic_info") or {}
    session.query(Experience).filter_by(user_id=user_id).delete()
    items = data.get("experiences") or []
    for item in items:
        session.add(Experience(
            user_id=user_id,
            kind=item["kind"],
            title=item.get("title"),
            period=item.get("period"),
            content=item["content"],
            tags=item.get("tags"),
        ))
    session.commit()
    return len(items)


def main() -> None:
    parser = argparse.ArgumentParser(description="profile.json을 DB에 적재")
    parser.add_argument("path", nargs="?", default="profile.json")
    parser.add_argument("--user-id", type=int, default=1)
    args = parser.parse_args()

    data = json.loads(Path(args.path).read_text(encoding="utf-8"))
    init()
    session = get_session()
    try:
        count = import_profile(session, args.user_id, data)
        print(f"유저 {args.user_id}: basic_info {len(data.get('basic_info') or {})}개 항목, 경험 {count}건 적재")
    finally:
        session.close()


if __name__ == "__main__":
    main()
