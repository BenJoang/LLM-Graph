import asyncio
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import jieba
from rank_bm25 import BM25Okapi

from src.rag.embedding_client import EmbeddingSettings
from src.rag.postgres_store import (
    AsyncPostgresRagStore,
    RagStoredChunk,
)
from src.rag.service import retrieve_chunks


@dataclass(frozen=True)
class HybridSearchResult:
    chunk_id: str
    document_id: str
    source: str
    section: str | None
    content: str
    metadata: dict[str, Any]

    vector_similarity: float | None
    bm25_score: float | None
    rrf_score: float


def tokenize(text: str) -> list[str]:
    """
    对中英文混合文本进行简单分词。

    英文统一转成小写；
    标点和纯空白 token 会被丢弃。
    """
    tokens = jieba.lcut(
        text.lower(),
        cut_all=False,
    )

    return [
        token.strip()
        for token in tokens
        if token.strip()
        and any(character.isalnum() for character in token)
    ]


def chunk_search_text(chunk: RagStoredChunk) -> str:
    """
    BM25 不只搜索正文，也搜索文档 ID、来源和章节。
    这样文件名、编号等精确关键词也能命中。
    """
    return "\n".join(
        value
        for value in (
            chunk.chunk_id,
            chunk.document_id,
            chunk.source,
            chunk.section,
            chunk.content,
        )
        if value
    )


def bm25_search(
    question: str,
    chunks: list[RagStoredChunk],
    *,
    top_k: int,
) -> list[tuple[RagStoredChunk, float]]:
    if not chunks:
        return []

    query_tokens = tokenize(question)

    if not query_tokens:
        return []

    tokenized_corpus = [
        tokenize(chunk_search_text(chunk))
        for chunk in chunks
    ]

    bm25 = BM25Okapi(tokenized_corpus)
    scores = bm25.get_scores(query_tokens)

    ranked_indexes = sorted(
        range(len(chunks)),
        key=lambda index: float(scores[index]),
        reverse=True,
    )

    results: list[tuple[RagStoredChunk, float]] = []

    for index in ranked_indexes:
        score = float(scores[index])

        # 全部关键词都没匹配时，不把任意文档当成 BM25 命中。
        if score <= 0:
            continue

        results.append((chunks[index], score))

        if len(results) >= top_k:
            break

    return results


async def hybrid_retrieve(
    question: str,
    *,
    tenant_id: str,
    top_k: int = 5,
    candidate_k: int = 20,
    rrf_k: int = 60,
) -> list[HybridSearchResult]:
    question = question.strip()

    if not question:
        raise ValueError("question 不能为空")

    if not 1 <= top_k <= 100:
        raise ValueError("top_k 必须在 1 到 100 之间")

    if candidate_k <= 0:
        raise ValueError("candidate_k 必须大于 0")

    candidate_k = min(
        100,
        max(candidate_k, top_k),
    )

    settings = EmbeddingSettings.from_env()
    database_url = os.environ["RAG_POSTGRES_URL"]
    store = AsyncPostgresRagStore(database_url)

    # 向量查询和 BM25 文档加载可以同时进行。
    vector_results, stored_chunks = await asyncio.gather(
        retrieve_chunks(
            question,
            tenant_id=tenant_id,
            top_k=candidate_k,
        ),
        store.list_chunks(
            tenant_id=tenant_id,
            embedding_model=settings.model,
        ),
    )

    keyword_results = bm25_search(
        question,
        stored_chunks,
        top_k=candidate_k,
    )

    chunk_by_id = {
        chunk.chunk_id: chunk
        for chunk in stored_chunks
    }

    vector_by_id = {
        result.chunk_id: result.similarity
        for result in vector_results
    }

    bm25_by_id = {
        chunk.chunk_id: score
        for chunk, score in keyword_results
    }

    rrf_scores: dict[str, float] = defaultdict(float)

    for rank, result in enumerate(vector_results, start=1):
        rrf_scores[result.chunk_id] += 1 / (rrf_k + rank)

    for rank, (chunk, _) in enumerate(keyword_results, start=1):
        rrf_scores[chunk.chunk_id] += 1 / (rrf_k + rank)

    results = [
        HybridSearchResult(
            chunk_id=chunk_id,
            document_id=chunk_by_id[chunk_id].document_id,
            source=chunk_by_id[chunk_id].source,
            section=chunk_by_id[chunk_id].section,
            content=chunk_by_id[chunk_id].content,
            metadata=chunk_by_id[chunk_id].metadata,
            vector_similarity=vector_by_id.get(chunk_id),
            bm25_score=bm25_by_id.get(chunk_id),
            rrf_score=rrf_score,
        )
        for chunk_id, rrf_score in rrf_scores.items()
        if chunk_id in chunk_by_id
    ]

    results.sort(
        key=lambda result: (
            result.rrf_score,
            result.vector_similarity
            if result.vector_similarity is not None
            else -1.0,
            result.bm25_score
            if result.bm25_score is not None
            else -1.0,
        ),
        reverse=True,
    )

    return results[:top_k]