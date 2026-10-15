"""설정 로딩 유닛 테스트.

.env에 값이 아예 없는 경우와 빈 문자열로 남아있는 경우를 구분해서 검증한다 —
pydantic-settings는 기본적으로 이 둘을 다르게 취급한다(빈 문자열은 "설정된 값"으로 봄).
"""
import os
from unittest.mock import patch

from shared.config import Settings


def test_ocr_provider_defaults_to_document_ai_when_env_unset():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OCR_PROVIDER", None)
        assert Settings().ocr_provider == "document_ai"


def test_ocr_provider_falls_back_to_default_when_env_is_blank():
    """OCR_PROVIDER=(빈 값)으로 .env에 남겨진 흔한 실수 — 기본값(document_ai)으로 돌아가야 한다."""
    with patch.dict(os.environ, {"OCR_PROVIDER": ""}):
        assert Settings().ocr_provider == "document_ai"


def test_ocr_provider_falls_back_when_env_is_whitespace_only():
    with patch.dict(os.environ, {"OCR_PROVIDER": "   "}):
        assert Settings().ocr_provider == "document_ai"


def test_ocr_provider_honors_explicit_value():
    with patch.dict(os.environ, {"OCR_PROVIDER": "claude_code"}):
        assert Settings().ocr_provider == "claude_code"


def test_claude_ocr_model_falls_back_to_default_when_env_is_blank():
    with patch.dict(os.environ, {"CLAUDE_OCR_MODEL": ""}):
        assert Settings().claude_ocr_model == "sonnet"
