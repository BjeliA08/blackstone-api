from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_HOURS: int = 24
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""
    PHOTO_URL_TTL_SECONDS: int = 300

    model_config = {"env_file": ".env"}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Railway uses postgres:// — SQLAlchemy requires postgresql://
        if self.DATABASE_URL.startswith("postgres://"):
            object.__setattr__(self, "DATABASE_URL",
                               self.DATABASE_URL.replace("postgres://", "postgresql://", 1))


settings = Settings()
