/**
 * Merchant naming per build. The named build (demo.umpyr.org) shows the sandbox merchants by name; the
 * anonymous build (try.umpyr.org, sent to investors) shows generic stand-ins, because none of the three has
 * signed anything and a named page would read as a client list.
 *
 *   VITE_BRAND_MODE=anonymous   -> "Specialty Retailer", "Major Airline", "Warehouse Club" at /retailer, /airline, /club
 *   (unset)                     -> Rockler, Alaska Airlines, BJ's at /rockler, /alaska, /bjs
 *
 * Three hooks cover everything a page can show: `b()` for a string the UI builds, `anonymiseDeep()` for data
 * (fixtures at load, engine responses in `api()`), and `ANON` where a link or a sentence must change shape.
 * Workspace ids stay internal (API paths, metrics keys); only what is rendered changes.
 */
export type BrandMode = "named" | "anonymous";
export const BRAND_MODE: BrandMode = String(import.meta.env.VITE_BRAND_MODE ?? "").toLowerCase() === "anonymous" ? "anonymous" : "named";
export const ANON = BRAND_MODE === "anonymous";

type Merchant = { named: string; anonymous: string; slug: Record<BrandMode, string> };
export const MERCHANTS: Record<string, Merchant> = {
  rockler: { named: "Rockler", anonymous: "Specialty Retailer", slug: { named: "rockler", anonymous: "retailer" } },
  alaska: { named: "Alaska Airlines", anonymous: "Major Airline", slug: { named: "alaska", anonymous: "airline" } },
  bjs: { named: "BJ's", anonymous: "Warehouse Club", slug: { named: "bjs", anonymous: "club" } },
};

/** Display name for a workspace id ("rockler" -> "Rockler" or "Specialty Retailer"). Unknown ids come back unchanged. */
export function merchantName(workspace: string): string {
  const m = MERCHANTS[workspace];
  return m ? m[BRAND_MODE] : workspace;
}
/** Public URL segment for a workspace id. */
export function slugFor(workspace: string): string {
  return MERCHANTS[workspace]?.slug[BRAND_MODE] ?? workspace;
}
/** Workspace id behind a URL segment; a raw workspace id is accepted too so old links keep working. */
export function workspaceFor(slug: string): string {
  for (const [id, m] of Object.entries(MERCHANTS)) if (m.slug[BRAND_MODE] === slug || id === slug) return id;
  return slug;
}
/** `/retailer`, `/club/check`, ... */
export function pathFor(workspace: string, sub = ""): string {
  return `/${slugFor(workspace)}${sub}`;
}
export const BJS_BASE = pathFor("bjs");

// Rewrites, applied in order. Full names first so the short forms never half-match them. Lower-case bare
// "alaska" / "rockler" / "bjs" are left alone: they are workspace ids and enum values, not prose.
const RULES: [RegExp, string][] = [
  [/BJ['’]?s Wholesale Club/g, "Warehouse Club"],
  [/\bBJ['’]s\b/g, "Warehouse Club"],
  [/\bBJs\b/g, "Warehouse Club"],
  [/\b(?:help\.|www\.)?bjs\.com\b/gi, "warehouseclub.example"],
  [/\bBJ-(?=[A-Z0-9])/g, "WC-"], // order BJ-7007, rule BJ-SDD-4, case BJ-A-3, ticket BJ-T-...
  [/\bBJS-(?=[A-Z0-9])/g, "WC-"], // merchant reference
  [/\bAlaska Air(?:lines)?['’]?\b/gi, "Major Airline"],
  [/\b(?:www\.)?alaskaair\.com\b/gi, "majorairline.example"],
  [/\bAlaska\b/g, "Major Airline"],
  [/\bMileage Plan\b/g, "loyalty programme"],
  [/\bAS ?(\d{2,4})\b/g, "MA $1"], // flight numbers
  [/\bRockler Woodworking(?: and Hardware)?\b/gi, "Specialty Retailer"],
  [/\bRockler\b/g, "Specialty Retailer"],
  [/\b(?:www\.)?rockler\.com\b/gi, "specialtyretailer.example"],
];

/** Rewrite one string for the current build (identity in the named build). */
export function b(text: string): string {
  if (!ANON || !text) return text;
  let out = text;
  for (const [re, to] of RULES) out = out.replace(re, to);
  return out;
}

/** Rewrite every string value in a JSON-like structure (keys untouched). Identity in the named build. */
export function anonymiseDeep<T>(value: T): T {
  if (!ANON) return value;
  return walk(value) as T;
}
function walk(v: unknown): unknown {
  if (typeof v === "string") return b(v);
  if (Array.isArray(v)) return v.map(walk);
  if (v && typeof v === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(v as Record<string, unknown>)) out[k] = walk(x);
    return out;
  }
  return v;
}

/** Anything that would name a real merchant in the anonymous build. The guard test fails on a match. */
export const FORBIDDEN = /BJ['’]s|\bBJs\b|\bBJS?-[A-Z0-9]|bjs\.com|Wholesale Club|Rockler|Alaska|alaskaair|rockler\.com|Mileage Plan/;
