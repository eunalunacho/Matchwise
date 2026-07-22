"""FastAPI web 앱 유닛 테스트 — SQLite 인메모리 DB로 대체."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.orm import ApplicationQuestion, Base, Job, JobScore, ResumeDraft, User
from web.deps import get_db
from web.main import app

# StaticPool: TestClient가 요청마다 새 커넥션을 열어도 같은 인메모리 DB를 공유하게 한다.
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine)


def override_get_db():
    s = TestSession()
    try:
        yield s
    finally:
        s.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_db():
    """각 테스트 전 테이블을 비운다."""
    yield
    s = TestSession()
    try:
        for model in (ResumeDraft, ApplicationQuestion, JobScore, Job, User):
            s.query(model).delete()
        s.commit()
    finally:
        s.close()


# ── health / index ───────────────────────────────────────────────────────

def test_health_check():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_index_redirects_to_dashboard():
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/dashboard"


# ── dashboard ────────────────────────────────────────────────────────────

def test_dashboard_returns_200():
    assert client.get("/dashboard").status_code == 200


def test_dashboard_shows_job_title():
    s = TestSession()
    s.add(Job(source="inthiswork", url="https://ex.com/1", title="Python Dev", company="Co"))
    s.commit()
    s.close()
    assert "Python Dev" in client.get("/dashboard").text


def test_dashboard_source_filter():
    s = TestSession()
    s.add(Job(source="inthiswork", url="https://ex.com/a", title="Job A", company="A"))
    s.add(Job(source="superookie", url="https://ex.com/b", title="Job B", company="B"))
    s.commit()
    s.close()

    resp = client.get("/dashboard?source=inthiswork")
    assert "Job A" in resp.text
    assert "Job B" not in resp.text


# ── job detail ───────────────────────────────────────────────────────────

def test_job_detail_returns_200_and_shows_jd_summary():
    s = TestSession()
    job = Job(
        source="inthiswork",
        url="https://ex.com/1",
        title="AI Engineer",
        company="Acme",
        jd_text="원문 텍스트",
        jd_summary={
            "tasks": ["RAG 구축"],
            "stack": ["Python"],
            "employment_type": "정규직",
            "qualifications": ["경력 0~3년"],
            "preferred": ["LangGraph 경험"],
        },
    )
    s.add(job)
    s.commit()
    job_id = job.id
    s.close()

    resp = client.get(f"/jobs/{job_id}")
    assert resp.status_code == 200
    assert "RAG 구축" in resp.text
    assert "경력 0~3년" in resp.text
    assert "LangGraph 경험" in resp.text
    assert "정규직" in resp.text


def test_job_detail_questions_saved_overwrites_existing():
    s = TestSession()
    job = Job(source="inthiswork", url="https://ex.com/1", title="T", company="C")
    s.add(job)
    s.commit()
    job_id = job.id
    s.add(ApplicationQuestion(job_id=job_id, question="옛 질문", order_num=0))
    s.commit()
    s.close()

    resp = client.post(
        f"/jobs/{job_id}/questions",
        data={"questions": "새 질문 1\n새 질문 2\n"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    s = TestSession()
    qs = (
        s.query(ApplicationQuestion)
        .filter_by(job_id=job_id)
        .order_by(ApplicationQuestion.order_num)
        .all()
    )
    assert [q.question for q in qs] == ["새 질문 1", "새 질문 2"]
    s.close()


# ── profile ──────────────────────────────────────────────────────────────

def test_profile_page_returns_200():
    assert client.get("/profile").status_code == 200


def test_profile_resume_update():
    s = TestSession()
    s.add(User(discord_id="777", username="profileuser"))
    s.commit()
    s.close()

    resp = client.post(
        "/profile/777/resume",
        data={"resume_text": "Python 3년 경력"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    s = TestSession()
    u = s.query(User).filter_by(discord_id="777").first()
    assert u.resume_text == "Python 3년 경력"
    s.close()


def test_profile_experience_update():
    s = TestSession()
    s.add(User(discord_id="778", username="expuser"))
    s.commit()
    s.close()

    resp = client.post(
        "/profile/778/experience",
        data={"experience_text": "팀 프로젝트 갈등 해결 경험"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    s = TestSession()
    u = s.query(User).filter_by(discord_id="778").first()
    assert u.experience_text == "팀 프로젝트 갈등 해결 경험"
    s.close()


def test_profile_update_unknown_discord_id_noop():
    """등록되지 않은 discord_id로 요청해도 에러 없이 리다이렉트한다."""
    resp = client.post(
        "/profile/999999/resume",
        data={"resume_text": "무시됨"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


# ── draft ────────────────────────────────────────────────────────────────

def test_draft_page_no_drafts_shows_placeholder():
    s = TestSession()
    job = Job(source="inthiswork", url="https://ex.com/1", title="T", company="C")
    s.add(job)
    s.commit()
    job_id = job.id
    s.close()

    resp = client.get(f"/draft/{job_id}")
    assert resp.status_code == 200
    assert "아직 초안이 없습니다" in resp.text


def test_draft_page_shows_answer_grouped_by_question():
    s = TestSession()
    job = Job(source="inthiswork", url="https://ex.com/1", title="T", company="C")
    user = User(discord_id="1", username="euna")
    s.add_all([job, user])
    s.commit()

    q = ApplicationQuestion(job_id=job.id, question="지원 동기는?", order_num=0)
    s.add(q)
    s.commit()

    s.add(ResumeDraft(
        job_id=job.id, user_id=user.id, question_id=q.id,
        answer="이 회사의 RAG 파이프라인 경험이 제 이력과 맞아서 지원했습니다.",
    ))
    s.commit()
    job_id = job.id
    s.close()

    resp = client.get(f"/draft/{job_id}")
    assert resp.status_code == 200
    assert "지원 동기는?" in resp.text
    assert "euna" in resp.text
    assert "RAG 파이프라인 경험" in resp.text
