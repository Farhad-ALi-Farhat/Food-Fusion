from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://user:password@localhost:5432/foodfusion"

    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""

    admin_numbers: str = ""  # comma-separated

    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    groq_api_key: str = ""

    @property
    def admin_number_set(self) -> set[str]:
        return {normalize_whatsapp_number(n) for n in self.admin_numbers.split(",") if n.strip()}


def normalize_whatsapp_number(raw: str) -> str:
    """Match Meta Cloud API `from` / `to` (digits only, no +)."""
    return "".join(c for c in raw.strip() if c.isdigit())


settings = Settings()
