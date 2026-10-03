from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    MONGODB_URI: str
    MONGODB_DB: str = "pas_db"

    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60

    # OTP validity window in seconds (default 5 minutes)
    OTP_EXPIRE_SECONDS: int = 300


settings = Settings()
