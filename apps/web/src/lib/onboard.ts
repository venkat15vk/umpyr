import { API_BASE, api, subscribeJob, type JobEvent } from "./api";

export type Upload = { id: string; kind: "tickets" | "policy" | "orders"; filename: string; size_bytes: number; status: string; row_count: number | null; mapping: Mapping | null };
export type Mapping = { kind: string; columns: string[]; mapping: Record<string, { source: string | null; confidence: number; note: string }>; thread_format: string; notes: string; status: string; samples?: Record<string, string | null>[] };
export type Category = { key: string; name: string; description: string | null; volume: number; avg_hours_to_resolve: number | null; pain_score: number; status: string };
export type Procedure = { category_key: string; version: number; name: string; status: string; steps: { step_id: string; name: string; kind: string; instruction: string; required_facts: string[]; clause_ids: string[]; tool: string }[]; outcomes: { condition: string; decision: string; remedy: string; clause_ids: string[] }[]; mining: { common_path?: string[]; cases?: number } };
export type RuleRow = { rule_id: string; clause_id: string; tier: string; priority: number; claim_reasons: string[]; requires: string[]; when: unknown; effect: { decision: string; remedy: string | null; amount: unknown; fault: string | null; note: string | null }; description: string; enabled: boolean };
export type Clause = { clause_id: string; title: string; text: string; source_url: string; tier: string; ambiguous: boolean };
export type Drift = { id: string; kind: string; category_key: string | null; rule_id: string | null; clause_id: string | null; staff_outcome: Record<string, unknown>; policy_outcome: Record<string, unknown>; pattern: string; support: number; status: string };

export const createWorkspace = (name: string) => api<{ id: string; name: string; retention_days: number }>("/workspaces", { method: "POST", body: JSON.stringify({ name }) });
export const listUploads = (ws: string) => api<Upload[]>(`/workspaces/${ws}/uploads`);
export async function uploadFile(ws: string, kind: string, file: File): Promise<Upload> {
  const fd = new FormData();
  fd.append("kind", kind);
  fd.append("file", file);
  const res = await fetch(`${API_BASE}/workspaces/${ws}/uploads`, { method: "POST", body: fd });
  const body = await res.json();
  if (!res.ok) throw new Error(body?.detail ?? body?.message ?? `upload failed (${res.status})`);
  return body as Upload;
}
export const addPolicyUrl = (ws: string, url: string, tier: string) => api<{ documents: { title: string; url: string; chars: number }[] }>(`/workspaces/${ws}/policy-urls`, { method: "POST", body: JSON.stringify({ url, tier }) });
export const loadSample = (ws: string, client: string) => api<{ uploads: Upload[] }>(`/workspaces/${ws}/uploads/sample?client=${client}`, { method: "POST" });
export const approveMapping = (ws: string, uploadId: string, mapping: Record<string, string | null>, thread_format?: string) => api<Upload>(`/workspaces/${ws}/uploads/${uploadId}/mapping`, { method: "PATCH", body: JSON.stringify({ mapping, thread_format, approve: true }) });
export const startOnboarding = (ws: string) => api<{ job_id: string }>(`/workspaces/${ws}/onboard`, { method: "POST" });
export const policyDocs = (ws: string) => api<{ title: string; source_url: string | null; source_type: string; tier: string; chars: number }[]>(`/workspaces/${ws}/policy-documents`);
export const categories = (ws: string) => api<Category[]>(`/workspaces/${ws}/categories`);
export const patchCategory = (ws: string, key: string, body: Record<string, unknown>) => api(`/workspaces/${ws}/categories/${key}`, { method: "PATCH", body: JSON.stringify(body) });
export const procedures = (ws: string) => api<Procedure[]>(`/workspaces/${ws}/procedures`);
export const patchProcedure = (ws: string, key: string, status: string) => api(`/workspaces/${ws}/procedures/${key}`, { method: "PATCH", body: JSON.stringify({ status }) });
export const rules = (ws: string) => api<{ version: string | null; clauses: Clause[]; rules: RuleRow[]; conflicts: string[] }>(`/workspaces/${ws}/rules`);
export const patchRule = (ws: string, id: string, enabled: boolean) => api(`/workspaces/${ws}/rules/${id}`, { method: "PATCH", body: JSON.stringify({ enabled }) });
export const drift = (ws: string) => api<Drift[]>(`/workspaces/${ws}/drift`);
export const setDrift = (ws: string, id: string, status: string) => api(`/workspaces/${ws}/drift/${id}?status=${status}`, { method: "POST" });
export const readiness = (ws: string) => api<{ ready: boolean; reasons: string[]; status: string; expires_at: string | null }>(`/workspaces/${ws}/readiness`);
export const summary = (ws: string) => api<{ workspace: { id: string; name: string; kind: string; status: string }; tickets: number; cases: number; categories: number; procedures: number; clauses: number; rule_pack: { version: string; rules: number } | null }>(`/workspaces/${ws}/summary`);
export { subscribeJob };
export type { JobEvent };
