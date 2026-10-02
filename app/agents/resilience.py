from langchain.agents.middleware import ModelFallbackMiddleware
from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import settings

_fallback_llm: ChatGoogleGenerativeAI | None = None


def get_fallback_llm() -> ChatGoogleGenerativeAI:
    global _fallback_llm
    if _fallback_llm is None:
        _fallback_llm = ChatGoogleGenerativeAI(
            model="gemini-3.5-flash-lite",
            google_api_key=settings.gemini_api_key,
            timeout=20,
            max_retries=1,
        )
    return _fallback_llm


def build_resilience_middleware() -> list:
    """If the Groq call raises (429, timeout, ...), retry the turn on Gemini."""
    return [ModelFallbackMiddleware(get_fallback_llm())]