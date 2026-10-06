import asyncio
from datetime import date, datetime, timezone
from time import monotonic
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from database import connect, connect_async, get_db
from workflows import date_clause


class ReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    read: bool = Field(strict=True)


class ReadAllInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    before: datetime


def stream_account(request, dependency):
    with connect() as connection:
        return dependency(request, Response(), connection)


async def notification_events(request, dependency, scope, account):
    recipient = f"{'team' if scope == 'team' else 'admin'}:{account['id']}"
    identity = tuple(account.get(key) for key in ('id', 'company_id', 'team_id', 'role'))
    try:
        async with await connect_async() as listener:
            await listener.execute('LISTEN mikan_notifications')
            yield 'retry: 1000\nevent: changed\ndata: {}\n\n'
            deadline = monotonic() + 120
            while monotonic() < deadline and not await request.is_disconnected():
                changed = False
                async for notice in listener.notifies(timeout=15, stop_after=1):
                    changed = notice.payload == recipient
                try:
                    current = await asyncio.to_thread(stream_account, request, dependency)
                except HTTPException:
                    yield 'event: expired\ndata: {}\n\n'
                    return
                if tuple(current.get(key) for key in ('id', 'company_id', 'team_id', 'role')) != identity:
                    yield 'event: expired\ndata: {}\n\n'
                    return
                yield 'event: changed\ndata: {}\n\n' if changed else ': heartbeat\n\n'
    except Exception:
        yield 'retry: 5000\nevent: unavailable\ndata: {}\n\n'


def create_notification_router(dependency, origin_dependency, scope):
    prefix = {"super": "/admin", "company": "/company", "team": "/team"}[scope]
    router = APIRouter(prefix=prefix + "/notifications")
    mutation = [Depends(origin_dependency)]

    def sources(account):
        if scope == "super":
            where, values = "admin_id=%s", [account["id"]]
        elif scope == "company":
            where, values = "admin_id=%s AND company_id=%s", [account["id"], account["company_id"]]
        else:
            where, values = "account_id=%s AND company_id=%s AND team_id=%s", [account["id"], account["company_id"], account["team_id"]]
        result = [("storage", "notification", where, values)]
        if scope == "team":
            result.append(("workflow", "workflow_notification", "recipient_id=%s AND EXISTS (SELECT 1 FROM workflow_run WHERE workflow_run.id=workflow_notification.run_id AND company_id=%s)", [account["id"], account["company_id"]]))
        return result

    def dataset(account):
        queries, values = [], []
        for source, table, where, parameters in sources(account):
            fields = "kind, run_id" if source == "storage" else "'workflow' AS kind, run_id"
            queries.append(f"SELECT '{source}:' || id AS id, message, read_at, created_at, {fields} FROM {table} WHERE {where}")
            values.extend(parameters)
        return " UNION ALL ".join(queries), values

    @router.get('/stream')
    async def stream(request: Request):
        account = await asyncio.to_thread(stream_account, request, dependency)
        return StreamingResponse(notification_events(request, dependency, scope, account), media_type='text/event-stream',
                                 headers={'Cache-Control': 'private, no-store', 'X-Accel-Buffering': 'no', 'Vary': 'Cookie'})

    @router.get("/unread")
    def unread(connection=Depends(get_db, scope="function"), account=Depends(dependency)):
        query, values = dataset(account)
        return connection.execute(f"SELECT count(*) AS unread FROM ({query}) notice WHERE read_at IS NULL", values).fetchone()

    @router.get("")
    def inbox(connection=Depends(get_db, scope="function"), account=Depends(dependency),
              page: int = Query(1, ge=1, le=100000), search: str = Query("", max_length=100),
              view: Literal["all", "unread", "read"] = "all", kind: Literal["all", "storage", "workflow", "automation_failure"] = "all",
              from_date: date | None = None, to_date: date | None = None):
        query, values = dataset(account)
        dates, date_values = date_clause(from_date, to_date)
        conditions = ["strpos(lower(message),lower(%s))>0", dates]
        values.extend([search, *date_values])
        if view != "all":
            conditions.append("read_at IS " + ("NULL" if view == "unread" else "NOT NULL"))
        if kind != "all":
            conditions.append("kind IN ('storage_warning','storage_critical','storage_full')" if kind == 'storage' else "kind = %s")
            if kind != 'storage':
                values.append(kind)
        where = " AND ".join(conditions)
        before = connection.execute("SELECT clock_timestamp() AS value").fetchone()["value"]
        total = connection.execute(f"SELECT count(*) AS total FROM ({query}) notice WHERE {where}", values).fetchone()["total"]
        items = connection.execute(f"SELECT * FROM ({query}) notice WHERE {where} ORDER BY created_at DESC,id DESC LIMIT 10 OFFSET %s", [*values, (page - 1) * 10]).fetchall()
        return {"items": items, "total": total, "before": before, **unread(connection, account)}

    @router.post("/read-all", dependencies=mutation)
    def read_all(payload: ReadAllInput, connection=Depends(get_db, scope="function"), account=Depends(dependency)):
        if payload.before.tzinfo is None or payload.before > datetime.now(timezone.utc):
            raise HTTPException(422, "Choose a valid notification snapshot.")
        with connection.transaction():
            for source, table, where, values in sources(account):
                connection.execute(f"UPDATE {table} SET read_at=clock_timestamp() WHERE {where} AND read_at IS NULL AND created_at<=%s", [*values, payload.before])
        return {"detail": "Notifications marked read."}

    @router.post("/{identifier}/read", dependencies=mutation)
    def mark_read(payload: ReadInput, identifier: str = Path(pattern=r"^(storage|workflow):[1-9][0-9]{0,18}$"), connection=Depends(get_db, scope="function"), account=Depends(dependency)):
        source_name, number = identifier.split(":")
        if int(number) > 9223372036854775807:
            raise HTTPException(404, "Notification not found.")
        for source, table, where, values in sources(account):
            if source == source_name:
                row = connection.execute(f"UPDATE {table} SET read_at=CASE WHEN %s THEN COALESCE(read_at,clock_timestamp()) ELSE NULL END WHERE id=%s AND {where} RETURNING id", [payload.read, int(number), *values]).fetchone()
                if row:
                    return {"detail": "Notification marked read." if payload.read else "Notification marked unread."}
        raise HTTPException(404, "Notification not found.")

    return router