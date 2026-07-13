from ollama import Client

from app.config import get_settings


class LLMClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.client = Client(host=settings.ollama_host)
        self.model = settings.chat_model
        self.embedding_model = settings.embedding_model

    def complete(self, system: str, user: str) -> str:
        response = self.client.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )

        content = response["message"]["content"]

        if not content:
            raise RuntimeError("Ollama returned an empty response.")

        return content.strip()

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embed(
            model=self.embedding_model,
            input=texts,
        )

        embeddings = response["embeddings"]

        if not embeddings:
            raise RuntimeError("Ollama returned no embeddings.")

        return embeddings