from __future__ import annotations
import json
from typing import Any, Optional

import asyncpg

from ..discovery.schema import CondensedSchema
from ..config import settings

_DDL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS schemas (
    id          SERIAL PRIMARY KEY,
    service_name TEXT NOT NULL,
    source_url   TEXT NOT NULL UNIQUE,
    schema_json  JSONB NOT NULL,
    tool_names   TEXT[] NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_schemas_service ON schemas(service_name);
"""


class SchemaRegistry:
    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(settings.database_url)
        async with self._pool.acquire() as conn:
            await conn.execute(_DDL)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    async def save(self, schema: CondensedSchema) -> int:
        tool_names = [t.name for t in schema.tools]
        data = json.loads(schema.model_dump_json())
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO schemas (service_name, source_url, schema_json, tool_names)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (source_url) DO UPDATE
                  SET service_name = EXCLUDED.service_name,
                      schema_json  = EXCLUDED.schema_json,
                      tool_names   = EXCLUDED.tool_names,
                      updated_at   = NOW()
                RETURNING id
                """,
                schema.service_name,
                schema.source_url or schema.base_url,
                json.dumps(data),
                tool_names,
            )
            return row["id"]

    async def get_by_id(self, schema_id: int) -> Optional[CondensedSchema]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT schema_json FROM schemas WHERE id = $1", schema_id)
        return CondensedSchema(**json.loads(row["schema_json"])) if row else None

    async def get_by_url(self, source_url: str) -> Optional[CondensedSchema]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT schema_json FROM schemas WHERE source_url = $1", source_url
            )
        return CondensedSchema(**json.loads(row["schema_json"])) if row else None

    async def list_all(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, service_name, source_url, tool_names, created_at, updated_at "
                "FROM schemas ORDER BY updated_at DESC"
            )
        return [dict(r) for r in rows]

    async def delete(self, schema_id: int) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute("DELETE FROM schemas WHERE id = $1", schema_id)
        return result == "DELETE 1"
