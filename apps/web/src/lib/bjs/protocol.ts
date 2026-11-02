/**
 * Protocol adapters. Umpyr does not define a wire protocol: a personal agent (Meta Muse first) reaches Umpyr over an
 * existing open protocol, and the adapter is the only code that knows which one. Everything after the adapter sees the
 * Umpyr ontology (Agent, Mandate, Cart, Claim). Swapping the adapter changes nothing downstream, which is the point.
 */
export type AdapterId = "pap-mcp" | "ap2-direct" | "generic";

export type ProtocolAdapter = {
  id: AdapterId;
  name: string;
  short: string;
  transport: string;
  mandate: string;
  note: string;
};

export const ADAPTERS: ProtocolAdapter[] = [
  { id: "pap-mcp", name: "Personal Agent Protocol over MCP", short: "PAP / MCP", transport: "MCP tool calls over HTTPS", mandate: "AP2-style signed mandate", note: "The path a Muse agent uses: Umpyr exposes check and resolve as tools; the agent calls them with the member's signed mandate." },
  { id: "ap2-direct", name: "AP2 mandate, direct HTTPS", short: "AP2 / HTTPS", transport: "HTTPS JSON", mandate: "AP2 checkout mandate", note: "Same mandate object, no MCP session. Nothing downstream changes." },
  { id: "generic", name: "Generic agent envelope", short: "generic", transport: "HTTPS JSON", mandate: "Any signed JSON mandate", note: "Any open protocol that carries a signed mandate and a cart maps onto the same ontology." },
];

export const adapterById = (id: string | null | undefined): ProtocolAdapter => ADAPTERS.find((a) => a.id === id) ?? ADAPTERS[0];

/** What the adapter does for one incoming message, as a short trace the pages can show. */
export function adapterTrace(adapter: ProtocolAdapter, product: "check" | "resolve"): { step: string; detail: string }[] {
  return [
    { step: "receive", detail: `${adapter.transport}: ${product === "check" ? "check_order" : "open_case"} from the personal agent, carrying the ${adapter.mandate}.` },
    { step: "verify", detail: "Platform attestation and mandate signature checked by the adapter; results travel as facts, not as trust." },
    { step: "normalise", detail: product === "check" ? "Agent, Mandate, Cart and the masked member mapped into the Umpyr ontology (umpyr-check/0.1 record)." : "Agent, claim lines and consent mapped into the Umpyr ontology (umpyr-resolve/0.2 record)." },
    { step: "hand off", detail: `${product === "check" ? "Check" : "Resolve"} runs on the normalised record. Swap the adapter and this is the first line that does not change.` },
  ];
}
