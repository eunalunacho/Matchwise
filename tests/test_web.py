"""FastAPI web 앱 유닛 테스트 — SQLite 인메모리 DB로 대체.

로그인은 get_current_user를 오버라이드해서 흉내낸다 — _login_as(user_id)를
호출한 뒤 요청하면 그 유저로 로그인한 것처럼 처리되고, _logout()하면 풀린다.
"""
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from db.orm import (
    Application, ApplicationEvent, ApplicationQuestion, Base, Experience, Job, JobScore, ResumeDraft, User,
)
from web.deps import NotAuthenticated, get_current_user, get_db
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


_logged_in_user_id = {"value": None}


def override_get_current_user(db: Session = Depends(get_db)) -> User:
    if _logged_in_user_id["value"] is None:
        raise NotAuthenticated()
    user = db.query(User).filter_by(id=_logged_in_user_id["value"]).first()
    if not user:
        raise NotAuthenticated()
    return user


def _login_as(user_id: int) -> None:
    _logged_in_user_id["value"] = user_id


def _logout() -> None:
    _logged_in_user_id["value"] = None


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_current_user] = override_get_current_user
client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_db():
    """각 테스트 전 테이블을 비우고 로그인 상태도 초기화한다."""
    yield
    _logout()
    s = TestSession()
    try:
        for model in (ApplicationEvent, Application, ResumeDraft, ApplicationQuestion, JobScore, Experience, Job, User):
            s.query(model).delete()
        s.commit()
    finally:
        s.close()


def _make_user(discord_id: str = "1", username: str = "tester") -> int:
    s = TestSession()
    user = User(discord_id=discord_id, username=username)
    s.add(user)
    s.commit()
    user_id = user.id
    s.close()
    return user_id


# ── health / index ───────────────────────────────────────────────────────

def test_health_check():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_index_redirects_to_dashboard():
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/dashboard"


# ── 인증 ─────────────────────────────────────────────────────────────────

def test_login_page_returns_200_when_not_logged_in():
    assert client.get("/login").status_code == 200


def test_gated_pages_redirect_to_login_when_not_logged_in():
    for path in ("/dashboard", "/profile", "/experiences"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/login"


def test_logout_clears_session_state():
    _login_as(_make_user())
    assert client.get("/dashboard").status_code == 200

    resp = client.get("/logout", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/login"
    # 실제 브라우저 세션 쿠키는 여기서 안 지워지지만(오버라이드라 무관),
    # 로그아웃 라우트 자체가 정상 동작하는지만 확인한다.


# ── dashboard ────────────────────────────────────────────────────────────

def test_dashboard_returns_200():
    _login_as(_make_user())
    assert client.get("/dashboard").status_code == 200


def test_dashboard_shows_job_title():
    _login_as(_make_user())
    s = TestSession()
    s.add(Job(source="inthiswork", url="https://ex.com/1", title="Python Dev", company="Co"))
    s.commit()
    s.close()
    assert "Python Dev" in client.get("/dashboard").text


def test_dashboard_source_filter():
    _login_as(_make_user())
    s = TestSession()
    s.add(Job(source="inthiswork", url="https://ex.com/a", title="Job A", company="A"))
    s.add(Job(source="superookie", url="https://ex.com/b", title="Job B", company="B"))
    s.commit()
    s.close()

    resp = client.get("/dashboard?source=inthiswork")
    assert "Job A" in resp.text
    assert "Job B" not in resp.text


def test_dashboard_only_shows_my_fit_score():
    """같은 공고라도 다른 유저의 fit_score는 안 섞인다."""
    me = _make_user(discord_id="1", username="me")
    friend = _make_user(discord_id="2", username="friend")

    s = TestSession()
    job = Job(source="inthiswork", url="https://ex.com/shared", title="Shared Job", company="Co")
    s.add(job)
    s.commit()
    s.add(JobScore(job_id=job.id, user_id=me, fit_score=90))
    s.add(JobScore(job_id=job.id, user_id=friend, fit_score=40))
    s.commit()
    s.close()

    _login_as(me)
    resp = client.get("/dashboard")
    assert "90" in resp.text
    assert "40" not in resp.text


def test_dashboard_shows_grade_label():
    me = _make_user()
    _login_as(me)
    _job_with_score(me, fit_score=85)
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "85" in resp.text and "추천" in resp.text


def test_dashboard_renders_none_score_row_with_grade_label():
    """지원 불가/판단 불가 행(fit_score None)도 대시보드·상세가 깨지지 않고 라벨을 보여준다."""
    me = _make_user()
    _login_as(me)
    job_id = _job_with_score(me, fit_score=None, fit_detail={"status": "ineligible"})
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "지원 불가" in resp.text
    assert "None" not in resp.text
    detail = client.get(f"/jobs/{job_id}")
    assert detail.status_code == 200
    assert "지원 불가" in detail.text


# ── job detail ───────────────────────────────────────────────────────────

def test_job_detail_returns_200_and_shows_jd_summary():
    _login_as(_make_user())
    s = TestSession()
    job = Job(
        source="inthiswork",
        url="https://ex.com/1",
        title="AI Engineer",
        company="Acme",
        jd_text="원문 텍스트",
        jd_tasks=["RAG 구축"],
        jd_stack=["Python"],
        jd_employment_type="정규직",
        jd_qualifications=["경력 0~3년"],
        jd_preferred=["LangGraph 경험"],
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
    _login_as(_make_user())
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
    _login_as(_make_user())
    assert client.get("/profile").status_code == 200


def test_profile_resume_update():
    _login_as(_make_user(discord_id="777", username="profileuser"))

    resp = client.post(
        "/profile/resume",
        data={"resume_text": "Python 3년 경력"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    s = TestSession()
    u = s.query(User).filter_by(discord_id="777").first()
    assert u.resume_text == "Python 3년 경력"
    s.close()


def test_profile_experience_update():
    _login_as(_make_user(discord_id="778", username="expuser"))

    resp = client.post(
        "/profile/experience",
        data={"experience_text": "팀 프로젝트 갈등 해결 경험"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    s = TestSession()
    u = s.query(User).filter_by(discord_id="778").first()
    assert u.experience_text == "팀 프로젝트 갈등 해결 경험"
    s.close()


def test_profile_update_requires_login():
    resp = client.post(
        "/profile/resume",
        data={"resume_text": "무시됨"},
        follow_redirects=False,
    )
    assert resp.status_code == 307
    assert resp.headers["location"] == "/login"


# ── experiences ──────────────────────────────────────────────────────────

def test_experiences_page_returns_200():
    _login_as(_make_user())
    assert client.get("/experiences").status_code == 200


def test_basic_info_update():
    _login_as(_make_user(discord_id="881", username="expuser"))

    resp = client.post(
        "/experiences/basic-info",
        data={"name": "홍길동", "education": "OO대학교"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    s = TestSession()
    u = s.query(User).filter_by(discord_id="881").first()
    assert u.basic_info == {"이름": "홍길동", "학력": "OO대학교"}
    s.close()


def test_basic_info_update_keeps_other_imported_fields():
    """profile.json으로 넣은 병역·자격증 등은 웹 폼(이름·학력만)에서 저장해도 지워지지 않는다."""
    user_id = _make_user(discord_id="882", username="expuser2")
    s = TestSession()
    s.get(User, user_id).basic_info = {"이름": "옛 이름", "병역": "비대상", "자격증": [{"이름": "SQLD"}]}
    s.commit()
    s.close()
    _login_as(user_id)

    client.post("/experiences/basic-info", data={"name": "조은아", "education": "건국대"}, follow_redirects=False)

    s = TestSession()
    assert s.get(User, user_id).basic_info == {
        "이름": "조은아", "병역": "비대상", "자격증": [{"이름": "SQLD"}], "학력": "건국대",
    }
    s.close()


def test_add_experience_and_shows_on_page():
    _login_as(_make_user(discord_id="882", username="expuser2"))

    resp = client.post(
        "/experiences/add",
        data={
            "kind": "project",
            "title": "추천 시스템 개발",
            "period": "2025.01~2025.06",
            "tags": "Python, LangGraph",
            "content": "RAG 기반 추천 파이프라인을 설계·구현했다.",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    s = TestSession()
    exp = s.query(Experience).filter_by(title="추천 시스템 개발").first()
    assert exp is not None
    assert exp.kind == "project"
    assert exp.tags == ["Python", "LangGraph"]
    s.close()

    page = client.get("/experiences").text
    assert "추천 시스템 개발" in page
    assert "RAG 기반" in page or "프로젝트" in page  # 목록에 반영됨


def test_add_experience_requires_login():
    resp = client.post(
        "/experiences/add",
        data={"kind": "career", "content": "무시됨"},
        follow_redirects=False,
    )
    assert resp.status_code == 307
    assert resp.headers["location"] == "/login"


def test_delete_experience():
    user_id = _make_user(discord_id="883", username="expuser3")
    s = TestSession()
    exp = Experience(user_id=user_id, kind="story", content="에피소드 내용")
    s.add(exp)
    s.commit()
    exp_id = exp.id
    s.close()

    _login_as(user_id)
    resp = client.post(f"/experiences/{exp_id}/delete", follow_redirects=False)
    assert resp.status_code == 303

    s = TestSession()
    assert s.query(Experience).filter_by(id=exp_id).first() is None
    s.close()


def test_delete_experience_cannot_remove_another_users():
    """다른 사람 소유의 경험 조각은 id를 알아도 못 지운다."""
    owner_id = _make_user(discord_id="884", username="owner")
    other_id = _make_user(discord_id="885", username="other")

    s = TestSession()
    exp = Experience(user_id=owner_id, kind="story", content="주인 있는 경험")
    s.add(exp)
    s.commit()
    exp_id = exp.id
    s.close()

    _login_as(other_id)
    resp = client.post(f"/experiences/{exp_id}/delete", follow_redirects=False)
    assert resp.status_code == 303  # 조용히 무시 — 에러는 안 남

    s = TestSession()
    assert s.query(Experience).filter_by(id=exp_id).first() is not None  # 안 지워짐
    s.close()


# ── draft ────────────────────────────────────────────────────────────────

def test_draft_page_no_drafts_shows_placeholder():
    _login_as(_make_user())
    s = TestSession()
    job = Job(source="inthiswork", url="https://ex.com/1", title="T", company="C")
    s.add(job)
    s.commit()
    job_id = job.id
    s.close()

    resp = client.get(f"/draft/{job_id}")
    assert resp.status_code == 200
    assert "아직 초안이 없습니다" in resp.text


def test_draft_page_shows_my_answer_grouped_by_question():
    user_id = _make_user(discord_id="1", username="euna")
    s = TestSession()
    job = Job(source="inthiswork", url="https://ex.com/1", title="T", company="C")
    s.add(job)
    s.commit()

    q = ApplicationQuestion(job_id=job.id, question="지원 동기는?", order_num=0)
    s.add(q)
    s.commit()

    s.add(ResumeDraft(
        job_id=job.id, user_id=user_id, question_id=q.id,
        answer="이 회사의 RAG 파이프라인 경험이 제 이력과 맞아서 지원했습니다.",
    ))
    s.commit()
    job_id = job.id
    s.close()

    _login_as(user_id)
    resp = client.get(f"/draft/{job_id}")
    assert resp.status_code == 200
    assert "지원 동기는?" in resp.text
    assert "RAG 파이프라인 경험" in resp.text


def test_draft_page_does_not_show_other_users_answer():
    owner_id = _make_user(discord_id="1", username="owner")
    other_id = _make_user(discord_id="2", username="other")

    s = TestSession()
    job = Job(source="inthiswork", url="https://ex.com/1", title="T", company="C")
    s.add(job)
    s.commit()
    q = ApplicationQuestion(job_id=job.id, question="지원 동기는?", order_num=0)
    s.add(q)
    s.commit()
    s.add(ResumeDraft(
        job_id=job.id, user_id=owner_id, question_id=q.id, answer="owner만의 답변",
    ))
    s.commit()
    job_id = job.id
    s.close()

    _login_as(other_id)
    resp = client.get(f"/draft/{job_id}")
    assert resp.status_code == 200
    assert "owner만의 답변" not in resp.text
    assert "아직 이 질문에 대한 초안이 없습니다" in resp.text


# ── 구 점수·재채점 요청 (2026-10-15) ────────────────────────────────────

def _job_with_score(user_id: int, **score_fields) -> int:
    from analysis.scorer import SCORER_VERSION
    s = TestSession()
    job = Job(source="a", url="https://ex.com/1", title="ML Engineer", company="Acme")
    s.add(job)
    s.flush()
    fields = {"fit_score": 80, "profile_version": 1, "scorer_version": SCORER_VERSION, **score_fields}
    s.add(JobScore(job_id=job.id, user_id=user_id, **fields))
    s.commit()
    job_id = job.id
    s.close()
    return job_id


def _score(user_id: int) -> JobScore:
    s = TestSession()
    try:
        return s.query(JobScore).filter_by(user_id=user_id).one()
    finally:
        s.close()


def test_dashboard_marks_stale_scores_and_requests_rescore_for_all_of_them():
    user_id = _make_user()
    _job_with_score(user_id, scorer_version=1)
    _login_as(user_id)

    page = client.get("/dashboard").text
    assert "구 점수" in page and "구 점수 1건" in page

    resp = client.post("/scores/rescore-stale", follow_redirects=False)
    assert resp.status_code == 303
    assert _score(user_id).rescore_requested_at is not None
    assert "재채점 대기" in client.get("/dashboard").text


def test_dashboard_has_no_rescore_banner_when_scores_are_current():
    user_id = _make_user()
    _job_with_score(user_id)
    _login_as(user_id)

    assert "구 점수" not in client.get("/dashboard").text


def test_job_detail_rescore_request_for_one_job():
    user_id = _make_user()
    job_id = _job_with_score(user_id)
    _login_as(user_id)

    client.post(f"/jobs/{job_id}/rescore")
    assert _score(user_id).rescore_requested_at is not None
    assert "재채점 요청됨" in client.get(f"/jobs/{job_id}").text


# ── 지원 상태 변경 (웹 [제출 완료] 등) ──────────────────────────────────

def test_job_detail_status_submit_records_change_with_web_source():
    user_id = _make_user()
    job_id = _job_with_score(user_id)
    _login_as(user_id)

    resp = client.post(f"/jobs/{job_id}/status", data={"status": "submitted"}, follow_redirects=False)
    assert resp.status_code == 303

    s = TestSession()
    try:
        app = s.query(Application).filter_by(job_id=job_id, user_id=user_id).one()
        assert app.status == "submitted" and app.submitted_at is not None
        event = s.query(ApplicationEvent).one()
        assert (event.from_status, event.to_status, event.source) == (None, "submitted", "web")
    finally:
        s.close()
    page = client.get(f"/jobs/{job_id}").text
    assert "지원 상태: 제출 완료" in page


def test_job_detail_status_rejects_unknown_status():
    user_id = _make_user()
    job_id = _job_with_score(user_id)
    _login_as(user_id)

    assert client.post(f"/jobs/{job_id}/status", data={"status": "interested"}).status_code == 400


def test_adding_experience_bumps_profile_version():
    user_id = _make_user()
    _login_as(user_id)
    s = TestSession()
    before = s.get(User, user_id).profile_version
    s.close()

    client.post("/experiences/add", data={"kind": "project", "content": "RAG 챗봇"})

    s = TestSession()
    try:
        assert s.get(User, user_id).profile_version == before + 1
    finally:
        s.close()
