/**
 * Illustrative impact metrics for the strip at the top of every Resolve demo.
 * None of these are measured: they are targets for a pilot at each merchant's scale and the page says so.
 * Definitions are shown behind the "?" on each tile.
 */
export type Metric = { key: string; label: string; value: string; vs: string; help: string };

const DEFS: Record<string, Omit<Metric, "value" | "vs">> = {
  auto_resolved: {
    key: "auto_resolved",
    label: "tickets auto-resolved today",
    help: "Share of today's tickets Umpyr closed with no person involved: a compiled rule fired, the policy decision point allowed the action, the customer confirmed the terms and the merchant executed. Cases that needed a missing fact, a staff approval or a specialist are not counted.",
  },
  median_ttr: {
    key: "median_ttr",
    label: "median time to resolution",
    help: "Time from the customer's first message to the executed remedy (or the explained denial), taken at the midpoint of today's cases. The comparison is the merchant's help-desk median before Umpyr.",
  },
  cost_per_ticket: {
    key: "cost_per_ticket",
    label: "cost per resolved ticket",
    help: "Model and platform cost of a case Umpyr closed, averaged over today's resolved tickets. The comparison is the fully loaded cost of a human-handled contact.",
  },
  policy_cited: {
    key: "policy_cited",
    label: "decisions citing policy",
    help: "Share of rulings that name the compiled policy clause that decided them, with the source document, section and link. It is 100% by construction: no decision ships without a fired rule, and every rule carries its clause.",
  },
};

const VALUES: Record<string, Record<string, { value: string; vs: string }>> = {
  rockler: {
    auto_resolved: { value: "68%", vs: "vs 0% today" },
    median_ttr: { value: "2 min 55 s", vs: "vs ~19 h help desk" },
    cost_per_ticket: { value: "$0.38", vs: "vs ~$5.90 per contact" },
    policy_cited: { value: "100%", vs: "every ruling links its clause" },
  },
  alaska: {
    auto_resolved: { value: "64%", vs: "vs 0% today" },
    median_ttr: { value: "4 min 10 s", vs: "vs ~31 h help desk" },
    cost_per_ticket: { value: "$0.47", vs: "vs ~$7.40 per contact" },
    policy_cited: { value: "100%", vs: "contract of carriage and DOT rules" },
  },
  bjs: {
    auto_resolved: { value: "71%", vs: "vs 0% today" },
    median_ttr: { value: "3 min 40 s", vs: "vs ~26 h help desk" },
    cost_per_ticket: { value: "$0.42", vs: "vs ~$6.80 per contact" },
    policy_cited: { value: "100%", vs: "every ruling links its clause" },
  },
};

export const METRIC_ORDER = ["auto_resolved", "median_ttr", "cost_per_ticket", "policy_cited"];

export function metricsFor(workspace: string): Metric[] {
  const v = VALUES[workspace] ?? VALUES.rockler;
  return METRIC_ORDER.map((k) => ({ ...DEFS[k], ...v[k] }));
}
