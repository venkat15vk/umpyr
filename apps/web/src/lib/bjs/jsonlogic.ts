/**
 * Minimal JSON Logic evaluator mirroring packages/rules/umpyr_rules/jsonlogic.py for the operators the
 * BJ's rule pack uses: var, missing, and, or, !, !!, ==, !=, <, <=, >, >= (chained), in, +, -, *, /, min,
 * max, if. Numbers stored as strings (Decimal in the engine) compare numerically.
 */

export type Logic = unknown;
export type Facts = Record<string, unknown>;

export function getVar(facts: Facts, path: string): unknown {
  if (!path) return facts;
  let cur: unknown = facts;
  for (const part of path.split(".")) {
    if (cur === null || cur === undefined || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[part];
  }
  return cur === null ? undefined : cur;
}

function num(v: unknown): number | null {
  if (typeof v === "number") return v;
  if (typeof v === "string" && v.trim() !== "" && !Number.isNaN(Number(v))) return Number(v);
  if (typeof v === "boolean") return v ? 1 : 0;
  return null;
}

function eq(a: unknown, b: unknown): boolean {
  if ((a === null || a === undefined) && (b === null || b === undefined)) return true;
  if (a === null || a === undefined || b === null || b === undefined) return false;
  const na = num(a), nb = num(b);
  if (na !== null && nb !== null && typeof a !== "boolean" && typeof b !== "boolean") return na === nb;
  return a === b;
}

function truthy(v: unknown): boolean {
  if (Array.isArray(v)) return v.length > 0;
  if (v && typeof v === "object") return Object.keys(v).length > 0;
  return !!v;
}

function cmp(op: string, args: unknown[]): boolean {
  for (let i = 0; i < args.length - 1; i++) {
    const l = num(args[i]), r = num(args[i + 1]);
    if (l === null || r === null) return false;
    const ok = op === "<" ? l < r : op === "<=" ? l <= r : op === ">" ? l > r : l >= r;
    if (!ok) return false;
  }
  return true;
}

export function apply(logic: Logic, facts: Facts): unknown {
  if (Array.isArray(logic)) return logic.map((l) => apply(l, facts));
  if (logic === null || typeof logic !== "object") return logic;
  const keys = Object.keys(logic as object);
  if (keys.length !== 1) return logic;
  const op = keys[0];
  const raw = (logic as Record<string, unknown>)[op];
  const rawArgs: unknown[] = Array.isArray(raw) ? raw : [raw];

  if (op === "var") {
    const path = apply(rawArgs[0], facts);
    const v = getVar(facts, String(path ?? ""));
    return v === undefined && rawArgs.length > 1 ? apply(rawArgs[1], facts) : v;
  }
  if (op === "missing") {
    const paths = (Array.isArray(raw) ? raw : [raw]).map((p) => String(apply(p, facts)));
    return paths.filter((p) => getVar(facts, p) === undefined);
  }
  if (op === "if") {
    for (let i = 0; i + 1 < rawArgs.length; i += 2) if (truthy(apply(rawArgs[i], facts))) return apply(rawArgs[i + 1], facts);
    return rawArgs.length % 2 === 1 ? apply(rawArgs[rawArgs.length - 1], facts) : null;
  }
  if (op === "and") {
    let last: unknown = true;
    for (const a of rawArgs) { last = apply(a, facts); if (!truthy(last)) return last; }
    return last;
  }
  if (op === "or") {
    let last: unknown = false;
    for (const a of rawArgs) { last = apply(a, facts); if (truthy(last)) return last; }
    return last;
  }
  const args = rawArgs.map((a) => apply(a, facts));
  switch (op) {
    case "==": case "===": return eq(args[0], args[1]);
    case "!=": case "!==": return !eq(args[0], args[1]);
    case "!": return !truthy(args[0]);
    case "!!": return truthy(args[0]);
    case "<": case "<=": case ">": case ">=": return cmp(op, args);
    case "in": return Array.isArray(args[1]) ? args[1].some((x) => eq(x, args[0])) : typeof args[1] === "string" ? args[1].includes(String(args[0])) : false;
    case "+": return args.reduce<number>((s, a) => s + (num(a) ?? 0), 0);
    case "-": return args.length === 1 ? -(num(args[0]) ?? 0) : (num(args[0]) ?? 0) - (num(args[1]) ?? 0);
    case "*": return args.reduce<number>((s, a) => s * (num(a) ?? 0), 1);
    case "/": return (num(args[0]) ?? 0) / (num(args[1]) ?? 1);
    case "min": return Math.min(...args.map((a) => num(a) ?? Infinity));
    case "max": return Math.max(...args.map((a) => num(a) ?? -Infinity));
    default: throw new Error(`unsupported json-logic op: ${op}`);
  }
}

/** Every `var` path referenced by a logic tree (for the fields column of the ops table). */
export function collectVars(logic: Logic, out: Set<string> = new Set()): Set<string> {
  if (Array.isArray(logic)) { logic.forEach((l) => collectVars(l, out)); return out; }
  if (logic && typeof logic === "object") {
    for (const [k, v] of Object.entries(logic as Record<string, unknown>)) {
      if (k === "var") { if (typeof v === "string") out.add(v); else if (Array.isArray(v) && typeof v[0] === "string") out.add(v[0]); }
      else collectVars(v, out);
    }
  }
  return out;
}
