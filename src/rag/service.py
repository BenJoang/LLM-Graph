# 组合 embedding 和数据库
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from src.rag.chunking import split_document
from src.rag.embedding_client import AsyncEmbeddingClient
from src.rag.postgres_store import AsyncPostgresRagStore


load_dotenv()


async def ingest_file(
    file_path: Path,
    *,
    tenant_id: str,
    document_id: str,
    batch_size: int = 8,
) -> int:
    text = await asyncio.to_thread(
        file_path.read_text,
        encoding="utf-8",
    )

    chunks = split_document(
        text,
        document_id=document_id,
        chunk_size=800,
        overlap=120,
    )

    embedding_client = AsyncEmbeddingClient()
    embeddings: list[list[float]] = []

    try:
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start:start + batch_size]

            batch_embeddings = (
                await embedding_client.embed_documents(
                    [chunk.content for chunk in batch]
                )
            )

            embeddings.extend(batch_embeddings)
    finally:
        await embedding_client.close()

    database_url = os.environ["RAG_POSTGRES_URL"]

    store = AsyncPostgresRagStore(database_url)

    return await store.replace_document(
        tenant_id=tenant_id,
        document_id=document_id,
        source=str(file_path),
        chunks=chunks,
        embeddings=embeddings,
        embedding_model=os.environ["RAG_EMBEDDING_MODEL"],
        metadata={
            "filename": file_path.name,
            "file_type": file_path.suffix.lower(),
        },
    )