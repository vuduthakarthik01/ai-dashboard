from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://industrialmind:industrialmind@localhost:5432/industrialmind"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720
    anthropic_api_key: str | None = None
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    simulate: bool = True

    class Config:
        env_file = ".env"


settings = Settings()
