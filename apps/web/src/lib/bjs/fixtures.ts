/**
 * The BJ's fixtures as the pages and the engines see them: loaded once and passed through the brand rewrite, so
 * the anonymous build hashes, cites and displays the same stand-in names everywhere (ids included).
 */
import agentCasesJson from "../../../../../samples/bjs/agent_cases.json";
import categoriesJson from "../../../../../samples/bjs/categories.json";
import scenariosJson from "../../../../../samples/bjs/check_scenarios.json";
import historyJson from "../../../../../samples/bjs/history.json";
import ordersJson from "../../../../../samples/bjs/orders.json";
import packJson from "../../../../../samples/bjs/rule_pack.json";
import proceduresJson from "../../../../../samples/bjs/procedures.json";
import ticketsJson from "../../../../../samples/bjs/tickets.json";
import { anonymiseDeep } from "../brand";
import type { HistoryFile } from "./chat";
import type { CheckScenario } from "./checkLink";
import type { AgentCasesFile } from "./negotiation";
import type { Category, Order, Procedure, RulePack, Ticket } from "./types";

export const categories = anonymiseDeep(categoriesJson as Category[]);
export const orders = anonymiseDeep(ordersJson as unknown as Order[]);
export const pack = anonymiseDeep(packJson as unknown as RulePack);
export const procedures = anonymiseDeep(proceduresJson as unknown as Procedure[]);
export const tickets = anonymiseDeep(ticketsJson as unknown as Ticket[]);
export const scenarios = anonymiseDeep((scenariosJson as { scenarios: CheckScenario[] }).scenarios);
export const agentCases = anonymiseDeep((agentCasesJson as unknown as AgentCasesFile).cases);
export const history = anonymiseDeep(historyJson as unknown as HistoryFile);
