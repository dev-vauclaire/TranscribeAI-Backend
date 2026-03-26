import os
# Configuration commune à toute les instances de l'application
class BaseConfig:
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = os.getenv("SECRET_KEY", "default_secret")
    AUDIO_STORAGE_PATH = os.getenv("AUDIO_STORAGE_PATH", "./audio_files")
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    DEBUG = bool(os.getenv("SECRET_KEY", "True"))