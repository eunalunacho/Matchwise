from langchain_google_genai import ChatGoogleGenerativeAI
from shared.config import get_settings


def get_llm(temperature: float = 0.3) -> ChatGoogleGenerativeAI:
    """Gemini 채팅 모델. 분류처럼 답이 하나여야 하는 호출은 temperature=0 (analysis/direction.py)."""
    settings = get_settings()
    return ChatGoogleGenerativeAI(
        model=settings.gemini.model,
        google_api_key=settings.gemini.api_key.get_secret_value() if settings.gemini.api_key else "",
        temperature=temperature,
    )
