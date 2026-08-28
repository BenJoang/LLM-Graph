import psycopg


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