import pytest
from sqlalchemy.exc import IntegrityError
from db.orm import Application, Experience, RawPosting, User, Job, JobScore


def test_create_user(session):
    user = User(discord_id="123456789", username="tester")
    session.add(user)
    session.commit()
    found = session.query(User).filter_by(discord_id="123456789").first()
    assert found is not None
    assert found.username == "tester"


def test_create_job(session):
    job = Job(source="inthiswork", url="https://example.com/job/1", title="Backend Dev", company="Acme")
    session.add(job)
    session.commit()
    found = session.query(Job).filter_by(url="https://example.com/job/1").first()
    assert found is not None


def test_job_score_unique_constraint(session):
    user = User(discord_id="111", username="u1")
    job = Job(source="inthiswork", url="https://example.com/job/2", title="Dev", company="Co")
    session.add_all([user, job])
    session.flush()
    session.add(JobScore(job_id=job.id, user_id=user.id, fit_score=80))
    session.flush()
    session.add(JobScore(job_id=job.id, user_id=user.id, fit_score=90))
    with pytest.raises(IntegrityError):
        session.flush()


def test_application_defaults_to_interested_status(session):
    user = User(discord_id="222", username="u2")
    job = Job(source="inthiswork", url="https://example.com/job/3", title="Dev", company="Co")
    session.add_all([user, job])
    session.flush()

    app = Application(job_id=job.id, user_id=user.id)
    session.add(app)
    session.commit()

    found = session.query(Application).filter_by(job_id=job.id, user_id=user.id).first()
    assert found.status == "interested"
    assert found.will_apply_at is None
    assert found.submitted_at is None


def test_application_unique_constraint(session):
    user = User(discord_id="333", username="u3")
    job = Job(source="inthiswork", url="https://example.com/job/4", title="Dev", company="Co")
    session.add_all([user, job])
    session.flush()
    session.add(Application(job_id=job.id, user_id=user.id))
    session.flush()
    session.add(Application(job_id=job.id, user_id=user.id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_create_experience_with_tags(session):
    user = User(discord_id="444", username="u4")
    session.add(user)
    session.flush()

    exp = Experience(
        user_id=user.id, kind="project", title="추천 시스템",
        period="2025.01~2025.06", content="RAG 파이프라인 구현",
        tags=["Python", "LangGraph"],
    )
    session.add(exp)
    session.commit()

    found = session.query(Experience).filter_by(user_id=user.id).first()
    assert found.kind == "project"
    assert found.tags == ["Python", "LangGraph"]


def test_user_basic_info_json(session):
    user = User(
        discord_id="555", username="u5",
        basic_info={"이름": "홍길동", "학력": "OO대학교"},
    )
    session.add(user)
    session.commit()

    found = session.query(User).filter_by(discord_id="555").first()
    assert found.basic_info == {"이름": "홍길동", "학력": "OO대학교"}


def test_job_extract_status_defaults_to_pending(session):
    job = Job(source="inthiswork", url="https://example.com/job/6", title="Dev", company="Co")
    session.add(job)
    session.commit()

    found = session.query(Job).filter_by(url="https://example.com/job/6").first()
    assert found.extract_status == "pending"


def test_job_eligibility_json(session):
    job = Job(
        source="inthiswork", url="https://example.com/job/7", title="Dev", company="Co",
        eligibility={"new_grad_ok": True, "min_years": None, "evidence": "신입 지원 가능"},
    )
    session.add(job)
    session.commit()

    found = session.query(Job).filter_by(url="https://example.com/job/7").first()
    assert found.eligibility == {"new_grad_ok": True, "min_years": None, "evidence": "신입 지원 가능"}


def test_job_eligibility_defaults_to_none(session):
    job = Job(source="inthiswork", url="https://example.com/job/8", title="Dev", company="Co")
    session.add(job)
    session.commit()

    found = session.query(Job).filter_by(url="https://example.com/job/8").first()
    assert found.eligibility is None


def test_create_raw_posting_with_json_fields(session):
    posting = RawPosting(
        source="jasoseol",
        url="https://jasoseol.com/recruit?ec=1",
        apply_url="https://company.com/apply",
        title="백엔드 개발자",
        company="회사",
        jd_text="",
        payload={"calendar": {"id": 1}},
        hints={"positions": [{"career_level": "신입"}]},
        image_urls=["https://cdn.example.com/jd.png"],
    )
    session.add(posting)
    session.commit()

    found = session.query(RawPosting).filter_by(url="https://jasoseol.com/recruit?ec=1").first()
    assert found is not None
    assert found.payload == {"calendar": {"id": 1}}
    assert found.hints == {"positions": [{"career_level": "신입"}]}
    assert found.image_urls == ["https://cdn.example.com/jd.png"]
    assert found.fetched_at is not None  # 기본값이 자동으로 채워짐


def test_raw_posting_unique_constraint_on_source_and_url(session):
    session.add(RawPosting(source="inthiswork", url="https://example.com/dup", jd_text=""))
    session.flush()
    session.add(RawPosting(source="inthiswork", url="https://example.com/dup", jd_text=""))
    with pytest.raises(IntegrityError):
        session.flush()


def test_raw_posting_allows_same_url_from_different_sources(session):
    """같은 URL 문자열이라도 소스가 다르면 별개 행으로 허용한다(사실상 안 겹치지만 제약상 확인)."""
    session.add(RawPosting(source="inthiswork", url="https://example.com/same", jd_text=""))
    session.add(RawPosting(source="jasoseol", url="https://example.com/same", jd_text=""))
    session.commit()

    assert session.query(RawPosting).filter_by(url="https://example.com/same").count() == 2
