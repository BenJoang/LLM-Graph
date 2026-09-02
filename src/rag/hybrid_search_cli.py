import argparse
import asyncio
import sys

from src.rag.hybrid_retriever import hybrid_retrieve


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--tenant-id", default="study")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    rows = await hybrid_retrieve(
        args.question,
        tenant_id=args.tenant_id,
        top_k=args.top_k,
    )

    print(f"找到 {len(rows)} 个结果")

    for index, row in enumerate(rows, start=1):
        print()
        print(f"排名：{index}")
        print(f"Chunk：{row.chunk_id}")
        print(f"来源：{row.source}")
        print(f"向量相似度：{row.vector_similarity}")
        print(f"BM25：{row.bm25_score}")
        print(f"RRF：{row.rrf_score:.6f}")
        print(f"内容：{row.content}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(
            main(),
            loop_factory=asyncio.SelectorEventLoop,
        )
    else:
        asyncio.run(main())