export type Outcome = "approved" | "rejected" | "changes_requested";
export type Kind = "trigger" | "approval" | "notify" | "file" | "end";
export type StepData = {
  trigger: "manual" | "upload" | "schedule"; interval_minutes: number; file_suffix: string; folder: string;
  channel: "in_app" | "email" | "webhook"; webhook_name: string; file_action: "rename" | "move"; target: string;
  label: string; reviewer_mode: "tagged" | "fixed"; reviewer_scope: "company" | "team" | "selected";
  reviewer_ids: number[]; allow_self_review: boolean; rule: "all" | "any";
  recipients: "submitter" | "reviewers" | "selected"; recipient_ids: number[]; message: string; result: Outcome;
};
export type Step = { id: string; type: Kind; position: { x: number; y: number }; data: StepData };
export type Connection = { id: string; source: string; target: string; sourceHandle: "next" | Outcome };
export type Graph = { nodes: Step[]; edges: Connection[] };
export type Person = { id: number; name: string; team_id: number };
export type Workflow = { id?: number; name: string; version: number; enabled: boolean; team_ids: number[]; submitter_roles: ("manager" | "member")[]; graph: Graph; people?: Person[] };
export const outcomes: Outcome[] = ["approved", "rejected", "changes_requested"];
export const outcomeLabel = (value: string) => value.replaceAll("_", " ").replace(/^./, letter => letter.toUpperCase());
export const stepDefaults = (label: string): StepData => ({ label, trigger: "manual", interval_minutes: 1440, file_suffix: "", folder: "", channel: "in_app", webhook_name: "", file_action: "move", target: "", reviewer_mode: "tagged", reviewer_scope: "company", reviewer_ids: [], allow_self_review: false, rule: "all", recipients: "submitter", recipient_ids: [], message: "Workflow updated", result: "approved" });
export function initialWorkflow(): Workflow {
  const nodes: Step[] = [
    { id: "submit", type: "trigger", position: { x: 260, y: 0 }, data: stepDefaults("File submitted") },
    { id: "review", type: "approval", position: { x: 260, y: 180 }, data: stepDefaults("File review") },
    { id: "notice", type: "notify", position: { x: 0, y: 380 }, data: { ...stepDefaults("Notify submitter"), message: "Your file has been approved." } },
    ...outcomes.map((result, index): Step => ({ id: result, type: "end", position: { x: index * 280, y: index === 0 ? 560 : 430 }, data: { ...stepDefaults(outcomeLabel(result)), result } })),
  ];
  return { name: "", version: 0, enabled: false, team_ids: [], submitter_roles: ["manager", "member"], graph: { nodes, edges: [
    { id: "submit-review", source: "submit", target: "review", sourceHandle: "next" },
    ...outcomes.map((outcome): Connection => ({ id: `review-${outcome}`, source: "review", target: outcome === "approved" ? "notice" : outcome, sourceHandle: outcome })),
    { id: "notice-approved", source: "notice", target: "approved", sourceHandle: "next" },
  ] } };
}

export function serializeWorkflow(workflow: Workflow): Omit<Workflow, "id" | "people"> {
  return { name: workflow.name, version: workflow.version, enabled: workflow.enabled, team_ids: workflow.team_ids, submitter_roles: workflow.submitter_roles,
    graph: { nodes: workflow.graph.nodes.map(({ id, type, position, data }) => ({ id, type, position, data })), edges: workflow.graph.edges.map(({ id, source, target, sourceHandle }) => ({ id, source, target, sourceHandle })) } };
}