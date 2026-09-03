import json
from dataclasses import dataclass
from typing import Any

import psycopg

from src.rag.chunking import TextChunk
# 数据库写入、查询
class PostgresRagStore:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def ping(self) -> bool:
        """执行 SELECT 1，验证 PostgreSQL 是否可连接。"""

        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                row = cursor.fetchone()

        return row == (1,)

    def count_chunks(self, tenant_id: str) -> int:
        """统计指定租户的 RAG 文档块数量。"""

        sql = """
        SELECT count(*)
        FROM rag.document_chunks
        WHERE tenant_id = %s
        """

        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (tenant_id,))
                row = cursor.fetchone()

        if row is None:
            return 0

        return int(row[0])
def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(map(str, vector)) + "]"

@dataclass(frozen=True)
class RagSearchResult:
    chunk_id: str
    document_id: str
    source: str
    section: str | None
    content: str
    similarity: float
    metadata: dict[str, Any]

@dataclass(frozen=True)
class RagStoredChunk:
    chunk_id: str
    document_id: str
    source: str
    section: str | None
    content: str
    metadata: dict[str, Any]

class AsyncPostgresRagStore:
    def __init__(self, database_url: str):
        self.database_url = database_url


    # 把切块结果和向量配对替换进rag.document_chunks 表
    async def replace_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        source: str,
        chunks: list[TextChunk],  # ← chunking.py 的产物
        embeddings: list[list[float]],  # ← embedding_client.py 的产物
        embedding_model: str,
        metadata: dict | None = None,
    ) -> int:
        if len(chunks) != len(embeddings):
            raise ValueError("chunk 数量和 embedding 数量不一致")

        insert_sql = """
        INSERT INTO rag.document_chunks (
            tenant_id,
            document_id,
            chunk_id,
            source,
            section,
            content,
            content_hash,
            embedding_model,
            embedding,
            metadata
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s::vector, %s::jsonb
        )
        """

        rows = []

        for chunk, embedding in zip(chunks, embeddings):
            chunk_metadata = {
                **(metadata or {}),
                "chunk_index": chunk.index,
                "start_char": chunk.start_char,
                "end_char": chunk.end_char,
            }

            rows.append(
                (
                    tenant_id,
                    document_id,
                    chunk.chunk_id,
                    source,
                    None,
                    chunk.content,
                    chunk.content_hash,
                    embedding_model,
                    vector_literal(embedding),
                    json.dumps(
                        chunk_metadata,
                        ensure_ascii=False,
                    ),
                )
            )

        connection = await psycopg.AsyncConnection.connect(
            self.database_url
        )

        async with connection:
            async with connection.cursor() as cursor:
                async with connection.transaction():
                    await cursor.execute(
                        """
                        DELETE FROM rag.document_chunks
                        WHERE tenant_id = %s
                          AND document_id = %s
                        """,
                        (tenant_id, document_id),
                    )

                    if rows:
                        await cursor.executemany(
                            insert_sql,
                            rows,
                        )

        return len(rows)
    
    async def search_chunks(
        self,
        *,
        tenant_id: str,
        query_embedding: list[float],  # ← embed_query 的产物
        embedding_model: str,
        embedding_dimensions: int,
        top_k: int = 5,
    ) -> list[RagSearchResult]:
        # 检索用
        if not 1 <= top_k <= 100:
            raise ValueError("top_k 必须在 1 到 100 之间")

        if len(query_embedding) != embedding_dimensions:
            raise ValueError(
                "查询向量维度不正确："
                f"预期 {embedding_dimensions}，"
                f"实际 {len(query_embedding)}"
            )

        query_vector = vector_literal(query_embedding)
        # <=>：pgvector 的余弦距离操作符
        sql = """
        SELECT
            chunk_id,
            document_id,
            source,
            section,
            content,
            1 - (embedding <=> %s::vector) AS similarity,
            metadata
        FROM rag.document_chunks
        WHERE tenant_id = %s
          AND embedding_model = %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """

        connection = await psycopg.AsyncConnection.connect(
            self.database_url
        )

        async with connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    sql,
                    (
                        query_vector,
                        tenant_id,
                        embedding_model,
                        query_vector,
                        top_k,
                    ),
                )

                rows = await cursor.fetchall()

        return [
            RagSearchResult(
                chunk_id=row[0],
                document_id=row[1],
                source=row[2],
                section=row[3],
                content=row[4],
                similarity=float(row[5]),
                metadata=dict(row[6] or {}),
            )
            for row in rows
        ]
    async def list_chunks(
        self,
        *,
        tenant_id: str,
        embedding_model: str,
    ) -> list[RagStoredChunk]:
        sql = """
        SELECT
            chunk_id,
            document_id,
            source,
            section,
            content,
            metadata
        FROM rag.document_chunks
        WHERE tenant_id = %s
          AND embedding_model = %s
        ORDER BY document_id, chunk_id
        """

        connection = await psycopg.AsyncConnection.connect(
            self.database_url
        )

        async with connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    sql,
                    (
                        tenant_id,
                        embedding_model,
                    ),
                )

                rows = await cursor.fetchall()

        return [
            RagStoredChunk(
                chunk_id=row[0],
                document_id=row[1],
                source=row[2],
                section=row[3],
                content=row[4],
                metadata=dict(row[5] or {}),
            )
            for row in rows
        ]