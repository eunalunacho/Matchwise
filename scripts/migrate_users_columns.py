"""users 테이블에 누락된 컬럼(google_sub, basic_info)을 추가하는 1회성 마이그레이션.

Base.metadata.create_all()(db/init_db.py)은 DB 파일에 없는 테이블만 새로 만들고, 이미 존재하는
테이블에는 컬럼을 추가하지 못한다. users 테이블은 google_sub/basic_info가 ORM(db/orm.py)에
추가되기 전에 이미 만들어진 DB 파일에는 이 두 컬럼이 없을 수 있어, 여기서 ALTER TABLE로
보강한다. SQLite/Postgres 공용. 이미 있는 컬럼은 건드리지 않아 몇 번을 다시 실행해도 안전하다
(idempotent).

Usage:
    python scripts/migrate_users_columns.py
"""
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

# 컬럼명 → ALTER TABLE에 쓸 타입. basic_info는 자유 key-value(JSON)라 두 DB 모두 JSON으로 선언.
_NEW_COLUMNS = {
    "google_sub": "VARCHAR(255)",
    "basic_info": "JSON",
}


def add_missing_user_columns(engine: Engine) -> list[str]:
    """users 테이블에 없는 컬럼을 ALTER TABLE로 추가하고, 실제로 추가한 컬럼명을 반환한다."""
    existing = {col["name"] for col in inspect(engine).get_columns("users")}
    added = []
    with engine.begin() as conn:
        for name, col_type in _NEW_COLUMNS.items():
            if name in existing:
                continue
            conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {col_type}"))
            added.append(name)
    return added


if __name__ == "__main__":
    from shared.db import get_engine

    added = add_missing_user_columns(get_engine())
    print(f"추가된 컬럼: {added}" if added else "추가할 컬럼 없음 (이미 최신 스키마)")
