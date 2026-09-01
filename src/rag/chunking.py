import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    index: int
    chunk_id: str
    content: str
    content_hash: str
    start_char: int
    end_char: int


def split_document(
    text: str,
    document_id: str,
    chunk_size: int = 800,
    overlap: int = 120,
) -> list[TextChunk]:
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")

    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须满足 0 <= overlap < chunk_size")

    normalized = text.replace("\r\n", "\n").strip()

    if not normalized:
        return []

    chunks: list[TextChunk] = []
    start = 0
    index = 0

    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))

        # 尽量在换行或句号处结束，避免截断句子
        if end < len(normalized):
            search_start = max(start, end - 160)
            boundary = max(
                normalized.rfind("\n", search_start, end),
                normalized.rfind("。", search_start, end),
                normalized.rfind("！", search_start, end),
                normalized.rfind("？", search_start, end),
            )

            if boundary > start:
                end = boundary + 1

        content = normalized[start:end].strip()

        if content:
            content_hash = hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest()

            chunk_id = (
                f"{document_id}:{index}:"
                f"{content_hash[:12]}"
            )

            chunks.append(
                TextChunk(
                    index=index,
                    chunk_id=chunk_id,
                    content=content,
                    content_hash=content_hash,
                    start_char=start,
                    end_char=end,
                )
            )

            index += 1

        if end >= len(normalized):
            break

        next_start = end - overlap

        if next_start <= start:
            next_start = end

        start = next_start

    return chunks