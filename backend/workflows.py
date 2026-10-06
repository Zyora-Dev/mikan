from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from database import get_db
from teams import current_team_account

AccountId = Annotated[int, Field(strict=True, gt=0, le=9007199254740991)]


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Position(StrictInput):
    x: float = Field(ge=-10000, le=10000, allow_inf_nan=False)
    y: float = Field(ge=-10000, le=10000, allow_inf_nan=False)


class StepData(StrictInput):
    label: str = Field(min_length=1, max_length=100)
    trigger: Literal["manual", "upload", "schedule"] = "manual"
    interval_minutes: int = Field(default=1440, ge=5, le=525600, strict=True)
    file_suffix: str = Field(default="", max_length=40, pattern=r"^[A-Za-z0-9._-]*$")
    folder: str = Field(default="", max_length=255)
    channel: Literal["in_app", "email", "webhook"] = "in_app"
    webhook_name: str = Field(default="", max_length=80, pattern=r"^[A-Za-z0-9_-]*$")
    file_action: Literal["rename", "move"] = "move"
    target: str = Field(default="", max_length=255)
    reviewer_mode: Literal["tagged", "fixed"] = "tagged"
    reviewer_scope: Literal["company", "team", "selected"] = "company"
    reviewer_ids: list[AccountId] = Field(default_factory=list, max_length=25)
    allow_self_review: bool = Field(default=False, strict=True)
    rule: Literal["all", "any"] = "all"
    recipients: Literal["submitter", "reviewers", "selected"] = "submitter"
    recipient_ids: list[AccountId] = Field(default_factory=list, max_length=25)
    message: str = Field(default="Workflow updated", min_length=1, max_length=500)
    result: Literal["approved", "rejected", "changes_requested"] = "approved"


class Step(StrictInput):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    type: Literal["trigger", "approval", "notify", "file", "end"]
    position: Position
    data: StepData


class Edge(StrictInput):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    source: str = Field(max_length=40)
    target: str = Field(max_length=40)
    sourceHandle: Literal["next", "approved", "rejected", "changes_requested"]


class Graph(StrictInput):
    nodes: list[Step] = Field(min_length=2, max_length=30)
    edges: list[Edge] = Field(default_factory=list, max_length=60)


class WorkflowInput(StrictInput):
    name: str = Field(min_length=1, max_length=100)
    team_ids: list[AccountId] = Field(min_length=1, max_length=100)
    submitter_roles: list[Literal["manager", "member"]] = Field(min_length=1, max_length=2)
    enabled: bool = Field(default=False, strict=True)
    version: int = Field(default=0, ge=0, strict=True)
    graph: Graph


def validate_graph(graph: Graph):
    nodes = {node.id: node for node in graph.nodes}
    if len(nodes) != len(graph.nodes) or len({edge.id for edge in graph.edges}) != len(graph.edges):
        raise ValueError("Step and connection IDs must be unique.")
    triggers = [node.id for node in graph.nodes if node.type == "trigger"]
    if len(triggers) != 1:
        raise ValueError("Use exactly one trigger.")
    automatic = nodes[triggers[0]].data.trigger != "manual"
    outgoing = {node.id: {} for node in graph.nodes}
    for edge in graph.edges:
        if edge.source not in nodes or edge.target not in nodes or nodes[edge.target].type == "trigger":
            raise ValueError("Connections must join existing steps and cannot lead into the trigger.")
        if edge.sourceHandle in outgoing[edge.source]:
            raise ValueError("Each outcome can connect to only one next step.")
        outgoing[edge.source][edge.sourceHandle] = edge.target
    for node in graph.nodes:
        required = {"approved", "rejected", "changes_requested"} if node.type == "approval" else set() if node.type == "end" else {"next"}
        if set(outgoing[node.id]) != required:
            raise ValueError(f"Connect every outcome of '{node.data.label}' using the correct ports.")
        if node.type == "approval" and (node.data.reviewer_mode == "fixed" or node.data.reviewer_scope == "selected") and not node.data.reviewer_ids:
            raise ValueError(f"Choose eligible reviewers for '{node.data.label}'.")
        if automatic and node.type == "approval" and node.data.reviewer_mode != "fixed":
            raise ValueError("Automatic triggers require fixed reviewers for every approval step.")
        if node.type == "notify" and node.data.channel == "webhook" and not node.data.webhook_name:
            raise ValueError("Choose a configured webhook destination.")
        if node.type == "file":
            validate_file_target(node.data.target, node.data.file_action)
        if node.type == "trigger" and node.data.folder:
            validate_file_target(node.data.folder, "move")
        if node.type == "notify" and node.data.channel != "webhook" and node.data.recipients == "selected" and not node.data.recipient_ids:
            raise ValueError(f"Choose notification recipients for '{node.data.label}'.")
    visited, active = set(), set()

    def visit(identifier):
        if identifier in active:
            raise ValueError("Workflow connections cannot contain cycles.")
        if identifier in visited:
            return
        active.add(identifier)
        for target in outgoing[identifier].values():
            visit(target)
        active.remove(identifier)
        visited.add(identifier)

    visit(triggers[0])
    if len(visited) != len(nodes):
        raise ValueError("Every step must be reachable from the submission trigger.")
    return triggers[0]


def validate_file_target(value, action):
    if value != value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value) or "\\" in value:
        raise ValueError("Use a clean file name or folder path.")
    if action == "rename" and (not value or "/" in value or value in {".", ".."}):
        raise ValueError("Enter a file name without path separators.")
    if action == "move" and value and any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("Use a relative folder path without empty or parent segments.")


class Submission(StrictInput):
    file_id: UUID
    request_key: UUID
    version: int | None = Field(default=None, ge=1, strict=True)
    reviewers: dict[str, list[AccountId]] = Field(default_factory=dict, max_length=30)


class Decision(StrictInput):
    outcome: Literal["approved", "rejected", "changes_requested"]
    comment: str = Field(default="", max_length=2000)


class Note(StrictInput):
    comment: str = Field(default="", max_length=2000)


FILE_SNAPSHOT_SQL = """jsonb_build_object('etag', file.etag, 'version', file.object_version,
    'size', file.size_bytes, 'connection', file.connection_id, 'key', file.object_key,
    'owner', file.owner_id, 'team', file.team_id)"""

REVIEW_ACCESS_SQL = f"""EXISTS (
    SELECT 1 FROM workflow_task task JOIN workflow_run run ON run.id=task.run_id
    JOIN workflow flow ON flow.id=run.workflow_id AND flow.company_id=run.company_id
    JOIN team_account owner ON owner.id=run.submitter_id AND owner.company_id=run.company_id
    WHERE run.file_id=file.id AND run.company_id=file.company_id AND run.status='pending'
    AND flow.enabled AND run.current_node=task.node_id AND task.status='pending'
    AND task.reviewer_id=%s AND task.reviewer_team_id=%s
    AND owner.status='active' AND owner.team_id=run.submitter_team_id
    AND run.file_snapshot={FILE_SNAPSHOT_SQL})"""


def graph_or_422(graph):
    try:
        return validate_graph(graph)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def workflow_event(connection, flow_id, run_id, kind, actor, detail, node=None):
    event = connection.execute("INSERT INTO workflow_event (workflow_id, run_id, kind, actor_name, detail, node_id) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id", (flow_id, run_id, kind, actor, detail, node)).fetchone()
    if run_id and kind in ('submitted', 'approved', 'rejected', 'changes_requested', 'cancelled', 'completed'):
        connection.execute("""INSERT INTO notification(company_id,team_id,admin_id,kind,message,run_id,event_key)
            SELECT run.company_id,run.submitter_team_id,admin.id,'workflow',
                run.workflow_name || ': ' || %s || ' - ' || run.file_name,run.id,%s
            FROM workflow_run run JOIN admin ON admin.company_id=run.company_id AND admin.role='admin' AND admin.is_active
            WHERE run.id=%s ON CONFLICT DO NOTHING""", (kind.replace('_', ' '), f"workflow-event:{event['id']}", run_id))


def notify(connection, run, node, recipients, message):
    for recipient in set(recipients):
        connection.execute("""INSERT INTO workflow_notification (run_id,node_id,recipient_id,message)
            SELECT %s,%s,id,%s FROM team_account WHERE id=%s AND company_id=%s AND status='active'
            ON CONFLICT DO NOTHING""", (run["id"], node, message, recipient, run["company_id"]))


def advance(connection, run, node_id):
    graph = Graph.model_validate(run["graph"])
    nodes = {node.id: node for node in graph.nodes}
    edges = {(edge.source, edge.sourceHandle): edge.target for edge in graph.edges}
    for _ in range(30):
        node = nodes[node_id]
        workflow_event(connection, run["workflow_id"], run["id"], "step_entered", "Workflow", node.data.label, node.id)
        if node.type == "end":
            connection.execute("UPDATE workflow_run SET status=%s,current_node=%s,updated_at=clock_timestamp() WHERE id=%s", (node.data.result, node.id, run["id"]))
            workflow_event(connection, run['workflow_id'], run['id'], 'completed', 'Workflow', node.data.result, node.id)
            notify(connection, run, node.id, [run["submitter_id"]], f"{run['workflow_name']}: {node.data.result.replace('_', ' ')}")
            return
        if node.type == "approval":
            reviewers = run["reviewers"][node.id]
            active = connection.execute("SELECT id,team_id FROM team_account WHERE id=ANY(%s) AND company_id=%s AND status='active'", (reviewers, run["company_id"])).fetchall()
            if len(active) != len(reviewers) or any(node.data.reviewer_scope == "team" and person["team_id"] != run["submitter_team_id"] for person in active):
                raise HTTPException(409, "A reviewer is no longer eligible. Cancel and resubmit with eligible reviewers.")
            for person in active:
                connection.execute("INSERT INTO workflow_task (run_id,node_id,reviewer_id,reviewer_team_id) VALUES (%s,%s,%s,%s)", (run["id"], node.id, person["id"], person["team_id"]))
            connection.execute("UPDATE workflow_run SET current_node=%s,updated_at=clock_timestamp() WHERE id=%s", (node.id, run["id"]))
            notify(connection, run, node.id, reviewers, f"Review requested: {run['file_name']} - {node.data.label}")
            return
        if node.type == "notify":
            recipients = [run["submitter_id"]] if node.data.recipients == "submitter" else node.data.recipient_ids if node.data.recipients == "selected" else [person for group in run["reviewers"].values() for person in group]
            if node.data.channel == "in_app":
                notify(connection, run, node.id, recipients, node.data.message)
            else:
                from automation import enqueue_delivery
                enqueue_delivery(connection, run, node, recipients)
                connection.execute("UPDATE workflow_run SET current_node=%s,updated_at=clock_timestamp() WHERE id=%s", (node.id, run['id']))
                return
        if node.type == "file":
            column = "name" if node.data.file_action == "rename" else "folder"
            changed = connection.execute(f"UPDATE stored_file file SET {column}=%s WHERE id=%s AND company_id=%s AND owner_id=%s AND team_id=%s AND state='ready' AND {FILE_SNAPSHOT_SQL}=%s RETURNING id", (node.data.target, run['file_id'], run['company_id'], run['submitter_id'], run['submitter_team_id'], Jsonb(run['file_snapshot']))).fetchone()
            if not changed:
                raise HTTPException(409, 'The submitted file changed; cancel and resubmit.')
            workflow_event(connection, run['workflow_id'], run['id'], 'file_' + node.data.file_action, 'Workflow', node.data.target or 'Drive root', node.id)
        node_id = edges[(node.id, "next")]
    raise HTTPException(422, "Workflow exceeds the execution limit.")


def date_clause(from_date, to_date, column="created_at"):
    if from_date and to_date and from_date > to_date:
        raise HTTPException(422, "Start date must not be after end date.")
    return (f"(%s::date IS NULL OR {column} >= %s::date::timestamp AT TIME ZONE 'UTC') AND (%s::date IS NULL OR {column} < (%s::date + INTERVAL '1 day') AT TIME ZONE 'UTC')", [from_date, from_date, to_date, to_date])


def list_rows(connection, table, columns, where, values, page, order="created_at DESC, id DESC"):
    total = connection.execute(f"SELECT count(*) AS total FROM {table} WHERE {where}", values).fetchone()["total"]
    items = connection.execute(f"SELECT {columns} FROM {table} WHERE {where} ORDER BY {order} LIMIT 10 OFFSET %s", [*values, (page - 1) * 10]).fetchall()
    return {"items": items, "total": total}


def get_flow(connection, identifier, company, lock=False):
    flow = connection.execute("SELECT * FROM workflow WHERE id=%s AND company_id=%s" + (" FOR UPDATE" if lock else ""), (identifier, company)).fetchone()
    if not flow:
        raise HTTPException(404, "Workflow not found.")
    return flow


def eligible_flow(connection, identifier, account, lock=False):
    flow = get_flow(connection, identifier, account["company_id"], lock=lock)
    if not flow["enabled"] or account["team_id"] not in flow["team_ids"] or account["role"] not in flow["submitter_roles"]:
        raise HTTPException(404, "Workflow not available.")
    return flow


def check_configuration(connection, payload, company):
    if not payload.name.strip():
        raise HTTPException(422, "Workflow name is required.")
    teams = connection.execute("SELECT id FROM team WHERE id=ANY(%s) AND company_id=%s", (payload.team_ids, company)).fetchall()
    if len(teams) != len(set(payload.team_ids)):
        raise HTTPException(422, "Choose teams belonging to this company.")
    people = {person for node in payload.graph.nodes for person in node.data.reviewer_ids + node.data.recipient_ids}
    if people:
        rows = connection.execute("SELECT id FROM team_account WHERE id=ANY(%s) AND company_id=%s AND status='active'", (list(people), company)).fetchall()
        if len(rows) != len(people):
            raise HTTPException(422, "Choose active people from this company.")
    if payload.enabled:
        graph_or_422(payload.graph)
        from automation import webhook_destinations
        destinations = webhook_destinations(company)
        for node in payload.graph.nodes:
            if node.type == 'notify' and node.data.channel == 'webhook' and node.data.webhook_name not in destinations:
                raise HTTPException(422, 'Choose a webhook configured for this company by the server administrator.')
            if node.type == 'notify' and node.data.channel == 'email' and not connection.execute('SELECT id FROM zeptomail_integration WHERE id=1 AND enabled').fetchone():
                raise HTTPException(422, 'Enable the email integration before publishing email actions.')


def start_run(connection, flow, file, account, reviewer_input, request_key):
    graph = Graph.model_validate(flow['graph'])
    start = graph_or_422(graph)
    approval_ids = {node.id for node in graph.nodes if node.type == 'approval'}
    if set(reviewer_input) - approval_ids:
        raise HTTPException(422, 'Unknown approval step.')
    reviewers = {}
    for node in graph.nodes:
        if node.type != 'approval':
            continue
        ids = list(dict.fromkeys(node.data.reviewer_ids if node.data.reviewer_mode == 'fixed' else reviewer_input.get(node.id, [])))
        if not ids or len(ids) > 25 or (not node.data.allow_self_review and account['id'] in ids):
            raise HTTPException(422, f"Tag 1-25 eligible reviewers for '{node.data.label}'; self-review must be allowed by admin.")
        people = connection.execute("SELECT id,team_id FROM team_account WHERE id=ANY(%s) AND company_id=%s AND status='active'", (ids, account['company_id'])).fetchall()
        if len(people) != len(ids) or any(node.data.reviewer_scope == 'team' and person['team_id'] != file['team_id'] for person in people) or (node.data.reviewer_scope == 'selected' and not set(ids).issubset(node.data.reviewer_ids)):
            raise HTTPException(422, "Tagged reviewers are outside the admin's allowed people or teams.")
        reviewers[node.id] = ids
    run = connection.execute('''INSERT INTO workflow_run (company_id,workflow_id,workflow_name,workflow_version,graph,file_id,file_name,file_snapshot,submitter_id,submitter_team_id,reviewers,request_key)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''', (account['company_id'], flow['id'], flow['name'], flow['version'], Jsonb(flow['graph']), file['id'], file['name'], Jsonb(file['snapshot']), account['id'], account['team_id'], Jsonb(reviewers), request_key)).fetchone()
    workflow_event(connection, flow['id'], run['id'], 'submitted', account['name'], file['name'])
    notify(connection, run, 'event:submitted', [run['submitter_id']], f"{run['workflow_name']}: submitted - {run['file_name']}")
    advance(connection, run, start)
    return {'id': run['id']}


def run_detail(connection, run):
    tasks = connection.execute("""SELECT task.id,task.node_id,task.reviewer_id,person.name AS reviewer_name,task.status,task.comment,task.decided_at
        FROM workflow_task task JOIN team_account person ON person.id=task.reviewer_id WHERE task.run_id=%s ORDER BY task.id""", (run["id"],)).fetchall()
    events = connection.execute("SELECT id,kind,node_id,actor_name,detail,created_at FROM workflow_event WHERE run_id=%s ORDER BY created_at,id", (run["id"],)).fetchall()
    return {"id": run["id"], "workflow_name": run["workflow_name"], "workflow_version": run["workflow_version"], "file_id": run["file_id"], "file_name": run["file_name"], "status": run["status"], "current_node": run["current_node"], "created_at": run["created_at"], "submitter_id": run["submitter_id"], "tasks": tasks, "events": events}


def accessible_run(connection, identifier, account, lock=False):
    run = connection.execute("""SELECT run.* FROM workflow_run run WHERE run.id=%s AND run.company_id=%s
        AND ((run.submitter_id=%s AND run.submitter_team_id=%s) OR EXISTS
        (SELECT 1 FROM workflow_task task WHERE task.run_id=run.id AND task.reviewer_id=%s AND task.reviewer_team_id=%s))""" + (" FOR UPDATE OF run" if lock else ""),
        (identifier, account["company_id"], account["id"], account["team_id"], account["id"], account["team_id"])).fetchone()
    if not run:
        raise HTTPException(404, "Request not found.")
    return run


def cancel_run(connection, run, actor, comment):
    if run["status"] != "pending":
        raise HTTPException(409, "Only pending requests can be cancelled.")
    connection.execute("UPDATE workflow_run SET status='cancelled',updated_at=clock_timestamp() WHERE id=%s", (run["id"],))
    connection.execute("UPDATE workflow_task SET status='cancelled' WHERE run_id=%s AND status='pending'", (run["id"],))
    workflow_event(connection, run["workflow_id"], run["id"], "cancelled", actor, comment or "Request cancelled")
    recipients = [run['submitter_id'], *[person for group in run['reviewers'].values() for person in group]]
    notify(connection, run, "event:cancelled", recipients, f"{run['workflow_name']}: request cancelled")
    return {"detail": "Request cancelled."}


def create_workflow_router(company_dependency, origin_dependency):
    router = APIRouter()
    mutation = [Depends(origin_dependency)]
    admin_path = "/company/teams/workflows"
    team_path = "/team/workflows"

    @router.get(admin_path)
    def list_workflows(connection=Depends(get_db, scope="function"), admin=Depends(company_dependency), search: str = Query("", max_length=100), page: int = Query(1, ge=1, le=100000), from_date: date | None = None, to_date: date | None = None):
        dates, values = date_clause(from_date, to_date)
        return list_rows(connection, "workflow", "id,name,enabled,version,team_ids,created_at,updated_at,updated_by", f"company_id=%s AND strpos(lower(name),lower(%s))>0 AND {dates}", [admin["company_id"], search, *values], page)

    @router.get(admin_path + "/options")
    def options(connection=Depends(get_db, scope="function"), admin=Depends(company_dependency), search: str = Query("", max_length=100)):
        from automation import webhook_destinations
        teams = connection.execute("SELECT id,name FROM team WHERE company_id=%s ORDER BY name LIMIT 1000", (admin["company_id"],)).fetchall()
        people = connection.execute("SELECT id,name,team_id FROM team_account WHERE company_id=%s AND status='active' AND strpos(lower(name),lower(%s))>0 ORDER BY name,id LIMIT 50", (admin["company_id"], search)).fetchall()
        return {"teams": teams, "people": people, "webhooks": list(webhook_destinations(admin['company_id']))}

    @router.post(admin_path + "/validate", dependencies=mutation)
    def validate(payload: WorkflowInput, connection=Depends(get_db, scope="function"), admin=Depends(company_dependency)):
        check_configuration(connection, payload, admin["company_id"])
        graph_or_422(payload.graph)
        return {"detail": "Workflow is valid."}

    @router.post(admin_path, status_code=201, dependencies=mutation)
    def create(payload: WorkflowInput, connection=Depends(get_db, scope="function"), admin=Depends(company_dependency)):
        check_configuration(connection, payload, admin["company_id"])
        with connection.transaction():
            flow = connection.execute("""INSERT INTO workflow (company_id,name,team_ids,submitter_roles,enabled,graph,updated_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""", (admin["company_id"], payload.name.strip(), payload.team_ids, payload.submitter_roles, payload.enabled, Jsonb(payload.graph.model_dump()), admin["name"])).fetchone()
            workflow_event(connection, flow["id"], None, "created", admin["name"], "Published" if flow["enabled"] else "Draft created")
            return flow

    @router.get(admin_path + "/runs")
    def admin_runs(connection=Depends(get_db, scope="function"), admin=Depends(company_dependency), page: int = Query(1, ge=1, le=100000), search: str = Query("", max_length=100), from_date: date | None = None, to_date: date | None = None):
        dates, values = date_clause(from_date, to_date)
        return list_rows(connection, "workflow_run", "id,workflow_name,file_name,status,created_at", f"company_id=%s AND strpos(lower(file_name || ' ' || workflow_name),lower(%s))>0 AND {dates}", [admin["company_id"], search, *values], page)

    @router.get(admin_path + "/runs/{identifier}")
    def admin_run(identifier: UUID, connection=Depends(get_db, scope="function"), admin=Depends(company_dependency)):
        run = connection.execute("SELECT * FROM workflow_run WHERE id=%s AND company_id=%s", (identifier, admin["company_id"])).fetchone()
        if not run:
            raise HTTPException(404, "Request not found.")
        return run_detail(connection, run)

    @router.post(admin_path + "/runs/{identifier}/cancel", dependencies=mutation)
    def admin_cancel(identifier: UUID, payload: Note, connection=Depends(get_db, scope="function"), admin=Depends(company_dependency)):
        with connection.transaction():
            run = connection.execute("SELECT * FROM workflow_run WHERE id=%s AND company_id=%s FOR UPDATE", (identifier, admin["company_id"])).fetchone()
            if not run:
                raise HTTPException(404, "Request not found.")
            return cancel_run(connection, run, admin["name"], payload.comment)

    @router.get(admin_path + "/{identifier}")
    def read(identifier: int, connection=Depends(get_db, scope="function"), admin=Depends(company_dependency)):
        flow = get_flow(connection, identifier, admin["company_id"])
        people = list({person for node in Graph.model_validate(flow["graph"]).nodes for person in node.data.reviewer_ids + node.data.recipient_ids})
        flow["people"] = connection.execute("SELECT id,name,team_id FROM team_account WHERE id=ANY(%s) AND company_id=%s", (people, admin["company_id"])).fetchall()
        return flow

    @router.post(admin_path + "/{identifier}", dependencies=mutation)
    def update(identifier: int, payload: WorkflowInput, connection=Depends(get_db, scope="function"), admin=Depends(company_dependency)):
        with connection.transaction():
            flow = get_flow(connection, identifier, admin["company_id"], lock=True)
            if flow["version"] != payload.version:
                raise HTTPException(409, "Workflow changed in another session. Reload before saving.")
            check_configuration(connection, payload, admin["company_id"])
            result = connection.execute("""UPDATE workflow SET name=%s,team_ids=%s,submitter_roles=%s,enabled=%s,graph=%s,updated_by=%s,version=version+1,updated_at=clock_timestamp()
                WHERE id=%s RETURNING *""", (payload.name.strip(), payload.team_ids, payload.submitter_roles, payload.enabled, Jsonb(payload.graph.model_dump()), admin["name"], identifier)).fetchone()
            workflow_event(connection, identifier, None, "updated", admin["name"], f"Version {result['version']}: {'published' if result['enabled'] else 'paused/draft'}")
            return result

    @router.get(team_path)
    def available(connection=Depends(get_db, scope="function"), account=Depends(current_team_account), page: int = Query(1, ge=1, le=100000), search: str = Query("", max_length=100), from_date: date | None = None, to_date: date | None = None):
        dates, values = date_clause(from_date, to_date)
        return list_rows(connection, "workflow", "id,name,version,created_at", f"company_id=%s AND enabled AND %s=ANY(team_ids) AND %s=ANY(submitter_roles) AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(graph->'nodes') node WHERE node->>'type'='trigger' AND COALESCE(node->'data'->>'trigger','manual')<>'manual') AND strpos(lower(name),lower(%s))>0 AND {dates}", [account["company_id"], account["team_id"], account["role"], search, *values], page)

    @router.get(team_path + "/files")
    def available_files(connection=Depends(get_db, scope="function"), account=Depends(current_team_account), search: str = Query("", max_length=100), page: int = Query(1, ge=1, le=100000)):
        return list_rows(connection, "stored_file", "id,name,size_bytes,created_at", "company_id=%s AND team_id=%s AND owner_id=%s AND state='ready' AND strpos(lower(name),lower(%s))>0 AND EXISTS (SELECT 1 FROM storage_connection WHERE storage_connection.id=stored_file.connection_id AND enabled)", [account["company_id"], account["team_id"], account["id"], search], page)

    @router.get(team_path + "/people")
    def reviewer_options(workflow_id: int, node_id: str = Query(max_length=40), search: str = Query("", max_length=100), connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        flow = eligible_flow(connection, workflow_id, account)
        node = next((node for node in Graph.model_validate(flow["graph"]).nodes if node.id == node_id and node.type == "approval"), None)
        if not node:
            raise HTTPException(404, "Approval step not found.")
        conditions, values = ["company_id=%s", "status='active'", "strpos(lower(name),lower(%s))>0"], [account["company_id"], search]
        if not node.data.allow_self_review:
            conditions.append("id<>%s"); values.append(account["id"])
        if node.data.reviewer_scope == "team":
            conditions.append("team_id=%s"); values.append(account["team_id"])
        if node.data.reviewer_scope == "selected" or node.data.reviewer_mode == "fixed":
            conditions.append("id=ANY(%s)"); values.append(node.data.reviewer_ids)
        return {"items": connection.execute(f"SELECT id,name,team_id FROM team_account WHERE {' AND '.join(conditions)} ORDER BY name,id LIMIT 50", values).fetchall()}

    @router.get(team_path + "/runs")
    def my_runs(connection=Depends(get_db, scope="function"), account=Depends(current_team_account), page: int = Query(1, ge=1, le=100000), view: Literal["mine", "inbox"] = "mine", search: str = Query("", max_length=100), from_date: date | None = None, to_date: date | None = None):
        dates, values = date_clause(from_date, to_date, "run.created_at")
        ownership = "run.submitter_id=%s AND run.submitter_team_id=%s" if view == "mine" else "EXISTS (SELECT 1 FROM workflow_task task WHERE task.run_id=run.id AND task.reviewer_id=%s AND task.reviewer_team_id=%s AND task.status='pending') AND run.status='pending'"
        return list_rows(connection, "workflow_run run", "run.id,run.workflow_name,run.file_name,run.status,run.created_at", f"run.company_id=%s AND ({ownership}) AND strpos(lower(run.file_name || ' ' || run.workflow_name),lower(%s))>0 AND {dates}", [account["company_id"], account["id"], account["team_id"], search, *values], page, "run.created_at DESC,run.id DESC")

    @router.get(team_path + "/notifications")
    def notifications(connection=Depends(get_db, scope="function"), account=Depends(current_team_account), page: int = Query(1, ge=1, le=100000), from_date: date | None = None, to_date: date | None = None, search: str = Query("", max_length=100)):
        dates, values = date_clause(from_date, to_date, "notice.created_at")
        return list_rows(connection, "workflow_notification notice JOIN workflow_run run ON run.id=notice.run_id", "notice.id,notice.run_id,notice.message,notice.read_at,notice.created_at", f"notice.recipient_id=%s AND run.company_id=%s AND strpos(lower(notice.message),lower(%s))>0 AND {dates}", [account["id"], account["company_id"], search, *values], page, "notice.created_at DESC,notice.id DESC")

    @router.post(team_path + "/notifications/{identifier}/read", dependencies=mutation)
    def mark_read(identifier: int, payload: Note, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        row = connection.execute("UPDATE workflow_notification SET read_at=COALESCE(read_at,clock_timestamp()) WHERE id=%s AND recipient_id=%s AND EXISTS (SELECT 1 FROM workflow_run WHERE workflow_run.id=workflow_notification.run_id AND company_id=%s) RETURNING id", (identifier, account["id"], account["company_id"])).fetchone()
        if not row:
            raise HTTPException(404, "Notification not found.")
        return {"detail": "Notification marked read."}

    @router.get(team_path + "/runs/{identifier}")
    def my_run(identifier: UUID, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        run = accessible_run(connection, identifier, account)
        result = run_detail(connection, run)
        result["enabled"] = get_flow(connection, run["workflow_id"], account["company_id"])["enabled"]
        return result

    @router.post(team_path + "/runs/{identifier}/cancel", dependencies=mutation)
    def owner_cancel(identifier: UUID, payload: Note, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        with connection.transaction():
            run = accessible_run(connection, identifier, account, lock=True)
            if run["submitter_id"] != account["id"]:
                raise HTTPException(403, "Only the submitter or company admin can cancel this request.")
            return cancel_run(connection, run, account["name"], payload.comment)

    @router.post(team_path + "/runs/{identifier}/decide", dependencies=mutation)
    def decide(identifier: UUID, payload: Decision, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        if payload.outcome != "approved" and not payload.comment.strip():
            raise HTTPException(422, "Add a reason for rejection or requested changes.")
        with connection.transaction():
            run = accessible_run(connection, identifier, account, lock=True)
            flow = get_flow(connection, run["workflow_id"], account["company_id"], lock=True)
            if run["status"] != "pending" or not flow["enabled"]:
                raise HTTPException(409, "Request is closed or the workflow is paused.")
            task = connection.execute("SELECT * FROM workflow_task WHERE run_id=%s AND node_id=%s AND reviewer_id=%s AND reviewer_team_id=%s AND status='pending'", (identifier, run["current_node"], account["id"], account["team_id"])).fetchone()
            if not task:
                raise HTTPException(403, "No pending review is assigned to you.")
            file = connection.execute(f"SELECT file.id FROM stored_file file WHERE file.id=%s AND file.company_id=%s AND file.state='ready' AND {FILE_SNAPSHOT_SQL}=%s FOR SHARE", (run["file_id"], run["company_id"], Jsonb(run["file_snapshot"]))).fetchone()
            owner = connection.execute("SELECT id FROM team_account WHERE id=%s AND team_id=%s AND company_id=%s AND status='active'", (run["submitter_id"], run["submitter_team_id"], run["company_id"])).fetchone()
            if not file or not owner:
                raise HTTPException(409, "The submitted file or its owner changed. Cancel and resubmit.")
            node = next(node for node in Graph.model_validate(run["graph"]).nodes if node.id == run["current_node"])
            connection.execute("UPDATE workflow_task SET status=%s,comment=%s,decided_at=clock_timestamp() WHERE id=%s", (payload.outcome, payload.comment.strip(), task["id"]))
            workflow_event(connection, run["workflow_id"], identifier, payload.outcome, account["name"], payload.comment.strip() or "Approved", node.id)
            notify(connection, run, f"event:decision:{task['id']}", [run['submitter_id']], f"{account['name']} {payload.outcome.replace('_', ' ')}: {run['file_name']} - {node.data.label}")
            pending = connection.execute("SELECT count(*) AS total FROM workflow_task WHERE run_id=%s AND node_id=%s AND status='pending'", (identifier, node.id)).fetchone()["total"]
            if payload.outcome != "approved" or node.data.rule == "any" or pending == 0:
                connection.execute("UPDATE workflow_task SET status='cancelled' WHERE run_id=%s AND node_id=%s AND status='pending'", (identifier, node.id))
                target = next(edge.target for edge in Graph.model_validate(run["graph"]).edges if edge.source == node.id and edge.sourceHandle == payload.outcome)
                advance(connection, run, target)
            return {"detail": "Decision recorded."}

    @router.get(team_path + "/{identifier}")
    def available_workflow(identifier: int, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        flow = eligible_flow(connection, identifier, account)
        return {"id": flow["id"], "name": flow["name"], "version": flow["version"], "steps": [node.model_dump() for node in Graph.model_validate(flow["graph"]).nodes if node.type == "approval"]}

    @router.post(team_path + "/{identifier}/submit", status_code=201, dependencies=mutation)
    def submit(identifier: int, payload: Submission, connection=Depends(get_db, scope="function"), account=Depends(current_team_account)):
        with connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"workflow:{account['company_id']}:{account['id']}:{payload.request_key}",))
            flow = eligible_flow(connection, identifier, account, lock=True)
            file = connection.execute(f"""SELECT file.*, {FILE_SNAPSHOT_SQL} AS snapshot FROM stored_file file
                JOIN storage_connection storage ON storage.id=file.connection_id WHERE file.id=%s AND file.company_id=%s
                AND file.team_id=%s AND file.owner_id=%s AND file.state='ready' AND storage.enabled FOR UPDATE OF file""", (payload.file_id, account["company_id"], account["team_id"], account["id"])).fetchone()
            if not file:
                raise HTTPException(404, "Choose a ready file from your own drive.")
            existing = connection.execute("SELECT id,workflow_id,file_id FROM workflow_run WHERE company_id=%s AND submitter_id=%s AND request_key=%s", (account["company_id"], account["id"], payload.request_key)).fetchone()
            if existing:
                if existing["workflow_id"] != identifier or existing["file_id"] != payload.file_id:
                    raise HTTPException(409, "Submission key already used.")
                return {"id": existing["id"]}
            if payload.version is not None and payload.version != flow["version"]:
                raise HTTPException(409, "Workflow changed. Reload the submission page before submitting.")
            if connection.execute("SELECT id FROM workflow_run WHERE workflow_id=%s AND file_id=%s AND status='pending'", (identifier, payload.file_id)).fetchone():
                raise HTTPException(409, "This file already has a pending request for this workflow.")
            if any(node.type == 'trigger' and node.data.trigger != 'manual' for node in Graph.model_validate(flow['graph']).nodes):
                raise HTTPException(409, 'This workflow starts automatically.')
            return start_run(connection, flow, file, account, payload.reviewers, payload.request_key)

    return router