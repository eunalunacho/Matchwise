"""users 테이블 컬럼 보강 마이그레이션(ALTER TABLE) 테스트.

Base.metadata.create_all()은 이미 존재하는 테이블에 새 컬럼을 추가하지 못하므로(새 테이블만
생성), 오래된 스키마로 만들어진 DB 파일(google_sub/basic_info 없음)을 흉내낸 뒤 이 스크립트가
실제로 컬럼을 보강하는지 검증한다.
"""
from sqlalchemy import create_engine, inspect, text

from db.orm import Base
from scripts.migrate_users_columns import add_missing_user_columns


def _make_legacy_engine():
    """google_sub/basic_info가 없는 옛 users 테이블만 가진 엔진을 만든다."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                discord_id VARCHAR(64) UNIQUE,
                username VARCHAR(128) NOT NULL,
                resume_text TEXT,
                experience_text TEXT,
                is_active BOOLEAN,
                created_at DATETIME
            )
        """))
    return engine


def test_adds_missing_columns_to_legacy_users_table():
    engine = _make_legacy_engine()

    added = add_missing_user_columns(engine)

    assert added == ["google_sub", "basic_info"]
    columns = {col["name"] for col in inspect(engine).get_columns("users")}
    assert {"google_sub", "basic_info"}.issubset(columns)


def test_rerun_is_idempotent():
    engine = _make_legacy_engine()
    add_missing_user_columns(engine)

    added_second_time = add_missing_user_columns(engine)

    assert added_second_time == []


def test_noop_when_schema_already_current():
    """최신 스키마(create_all로 만든)엔 손댈 컬럼이 없다."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    added = add_missing_user_columns(engine)

    assert added == []
