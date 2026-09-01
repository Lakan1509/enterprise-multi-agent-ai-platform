from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Enterprise Multi-Agent AI Platform"

    ollama_host: str = "http://localhost:11434"
    chat_model: str = "qwen2.5:7b"
    embedding_model: str = "embeddinggemma"

    api_key: str = ""
    top_k: int = 4

    index_path: str = "data/faiss.index"
    metadata_path: str = "data/metadata.json"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()