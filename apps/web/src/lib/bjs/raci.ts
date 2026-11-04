import { anonymiseDeep } from "../brand";
import type { Party, Raci } from "./types";

export const PARTY_LABEL: Record<Party, string> = anonymiseDeep({
  customer: "Member",
  customer_agent: "Member's agent",
  agent_platform: "Agent platform",
  umpyr: "Umpyr",
  merchant: "BJ's (system)",
  merchant_staff: "BJ's staff",
  third_party: "Third party",
});

export const PARTY_ORDER: Party[] = ["customer", "customer_agent", "agent_platform", "umpyr", "merchant", "merchant_staff", "third_party"];

export const RACI_LEGEND: Record<"R" | "A" | "C" | "I", string> = { R: "Responsible: doing it", A: "Accountable: decides", C: "Consulted: asked", I: "Informed: told" };

type Ctx = { channel: "email" | "agent"; hasPartner: boolean; needsStaff: boolean; agentFault: boolean };

/** RACI per Resolve pipeline step. Same letters as the Rockler / Alaska ops narrative; parties vary by channel and by who is at fault. */
export function raciFor(step: string, c: Ctx): Raci {
  const who: Party = c.channel === "agent" ? "customer_agent" : "customer";
  switch (step) {
    case "intake": return c.channel === "agent" ? { customer_agent: "R", agent_platform: "C", umpyr: "A", customer: "I", merchant: "I" } : { customer: "R", umpyr: "A", merchant: "I" };
    case "order_match": return { umpyr: "R", merchant: "C", [who]: "I" };
    case "classification": return { umpyr: "R", merchant_staff: "I" };
    case "facts": return { umpyr: "R", [who]: "C", merchant: "C" };
    case "rule": return { umpyr: "R", merchant: "A", [who]: "I" };
    case "fault": return { umpyr: "R", merchant: "A", ...(c.hasPartner ? { third_party: "C" as const } : {}), ...(c.agentFault ? { agent_platform: "I" as const, customer_agent: "I" as const } : {}) };
    case "resolution": return { umpyr: "R", merchant: "A", [who]: "C" };
    case "pdp": return { umpyr: "R", merchant: "A", ...(c.needsStaff ? { merchant_staff: "C" as const } : {}) };
    case "terms": return c.channel === "agent" ? { customer_agent: "R", customer: "A", umpyr: "C", merchant: "I" } : { customer: "A", umpyr: "R", merchant: "I" };
    case "execute": return { merchant: "R", umpyr: "C", [who]: "I", ...(c.hasPartner ? { third_party: "I" as const } : {}) };
    case "audit": return { umpyr: "R", merchant: "I", [who]: "I" };
    case "close": return { merchant: "R", merchant_staff: "A", umpyr: "C", ...(c.hasPartner ? { third_party: "C" as const } : {}) };
    default: return { umpyr: "R" };
  }
}
