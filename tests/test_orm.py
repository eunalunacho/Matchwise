import pytest
from sqlalchemy.exc import IntegrityError
from db.orm import User, Job, JobScore


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
