import psycopg
import json
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


class AsyncPostgresRagStore:
    def __init__(self, database_url: str):
        self.database_url = database_url

    async def replace_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        source: str,
        chunks: list[TextChunk],
        embeddings: list[list[float]],
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