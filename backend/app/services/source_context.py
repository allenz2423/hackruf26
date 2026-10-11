"""Bounded source excerpts that give every selected readable upload a share."""

from __future__ import annotations


def source_excerpts(sources: list[dict], max_chars: int) -> list[dict]:
    """Share the text budget evenly, redistributing space from short sources."""
    readable = [
        (source, str(source.get("extracted_text") or source.get("summary") or ""))
        for source in sources
    ]
    readable = [(source, text) for source, text in readable if text.strip()]
    budgets = [0] * len(readable)
    remaining = max(0, max_chars)
    active = list(range(len(readable)))
    while remaining and active:
        share = max(1, remaining // len(active))
        next_active = []
        for index in active:
            available = len(readable[index][1]) - budgets[index]
            allocated = min(available, share, remaining)
            budgets[index] += allocated
            remaining -= allocated
            if allocated < available:
                next_active.append(index)
        active = next_active

    result = []
    for (source, text), budget in zip(readable, budgets, strict=True):
        if budget == 0:
            continue
        truncated = budget < len(text)
        marker = "\n[... source text omitted ...]\n"
        if truncated and budget > len(marker) + 2:
            head = (budget - len(marker)) // 2
            excerpt = text[:head] + marker + text[-(budget - len(marker) - head) :]
        else:
            excerpt = text[:budget]
        result.append(
            {
                "upload_id": str(source["id"]),
                "title": str(source["title"]),
                "text": excerpt,
                "truncated": truncated,
                "original_char_count": len(text),
            }
        )
    return result
