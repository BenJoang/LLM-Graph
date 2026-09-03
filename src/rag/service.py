# 组合切片、embedding 和数据库入库
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from src.rag.chunking import TextChunk, split_document
from src.rag.embedding_client import AsyncEmbeddingClient
from src.rag.postgres_store import (
    AsyncPostgresRagStore,
    RagSearchResult,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


async def _embed_chunks_concurrently(
    chunks: list[TextChunk],
    *,
    embedding_client: AsyncEmbeddingClient,
    batch_size: int,
    max_concurrency: int,
) -> list[list[float]]:
    """
    将 chunks 分批，并发请求 embedding。

    max_concurrency 只限制同时执行的 embedding 请求数量；
    asyncio.TaskGroup 会在任意批次失败时取消其他批次。
    """
    if batch_size <= 0:
        raise ValueError("batch_size 必须大于 0")

    if max_concurrency <= 0:
        raise ValueError("max_concurrency 必须大于 0")

    if not chunks:
        return []
    #各批次起点
    batch_starts = list(range(0, len(chunks), batch_size))
    #限制同时并发数量量
    semaphore = asyncio.Semaphore(max_concurrency)

    # 预留结果位置，确保并发完成顺序不会打乱 chunk 顺序。
    batch_results: list[list[list[float]] | None] = [
        None
        for _ in batch_starts
    ]

    async def embed_one_batch(
        batch_index: int,
        start: int,
    ) -> None:
        batch = chunks[start:start + batch_size]
        texts = [chunk.content for chunk in batch]

        async with semaphore:
            vectors = await embedding_client.embed_documents(texts)

        if len(vectors) != len(batch):
            raise RuntimeError(
                f"第 {batch_index} 批 embedding 数量不一致："
                f"chunks={len(batch)}，vectors={len(vectors)}"
            )

        batch_results[batch_index] = vectors

    #这里是注册任务，还没开始跑
    async with asyncio.TaskGroup() as task_group:
        for batch_index, start in enumerate(batch_starts):
            task_group.create_task(
                embed_one_batch(batch_index, start)
            )

    if any(result is None for result in batch_results):
        raise RuntimeError("部分 embedding 批次没有返回结果")

    return [
        vector
        for batch_result in batch_results
        if batch_result is not None
        for vector in batch_result
    ]


async def ingest_file(
    file_path: Path,
    *,
    tenant_id: str,
    document_id: str,
    batch_size: int = 8,
    max_concurrency: int = 3,
    chunk_size: int = 800,
    overlap: int = 120,
) -> int:
    """
    读取一个 UTF-8 文本文件，切片、并发生成 embedding，
    最后在一个数据库事务中替换整篇文档。
    """
    file_path = file_path.resolve()

    if not file_path.is_file():
        raise FileNotFoundError(f"文件不存在：{file_path}")

    text = await asyncio.to_thread(
        file_path.read_text,
        encoding="utf-8",
    )

    chunks = split_document(
        text,
        document_id=document_id,
        chunk_size=chunk_size,
        overlap=overlap,
    )

    embedding_client = AsyncEmbeddingClient()
    embedding_model = embedding_client.settings.model

    try:
        embeddings = await _embed_chunks_concurrently(
            chunks,
            embedding_client=embedding_client,
            batch_size=batch_size,
            max_concurrency=max_concurrency,
        )
    finally:
        await embedding_client.close()

    if len(chunks) != len(embeddings):
        raise RuntimeError(
            "全部批次结束后，chunk 与 embedding 总数不一致"
        )

    database_url = os.environ["RAG_POSTGRES_URL"]
    store = AsyncPostgresRagStore(database_url)

    return await store.replace_document(
        tenant_id=tenant_id,
        document_id=document_id,
        source=str(file_path),
        chunks=chunks,
        embeddings=embeddings,
        embedding_model=embedding_model,
        metadata={
            "filename": file_path.name,
            "file_type": file_path.suffix.lower(),
        },
    )
async def retrieve_chunks(
    question: str,
    *,
    tenant_id: str,
    top_k: int = 5,
) -> list[RagSearchResult]:
    """
    将用户问题转成查询向量，并从 PostgreSQL 召回相关块。
    """
    question = question.strip()

    if not question:
        raise ValueError("question 不能为空")

    embedding_client = AsyncEmbeddingClient()

    try:
        query_embedding = await embedding_client.embed_query(question)
        embedding_model = embedding_client.settings.model
        embedding_dimensions = embedding_client.settings.dimensions
    finally:
        await embedding_client.close()

    database_url = os.environ["RAG_POSTGRES_URL"]
    store = AsyncPostgresRagStore(database_url)

    return await store.search_chunks(
        tenant_id=tenant_id,
        query_embedding=query_embedding,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
        top_k=top_k,
    )