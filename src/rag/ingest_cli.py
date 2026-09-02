import argparse
import asyncio
import sys
from pathlib import Path

from src.rag.service import ingest_file


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="将文本文件切片并写入 RAG 数据库"
    )

    parser.add_argument("file")
    parser.add_argument("--tenant-id", default="study")
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-concurrency", type=int, default=3)
    parser.add_argument("--chunk-size", type=int, default=800)
    parser.add_argument("--overlap", type=int, default=120)

    args = parser.parse_args()

    written = await ingest_file(
        Path(args.file),
        tenant_id=args.tenant_id,
        document_id=args.document_id,
        batch_size=args.batch_size,
        max_concurrency=args.max_concurrency,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
    )

    print(f"文档 ID：{args.document_id}")
    print(f"写入块数：{written}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(
            main(),
            loop_factory=asyncio.SelectorEventLoop,
        )
    else:
        asyncio.run(main())