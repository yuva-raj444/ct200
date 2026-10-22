from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = "sqlite:///./ct200.db"
    llm_provider: str = "gemini"
    llm_api_key: str | None = None
    llm_model: str = "gemini-3.1-flash-lite"
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    generated_store_path: str = "./generated_outputs.json"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
