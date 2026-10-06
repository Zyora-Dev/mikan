"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ReactFlow, Background, Controls, MiniMap, Handle, Position, applyNodeChanges, applyEdgeChanges, MarkerType, type Node, type NodeProps, type Edge, type Connection as FlowConnection, type ReactFlowInstance } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { ArrowLeft, Bell, CheckCheck, CircleStop, FileInput, FolderInput, GitBranch, Plus, Redo2, Save, Settings2, ShieldCheck, Trash2, Undo2 } from "lucide-react";
import { teamRequest } from "@/lib/team-client";
import { initialWorkflow, outcomes, outcomeLabel, serializeWorkflow, stepDefaults, type Connection, type Graph, type Kind, type Person, type StepData, type Workflow } from "@/lib/workflow";
import { LoadState, PeoplePicker, useResource } from "./workflow-ui";
import shared from "@/app/admin/companies/companies.module.css";
import styles from "./workflows.module.css";

type CanvasNode = Node<StepData, Kind>;
const icons = { trigger: FileInput, approval: ShieldCheck, notify: Bell, file: FolderInput, end: CircleStop };
const kindLabel = { trigger: "Trigger", approval: "Approval", notify: "Notification", file: "File action", end: "End" };
function WorkflowNode({ type, data, selected }: NodeProps<CanvasNode>) {
  const kind = type || "approval";
  const Icon = icons[kind];
  return <div className={styles.node} data-kind={kind} data-selected={selected}>
    {kind !== "trigger" && <Handle type="target" position={Position.Top} />}
    <div className={styles.nodeTitle}><Icon size={18} /><span>{data.label || kindLabel[kind]}</span></div>
    <small>{kind === "approval" ? `${data.rule === "all" ? "Everyone" : "Anyone"} approves / ${data.reviewer_mode === "tagged" ? "Tagged people" : "Fixed reviewers"}` : kind === "end" ? outcomeLabel(data.result) : kindLabel[kind]}</small>
    {kind === "approval" ? <><div className={styles.ports}><span>Approved</span><span>Rejected</span><span>Changes</span></div>{outcomes.map((outcome, index) => <Handle key={outcome} type="source" id={outcome} position={Position.Bottom} style={{ left: `${16 + index * 34}%` }} />)}</> : kind !== "end" && <Handle type="source" id="next" position={Position.Bottom} />}
  </div>;
}
const nodeTypes = { trigger: WorkflowNode, approval: WorkflowNode, notify: WorkflowNode, file: WorkflowNode, end: WorkflowNode };
const api = "/api/company/teams/workflows";

export default function WorkflowEditor({ identifier }: { identifier: string }) {
  if (identifier === "new") return <NewEditor />;
  return <ExistingEditor identifier={identifier} />;
}

function NewEditor() {
  const [initial] = useState(initialWorkflow);
  return <Editor initial={initial} />;
}

function ExistingEditor({ identifier }: { identifier: string }) {
  const [revision, setRevision] = useState(0);
  const state = useResource<Workflow>(`${api}/${identifier}`, true, revision);
  return state.data ? <Editor key={`${identifier}-${revision}`} initial={state.data} /> : <section className={styles.section}><Link href="/company/admin/workflows" className={styles.back}><ArrowLeft size={16} />Workflows</Link><LoadState {...state} retry={() => setRevision(value => value + 1)} /></section>;
}

function Editor({ initial }: { initial: Workflow }) {
  const router = useRouter();
  const [flow, setFlow] = useState(initial);
  const [nodes, setNodes] = useState<CanvasNode[]>(() => initial.graph.nodes.map(node => ({ ...node, data: { ...stepDefaults(node.data.label), ...node.data } })));
  const [edges, setEdges] = useState<Edge[]>(initial.graph.edges);
  const [selected, setSelected] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [optionsRevision, setOptionsRevision] = useState(0);
  const options = useResource<{ teams: { id: number; name: string }[]; people: Person[]; webhooks: string[] }>(`${api}/options`, true, optionsRevision);
  const instance = useRef<ReactFlowInstance<CanvasNode, Edge> | null>(null);
  const history = useRef<{ back: Graph[]; forward: Graph[] }>({ back: [], forward: [] });
  const [historyAvailability, setHistoryAvailability] = useState({ back: false, forward: false });
  const selectedNode = nodes.find(node => node.id === selected);
  const currentGraph = (): Graph => ({ nodes: nodes.map(node => ({ id: node.id, type: node.type || "approval", position: node.position, data: node.data })), edges: edges.map(edge => ({ id: edge.id, source: edge.source, target: edge.target, sourceHandle: edge.sourceHandle as Connection["sourceHandle"] })) });

  useEffect(() => {
    if (!dirty) return;
    function guard(event: BeforeUnloadEvent) { event.preventDefault(); event.returnValue = ""; }
    function guardLink(event: MouseEvent) {
      const anchor = event.target instanceof Element ? event.target.closest("a[href]") : null;
      if (!(anchor instanceof HTMLAnchorElement) || anchor.target === "_blank" || anchor.hasAttribute("download") || event.ctrlKey || event.metaKey || event.shiftKey || anchor.href === window.location.href) return;
      if (!window.confirm("Discard unsaved workflow changes?")) { event.preventDefault(); event.stopPropagation(); }
    }
    window.addEventListener("beforeunload", guard);
    document.addEventListener("click", guardLink, true);
    return () => { window.removeEventListener("beforeunload", guard); document.removeEventListener("click", guardLink, true); };
  }, [dirty]);

  function changed() { setDirty(true); setMessage(""); }
  function checkpoint() { history.current.back.push(structuredClone(currentGraph())); if (history.current.back.length > 30) history.current.back.shift(); history.current.forward = []; setHistoryAvailability({ back: true, forward: false }); }
  function changeData(data: Partial<StepData>) { if (!selectedNode) return; checkpoint(); setNodes(previous => previous.map(node => node.id === selected ? { ...node, data: { ...node.data, ...data } } : node)); changed(); }
  function changeFlow(data: Partial<Workflow>) { setFlow(previous => ({ ...previous, ...data })); changed(); }
  function restore(direction: "back" | "forward") {
    const graph = history.current[direction].pop();
    if (!graph) return;
    history.current[direction === "back" ? "forward" : "back"].push(currentGraph());
    setNodes(graph.nodes); setEdges(graph.edges); setSelected(null); changed(); setHistoryAvailability({ back: !!history.current.back.length, forward: !!history.current.forward.length });
  }
  function connect(connection: FlowConnection) {
    if (busy || !connection.sourceHandle || connection.source === connection.target || nodes.find(node => node.id === connection.target)?.type === "trigger") return;
    checkpoint();
    setEdges(previous => [...previous.filter(edge => edge.source !== connection.source || edge.sourceHandle !== connection.sourceHandle), { id: crypto.randomUUID(), source: connection.source, target: connection.target, sourceHandle: connection.sourceHandle }]);
    changed();
  }
  function add(kind: Exclude<Kind, "trigger">) {
    if (nodes.length >= 30) { setError("A workflow can contain up to 30 steps."); return; }
    checkpoint();
    const center = instance.current?.getViewport();
    const position = center ? { x: Math.max(-9000, Math.min(9000, (140 - center.x) / center.zoom)), y: Math.max(-9000, Math.min(9000, (160 - center.y) / center.zoom)) } : { x: 200, y: 200 };
    const node: CanvasNode = { id: crypto.randomUUID(), type: kind, position, data: stepDefaults(kindLabel[kind]), selected: true };
    setNodes(previous => [...previous.map(item => ({ ...item, selected: false })), node]); setSelected(node.id); changed();
  }
  function removeSelection() {
    checkpoint();
    const identifiers = new Set(nodes.filter(node => node.type !== "trigger" && (node.selected || node.id === selected)).map(node => node.id));
    setNodes(previous => previous.filter(node => !identifiers.has(node.id)));
    setEdges(previous => previous.filter(edge => !edge.selected && !identifiers.has(edge.source) && !identifiers.has(edge.target)));
    setSelected(null); changed();
  }
  function payload() { return serializeWorkflow({ ...flow, graph: currentGraph() }); }
  async function save(validateOnly = false) {
    if (busy) return;
    if (!validateOnly && initial.enabled && !flow.enabled && !window.confirm("Pause this workflow? Pending reviewer downloads and decisions will be suspended until it is published again.")) return;
    setBusy(true); setError(""); setMessage("");
    try {
      if (validateOnly) { await teamRequest(`${api}/validate`, payload()); setMessage("Workflow is valid."); }
      else {
        const saved = await teamRequest<Workflow>(flow.id ? `${api}/${flow.id}` : api, payload());
        setFlow(saved); setDirty(false); setMessage(saved.enabled ? "Workflow published." : "Draft saved. Workflow is paused.");
        if (!flow.id) router.replace(`/company/admin/workflows/${saved.id}`);
      }
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to save workflow."); }
    finally { setBusy(false); }
  }
  const known = [...(initial.people || []), ...(options.data?.people || [])];
  const ports: Connection["sourceHandle"][] = selectedNode?.type === "approval" ? outcomes : selectedNode?.type === "end" ? [] : ["next"];

  return <section className={styles.section}>
    <Link href="/company/admin/workflows" className={styles.back}><ArrowLeft size={16} />Workflows</Link>
    <div className={styles.heading}><div><h1>{flow.name || "New workflow"}</h1><p className={styles.sub}>{dirty ? "Unsaved changes" : flow.id ? `Version ${flow.version}` : "Draft"}</p></div><div className={styles.actions}><button className={shared.secondary} disabled={busy} onClick={() => void save(true)}><CheckCheck size={16} />Validate</button><button className={shared.primary} disabled={busy} onClick={() => void save()}><Save size={16} />{busy ? "Saving..." : flow.enabled ? "Publish" : "Save draft"}</button></div></div>
    {error && <div className={shared.formError} role="alert">{error}</div>}{message && <p className={shared.notice} role="status">{message}</p>}
    <div className={styles.editor}>
      <div className={styles.toolbar}>
        {(["approval", "notify", "file", "end"] as const).map(kind => { const Icon = icons[kind]; return <button key={kind} disabled={busy || nodes.length >= 30} className={shared.secondary} onClick={() => add(kind)}><Icon size={16} /><span>{kindLabel[kind]}</span><Plus size={14} /></button>; })}
        <button className={shared.iconButton} title="Undo canvas change" aria-label="Undo canvas change" disabled={busy || !historyAvailability.back} onClick={() => restore("back")}><Undo2 size={18} /></button>
        <button className={shared.iconButton} title="Redo canvas change" aria-label="Redo canvas change" disabled={busy || !historyAvailability.forward} onClick={() => restore("forward")}><Redo2 size={18} /></button>
        <button className={shared.iconButton} title="Delete selected steps or connections" aria-label="Delete selected steps or connections" disabled={busy || (!nodes.some(node => node.selected && node.type !== "trigger") && !edges.some(edge => edge.selected))} onClick={removeSelection}><Trash2 size={18} /></button>
        <button className={shared.secondary} disabled={busy} onClick={() => setSelected(null)}><Settings2 size={16} />Workflow settings</button>
      </div>
      <div className={styles.editorGrid}>
        <div className={styles.canvas} aria-label="Workflow canvas">
          <ReactFlow<CanvasNode, Edge> nodes={nodes.map(node => ({ ...node, deletable: node.type !== "trigger" }))} edges={edges.map(edge => ({ ...edge, type: "smoothstep", markerEnd: { type: MarkerType.ArrowClosed }, label: edge.sourceHandle === "next" ? undefined : outcomeLabel(edge.sourceHandle || ""), labelStyle: { fill: "#244532", fontSize: 11 }, labelBgStyle: { fill: "#f5f7f6" } }))}
            nodeTypes={nodeTypes} onInit={value => { instance.current = value; }} fitView fitViewOptions={{ padding: 0.18 }} minZoom={0.2} maxZoom={1.6} nodeExtent={[[-9000, -9000], [9000, 9000]]}
            nodesDraggable={!busy} nodesConnectable={!busy} deleteKeyCode={busy ? null : ["Backspace", "Delete"]}
            onNodesChange={changes => { setNodes(previous => applyNodeChanges(changes, previous)); if (changes.some(change => change.type === "remove" || change.type === "position")) changed(); }}
            onEdgesChange={changes => { setEdges(previous => applyEdgeChanges(changes, previous)); if (changes.some(change => change.type === "remove")) changed(); }}
            onBeforeDelete={async () => { checkpoint(); return true; }} onNodeDragStart={checkpoint} onConnect={connect}
            onNodeClick={(_, node) => setSelected(node.id)} onSelectionChange={({ nodes: selection }) => { if (selection.length === 1) setSelected(selection[0].id); }} onPaneClick={() => setSelected(null)}>
            <Background color="#c5d1c9" gap={20} size={1} /><Controls /><MiniMap nodeColor={node => node.type === "approval" ? "#be8115" : node.type === "notify" ? "#178096" : "#087c50"} pannable zoomable style={{ width: 130, height: 90 }} />
          </ReactFlow>
        </div>
        <aside className={styles.inspector} aria-label="Workflow settings inspector"><fieldset disabled={busy} className={styles.fieldset}>
          {!selectedNode ? <><h2>Workflow settings</h2><div className={styles.fields}>
            <label className={styles.field}>Name<input maxLength={100} value={flow.name} onChange={event => changeFlow({ name: event.target.value })} /></label>
            <label className={styles.check}><input type="checkbox" checked={flow.enabled} onChange={event => changeFlow({ enabled: event.target.checked })} />Published</label>
            <fieldset className={styles.group}><legend>Who can submit</legend><div className={styles.choices}>{(["manager", "member"] as const).map(role => <label key={role} className={styles.check}><input type="checkbox" checked={flow.submitter_roles.includes(role)} onChange={event => changeFlow({ submitter_roles: event.target.checked ? [...flow.submitter_roles, role] : flow.submitter_roles.filter(value => value !== role) })} />{outcomeLabel(role)}</label>)}</div></fieldset>
            <fieldset className={styles.group}><legend>Team folders</legend>{options.error ? <><p className={shared.formError}>{options.error}</p><button className={shared.secondary} onClick={() => setOptionsRevision(value => value + 1)}>Retry</button></> : options.loading ? <p className={styles.sub}>Loading teams...</p> : <div className={styles.choices}>{options.data?.teams.length ? options.data.teams.map(team => <label className={styles.check} key={team.id}><input type="checkbox" checked={flow.team_ids.includes(team.id)} onChange={event => changeFlow({ team_ids: event.target.checked ? [...flow.team_ids, team.id] : flow.team_ids.filter(value => value !== team.id) })} />{team.name}</label>) : <p className={styles.sub}>No team folders available.</p>}</div>}</fieldset>
          </div></> : <><h2>{kindLabel[selectedNode.type || "approval"]}</h2><div className={styles.fields}>
            <label className={styles.field}>Step name<input maxLength={100} value={selectedNode.data.label} onChange={event => changeData({ label: event.target.value })} /></label>
            {selectedNode.type === "trigger" && <>
              <label className={styles.field}>Trigger<select value={selectedNode.data.trigger} onChange={event => changeData({ trigger: event.target.value as StepData["trigger"] })}><option value="manual">Manual submission</option><option value="upload">File uploaded</option><option value="schedule">Scheduled interval</option></select></label>
              {selectedNode.data.trigger === "schedule" && <label className={styles.field}>Interval (minutes)<input type="number" min={5} max={525600} step={1} value={selectedNode.data.interval_minutes} onChange={event => changeData({ interval_minutes: Number(event.target.value) })} /></label>}
              {selectedNode.data.trigger !== "manual" && <><label className={styles.field}>File suffix<input maxLength={40} placeholder="All files" value={selectedNode.data.file_suffix} onChange={event => changeData({ file_suffix: event.target.value })} /></label><label className={styles.field}>Folder filter<input maxLength={255} placeholder="All folders" value={selectedNode.data.folder} onChange={event => changeData({ folder: event.target.value })} /></label></>}
            </>}
            {selectedNode.type === "file" && <><label className={styles.field}>Action<select value={selectedNode.data.file_action} onChange={event => changeData({ file_action: event.target.value as StepData["file_action"] })}><option value="move">Move to folder</option><option value="rename">Rename file</option></select></label><label className={styles.field}>{selectedNode.data.file_action === "rename" ? "New file name" : "Destination folder"}<input maxLength={255} placeholder={selectedNode.data.file_action === "move" ? "Drive root" : "report.pdf"} value={selectedNode.data.target} onChange={event => changeData({ target: event.target.value })} /></label></>}
            {selectedNode.type === "approval" && <>
              <label className={styles.field}>Reviewers<select value={selectedNode.data.reviewer_mode} onChange={event => changeData({ reviewer_mode: event.target.value as StepData["reviewer_mode"] })}><option value="tagged">People tagged by submitter</option><option value="fixed">People chosen by admin</option></select></label>
              <label className={styles.field}>Eligible people<select value={selectedNode.data.reviewer_scope} onChange={event => changeData({ reviewer_scope: event.target.value as StepData["reviewer_scope"] })}><option value="company">Anyone in this company</option><option value="team">Same team as submitter</option><option value="selected">Only selected people</option></select></label>
              {(selectedNode.data.reviewer_mode === "fixed" || selectedNode.data.reviewer_scope === "selected") && <PeoplePicker key={`${selectedNode.id}-reviewers`} url={`${api}/options`} company selected={selectedNode.data.reviewer_ids} known={known} label="Allowed reviewers" onChange={reviewer_ids => changeData({ reviewer_ids })} />}
              <label className={styles.field}>Approval rule<select value={selectedNode.data.rule} onChange={event => changeData({ rule: event.target.value as StepData["rule"] })}><option value="all">Everyone must approve</option><option value="any">Any one person can approve</option></select></label>
              <label className={styles.check}><input type="checkbox" checked={selectedNode.data.allow_self_review} onChange={event => changeData({ allow_self_review: event.target.checked })} />Allow submitter to review</label>
            </>}
            {selectedNode.type === "notify" && <>
              <label className={styles.field}>Channel<select value={selectedNode.data.channel} onChange={event => changeData({ channel: event.target.value as StepData["channel"] })}><option value="in_app">In-app notification</option><option value="email">Email</option><option value="webhook">Webhook</option></select></label>
              {selectedNode.data.channel === "webhook" && <label className={styles.field}>Webhook destination<select value={selectedNode.data.webhook_name} onChange={event => changeData({ webhook_name: event.target.value })}><option value="">Select destination</option>{(options.data?.webhooks || []).map(name => <option key={name} value={name}>{name}</option>)}</select></label>}
              <label className={styles.field}>Notify<select value={selectedNode.data.recipients} onChange={event => changeData({ recipients: event.target.value as StepData["recipients"] })}><option value="submitter">Submitter</option><option value="reviewers">All assigned reviewers</option><option value="selected">Selected people</option></select></label>
              {selectedNode.data.recipients === "selected" && <PeoplePicker key={`${selectedNode.id}-recipients`} url={`${api}/options`} company selected={selectedNode.data.recipient_ids} known={known} label="Recipients" onChange={recipient_ids => changeData({ recipient_ids })} />}
              <label className={styles.field}>Message<textarea maxLength={500} value={selectedNode.data.message} onChange={event => changeData({ message: event.target.value })} /></label>
            </>}
            {selectedNode.type === "end" && <label className={styles.field}>Final status<select value={selectedNode.data.result} onChange={event => changeData({ result: event.target.value as StepData["result"] })}>{outcomes.map(outcome => <option key={outcome} value={outcome}>{outcomeLabel(outcome)}</option>)}</select></label>}
            {!!ports.length && <fieldset className={styles.group}><legend><GitBranch size={14} /> Connections</legend><div className={styles.fields}>{ports.map(port => <label className={styles.field} key={port}>{outcomeLabel(port)}<select value={edges.find(edge => edge.source === selectedNode.id && edge.sourceHandle === port)?.target || ""} onChange={event => { if (event.target.value) connect({ source: selectedNode.id, sourceHandle: port, target: event.target.value, targetHandle: null }); else { checkpoint(); setEdges(previous => previous.filter(edge => edge.source !== selectedNode.id || edge.sourceHandle !== port)); changed(); } }}><option value="">Not connected</option>{nodes.filter(node => node.type !== "trigger" && node.id !== selectedNode.id).map(node => <option value={node.id} key={node.id}>{node.data.label}</option>)}</select></label>)}</div></fieldset>}
            {selectedNode.type !== "trigger" && <button className={shared.secondary} onClick={removeSelection}><Trash2 size={16} />Delete step</button>}
          </div></>}
        </fieldset></aside>
      </div>
    </div>
  </section>;
}