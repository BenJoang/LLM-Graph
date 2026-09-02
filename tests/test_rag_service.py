import pytest

from src.rag.chunking import TextChunk
from src.rag.service import _embed_chunks_concurrently


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.active_requests = 0
        self.max_active_requests = 0

    async def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        import asyncio

        self.active_requests += 1
        self.max_active_requests = max(
            self.max_active_requests,
            self.active_requests,
        )

        try:
            await asyncio.sleep(0.02)

            return [
                [float(text.removeprefix("chunk-"))]
                for text in texts
            ]
        finally:
            self.active_requests -= 1


def make_chunks(count: int) -> list[TextChunk]:
    return [
        TextChunk(
            index=index,
            chunk_id=f"document:{index}",
            content=f"chunk-{index}",
            content_hash=f"hash-{index}",
            start_char=index,
            end_char=index + 1,
        )
        for index in range(count)
    ]


@pytest.mark.asyncio
async def test_embedding_batches_are_concurrent_and_ordered() -> None:
    client = FakeEmbeddingClient()

    embeddings = await _embed_chunks_concurrently(
        make_chunks(10),
        embedding_client=client,  # type: ignore[arg-type]
        batch_size=2,
        max_concurrency=3,
    )

    assert embeddings == [
        [float(index)]
        for index in range(10)
    ]

    assert client.max_active_requests == 3


@pytest.mark.asyncio
async def test_empty_chunks_do_not_call_embedding() -> None:
    client = FakeEmbeddingClient()

    embeddings = await _embed_chunks_concurrently(
        [],
        embedding_client=client,  # type: ignore[arg-type]
        batch_size=8,
        max_concurrency=3,
    )

    assert embeddings == []
    assert client.max_active_requests == 0