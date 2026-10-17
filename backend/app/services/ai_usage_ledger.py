"""Durable, content-free usage ledger for the configured AI provider."""

from __future__ import annotations

import asyncio
import logging
import threading
from contextvars import ContextVar, Token
from typing import Any
from uuid import uuid4

from app.services.database import SQLiteDatabase

logger = logging.getLogger("screen_tutor.ai_usage_ledger")
MICRO_USD = 1_000_000

_usage_context: ContextVar[dict[str, str]] = ContextVar("ai_usage_context", default={})


def bind_usage_context(**values: object) -> Token[dict[str, str]]:
    """Add safe operation labels to provider calls made in this context."""
    merged = dict(_usage_context.get())
    for key, value in values.items():
        if isinstance(value, str) and value.strip():
            merged[key] = value.strip()[:300]
    return _usage_context.set(merged)


def reset_usage_context(token: Token[dict[str, str]]) -> None:
    _usage_context.reset(token)


def usage_context_values() -> dict[str, str]:
    return dict(_usage_context.get())


class UsageLedgerRecorder:
    """Persist provider-call metadata without retaining prompts or completions."""

    def __init__(self, database: SQLiteDatabase, event_loop: asyncio.AbstractEventLoop) -> None:
        self._database = database
        self._event_loop = event_loop
        self._event_loop_thread = threading.get_ident()

    @staticmethod
    def _row(event: dict[str, Any]) -> tuple[object, ...]:
        context = usage_context_values()
        outcome = event.get("outcome")
        if outcome not in {"success", "http_error", "invalid_response", "timeout", "transport_error"}:
            outcome = "transport_error"
        integers = (
            "http_status", "attempt", "prompt_tokens", "completion_tokens",
            "reasoning_tokens", "total_tokens", "cost_microusd",
        )
        safe_numbers: dict[str, int | None] = {}
        for name in integers:
            value = event.get(name)
            safe_numbers[name] = value if type(value) is int and value >= 0 else None
        cost = safe_numbers["cost_microusd"]
        delta = -cost if cost is not None else None
        event_id = str(uuid4())
        operation_kind = context.get("operation_kind") or "other"
        operation_label = context.get("operation_label") or context.get("request_path") or "AI request"
        model = event.get("model")
        provider_host = event.get("provider_host")
        actual_model = event.get("actual_model")
        request_id = event.get("provider_request_id")
        for value_name, value in (
            ("model", model), ("provider_host", provider_host),
            ("actual_model", actual_model), ("provider_request_id", request_id),
        ):
            if not isinstance(value, str):
                if value_name == "model":
                    model = "unknown"
                elif value_name == "provider_host":
                    provider_host = "unknown"
                elif value_name == "actual_model":
                    actual_model = None
                else:
                    request_id = None
        return (
            event_id,
            context.get("operation_id"),
            operation_kind[:80],
            operation_label[:300],
            str(provider_host)[:253],
            str(model)[:200],
            str(actual_model)[:200] if actual_model else None,
            str(request_id)[:160] if request_id else None,
            safe_numbers["attempt"] or 1,
            outcome,
            safe_numbers["http_status"],
            safe_numbers["prompt_tokens"],
            safe_numbers["completion_tokens"],
            safe_numbers["reasoning_tokens"],
            safe_numbers["total_tokens"],
            cost,
            delta,
        )

    async def record_async(self, event: dict[str, Any]) -> None:
        await self._insert(self._row(event))

    def record_sync(self, event: dict[str, Any]) -> None:
        row = self._row(event)
        if threading.get_ident() == self._event_loop_thread:
            # A sync adapter call made on the loop must not deadlock that loop.
            self._event_loop.create_task(self._insert(row))
            return
        future = asyncio.run_coroutine_threadsafe(self._insert(row), self._event_loop)
        try:
            future.result(timeout=10)
        except Exception as exc:
            logger.error("ai_usage_ledger_write_failed category=%s", type(exc).__name__)
            raise RuntimeError("Provider usage could not be recorded; generation was stopped.") from None

    async def _insert(self, row: tuple[object, ...]) -> None:
        await self._database.execute(
            "INSERT INTO ai_usage_ledger_entries "
            "(id,operation_id,operation_kind,operation_label,provider_host,model,actual_model,"
            "provider_request_id,attempt,outcome,http_status,prompt_tokens,completion_tokens,"
            "reasoning_tokens,total_tokens,reported_cost_microusd,balance_delta_microusd) "
            "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17)",
            *row,
        )


async def set_tracked_balance(
    database: SQLiteDatabase, balance_microusd: int, note: str
) -> dict[str, int | bool]:
    """Record a manual balance snapshot as an auditable adjustment."""
    async with database.acquire() as connection:
        async with connection.transaction():
            current = await connection.fetchval(
                "SELECT COALESCE(SUM(balance_delta_microusd),0) "
                "FROM ai_usage_ledger_entries WHERE balance_delta_microusd IS NOT NULL"
            )
            delta = balance_microusd - int(current or 0)
            await connection.execute(
                "INSERT INTO ai_usage_ledger_entries "
                "(id,entry_type,operation_kind,operation_label,reported_cost_microusd,"
                "balance_delta_microusd,adjustment_note) "
                "VALUES (?1,'balance_adjustment','balance','Balance adjustment',NULL,?2,?3)",
                str(uuid4()),
                delta,
                note.strip()[:160] or "Balance set manually",
            )
            known = await connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM ai_usage_ledger_entries "
                "WHERE entry_type='balance_adjustment')"
            )
    return {"balance_microusd": balance_microusd, "balance_known": bool(known)}


async def usage_ledger_snapshot(database: SQLiteDatabase, *, limit: int = 500) -> dict[str, Any]:
    """Return totals and recent entries for the administrator ledger."""
    summary = await database.fetchrow(
        "SELECT "
        "COALESCE(SUM(CASE WHEN entry_type='provider_call' THEN reported_cost_microusd ELSE 0 END),0) "
        "AS total_spend_microusd, "
        "COALESCE(SUM(CASE WHEN entry_type='provider_call' AND created_at >= "
        "strftime('%Y-%m-%dT%H:%M:%fZ','now','-30 days') "
        "THEN reported_cost_microusd ELSE 0 END),0) AS month_spend_microusd, "
        "SUM(CASE WHEN entry_type='provider_call' AND reported_cost_microusd IS NULL THEN 1 ELSE 0 END) "
        "AS unpriced_calls, "
        "SUM(CASE WHEN entry_type='provider_call' AND outcome<>'success' THEN 1 ELSE 0 END) "
        "AS unsuccessful_calls, "
        "SUM(CASE WHEN entry_type='balance_adjustment' THEN 1 ELSE 0 END) AS balance_adjustments, "
        "SUM(balance_delta_microusd) AS balance_microusd "
        "FROM ai_usage_ledger_entries"
    ) or {}
    entries = await database.fetch(
        "SELECT e.id,e.entry_type,e.operation_id,e.operation_kind,e.operation_label,"
        "e.provider_host,e.model,e.actual_model,e.attempt,e.outcome,e.http_status,e.prompt_tokens,"
        "e.completion_tokens,e.reasoning_tokens,e.total_tokens,e.reported_cost_microusd,"
        "e.balance_delta_microusd,e.adjustment_note,e.created_at,m.status AS material_status "
        "FROM ai_usage_ledger_entries e LEFT JOIN study_materials m ON m.id=e.operation_id "
        # Multiple entries can be inserted within the same millisecond; rowid gives
        # an insertion-order tie break instead of sorting random UUIDs lexically.
        "ORDER BY e.created_at DESC,e.rowid DESC LIMIT ?1",
        limit,
    )
    return {
        "total_spend_microusd": int(summary.get("total_spend_microusd") or 0),
        "month_spend_microusd": int(summary.get("month_spend_microusd") or 0),
        "unpriced_calls": int(summary.get("unpriced_calls") or 0),
        "unsuccessful_calls": int(summary.get("unsuccessful_calls") or 0),
        "balance_known": bool(summary.get("balance_adjustments") or 0),
        "balance_microusd": (
            int(summary["balance_microusd"])
            if summary.get("balance_adjustments")
            else None
        ),
        "entries": entries,
    }
