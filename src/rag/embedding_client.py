# 调用 qwen3 embedding
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from openai import AsyncOpenAI


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class EmbeddingSettings:
    base_url: str
    api_key: str
    model: str
    dimensions: int

    @classmethod
    def from_env(cls) -> "EmbeddingSettings":
        base_url = os.getenv("RAG_EMBEDDING_BASE_URL", "").strip()
        api_key = os.getenv("RAG_EMBEDDING_API_KEY", "").strip()
        model = os.getenv("RAG_EMBEDDING_MODEL", "").strip()
        dimensions_text = os.getenv(
            "RAG_EMBEDDING_DIMENSIONS",
            "1024",
        ).strip()

        missing = []

        if not base_url:
            missing.append("RAG_EMBEDDING_BASE_URL")

        if not api_key:
            missing.append("RAG_EMBEDDING_API_KEY")

        if not model:
            missing.append("RAG_EMBEDDING_MODEL")

        if missing:
            names = ", ".join(missing)
            raise ValueError(f"缺少环境变量：{names}")

        return cls(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            model=model,
            dimensions=int(dimensions_text),
        )


class EmbeddingClient:
    def __init__(
        self,
        settings: EmbeddingSettings | None = None,
        timeout: float = 60,
    ) -> None:
        self.settings = settings or EmbeddingSettings.from_env()

        self.client = OpenAI(
            base_url=self.settings.base_url,
            api_key=self.settings.api_key,
            timeout=timeout,
        )

    def embed_documents(
        self,
        texts: Sequence[str],
    ) -> list[list[float]]:
        normalized = [text.strip() for text in texts]

        if not normalized:
            return []

        if any(not text for text in normalized):
            raise ValueError("不能为纯空白文本生成 embedding")

        response = self.client.embeddings.create(
            model=self.settings.model,
            input=normalized,
        )

        items = sorted(
            response.data,
            key=lambda item: item.index,
        )

        vectors = [
            list(item.embedding)
            for item in items
        ]

        if len(vectors) != len(normalized):
            raise RuntimeError(
                "Embedding 服务返回的向量数量与输入数量不一致"
            )

        for vector in vectors:
            if len(vector) != self.settings.dimensions:
                raise RuntimeError(
                    "Embedding 维度不正确："
                    f"预期 {self.settings.dimensions}，"
                    f"实际 {len(vector)}"
                )

        return vectors

    def embed_query(self, query: str) -> list[float]:
        vectors = self.embed_documents([query])
        return vectors[0]

from openai import AsyncOpenAI


class AsyncEmbeddingClient:
    def __init__(
        self,
        settings: EmbeddingSettings | None = None,
        timeout: float = 120,
    ) -> None:
        self.settings = settings or EmbeddingSettings.from_env()

        self.client = AsyncOpenAI(
            base_url=self.settings.base_url,
            api_key=self.settings.api_key,
            timeout=timeout,
        )

    async def embed_documents(
        self,
        texts: Sequence[str],
    ) -> list[list[float]]:
        normalized = [text.strip() for text in texts]

        if not normalized:
            return []

        if any(not text for text in normalized):
            raise ValueError("不能为纯空白文本生成 embedding")

        response = await self.client.embeddings.create(
            model=self.settings.model,
            input=normalized,
        )

        items = sorted(
            response.data,
            key=lambda item: item.index,
        )

        vectors = [
            list(item.embedding)
            for item in items
        ]

        if len(vectors) != len(normalized):
            raise RuntimeError("Embedding 数量与输入数量不一致")

        for vector in vectors:
            if len(vector) != self.settings.dimensions:
                raise RuntimeError(
                    f"预期 {self.settings.dimensions} 维，"
                    f"实际 {len(vector)} 维"
                )

        return vectors

    async def embed_query(self, query: str) -> list[float]:
        vectors = await self.embed_documents([query])
        return vectors[0]

    async def close(self) -> None:
        await self.client.close()