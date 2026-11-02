/**
 * Adapter boundary. The UI only ever calls these two interfaces; the local implementations run the
 * deterministic simulations over fixtures, the remote ones call the FastAPI engine on Railway with the
 * same contracts. Swapping is a one-line change here, no UI change.
 */
import { api } from "../api";
import { decide } from "./checkEngine";
import { runCase, type ResolveRun, type RunOptions } from "./resolveEngine";
import type { Category, CheckDecision, CheckRequest, Order, Procedure, RulePack, Ticket } from "./types";

export interface CheckAdapter { decide(req: CheckRequest, ledger: { seq: number; prev_hash: string }, adapterLabel?: string): Promise<CheckDecision> | CheckDecision }
export interface ResolveAdapter { run(ticket: Ticket, opts: RunOptions): Promise<ResolveRun> | ResolveRun }

export type ResolveContext = { pack: RulePack; procedures: Procedure[]; orders: Order[]; categories: Category[] };

export class LocalCheckAdapter implements CheckAdapter {
  decide(req: CheckRequest, ledger: { seq: number; prev_hash: string }, adapterLabel?: string): CheckDecision { return decide(req, ledger.seq, ledger.prev_hash, adapterLabel); }
}
export class RemoteCheckAdapter implements CheckAdapter {
  decide(req: CheckRequest): Promise<CheckDecision> { return api<CheckDecision>("/check/decisions", { method: "POST", body: JSON.stringify(req) }); }
}
export class LocalResolveAdapter implements ResolveAdapter {
  constructor(private ctx: ResolveContext) {}
  run(ticket: Ticket, opts: RunOptions): ResolveRun { return runCase(ticket, this.ctx, opts); }
}
export class RemoteResolveAdapter implements ResolveAdapter {
  run(ticket: Ticket, opts: RunOptions): Promise<ResolveRun> { return api<ResolveRun>("/workspaces/bjs/cases/replay", { method: "POST", body: JSON.stringify({ ticket, options: opts }) }); }
}
