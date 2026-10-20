export const API_BASE: string = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";
// Public demo build: only the two sandbox merchants are exposed; /try, onboarding and setup are compiled out.
export const PUBLIC_DEMO: boolean = String(import.meta.env.VITE_PUBLIC_DEMO ?? "").toLowerCase() === "true";

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown, message?: string) {
    super(message ?? `API error ${status}`);
    this.status = status;
    this.body = body;
  }
  get isBudgetExceeded(): boolean {
    return this.status === 402;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) throw new ApiError(res.status, body, body?.message);
  return body as T;
}

export type Health = {
  status: string;
  env: string;
  db: boolean;
  llm_provider: string;
  model: string;
  daily_cost_cap_usd: number;
};

export type Workspace = {
  id: string;
  name: string;
  kind: string;
  status: string;
  settings: Record<string, unknown>;
  is_synthetic: boolean;
};

export type Spend = {
  day: string;
  cap_usd: number;
  spent_usd: number;
  remaining_usd: number;
  blocked: boolean;
  calls: number;
  by_step: { step: string; calls: number; input_tokens: number; output_tokens: number; cost_usd: number }[];
};

export type JobEvent = { seq: number; step: string; level: string; message: string; data: Record<string, unknown>; progress: number | null };

/** Subscribe to a job's server-sent events. Returns an unsubscribe function. */
export function subscribeJob(jobId: string, onEvent: (e: JobEvent) => void, onEnd: (job: unknown) => void): () => void {
  const es = new EventSource(`${API_BASE}/jobs/${jobId}/events`);
  es.addEventListener("step", (ev) => onEvent(JSON.parse((ev as MessageEvent).data)));
  es.addEventListener("end", (ev) => {
    onEnd(JSON.parse((ev as MessageEvent).data));
    es.close();
  });
  es.onerror = () => es.close();
  return () => es.close();
}
