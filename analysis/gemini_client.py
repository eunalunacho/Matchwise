from langchain_google_genai import ChatGoogleGenerativeAI
from shared.config import get_settings


def get_llm() -> ChatGoogleGenerativeAI:
    settings = get_settings()
    return ChatGoogleGenerativeAI(
        model=settings.gemini.model,
        google_api_key=settings.gemini.api_key.get_secret_value() if settings.gemini.api_key else "",
        temperature=0.3,
    )
