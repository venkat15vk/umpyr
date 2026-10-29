# BJ's Wholesale Club sandbox (illustrative demo, not affiliated)

Fixtures for the two BJ's demo pages: `/bjs` (Umpyr Resolve) and `/bjs/check` (Umpyr Check). Everything here is
synthetic: members, orders, agents, tickets, cards, hashes. No real customer data, no BJ's logos or trademarks
beyond the name in text. Built 2026-10-28 for a partner presentation; no model calls, no engine, no spend.

| file | what |
|---|---|
| `sources.json` | public BJ's pages the clauses cite, with fetch date and a note on how they were read |
| `rule_pack.json` | compiled policy pack `2026-10-28.1`: 26 clauses, 29 JSON Logic rules, same schema as `samples/rockler/rule_pack.json` plus a `confirmed` flag per clause |
| `procedures.json` | approved procedure library, one per category (8) |
| `categories.json` | the 8 dispute categories with a BJ's-specific lifecycle-closure note each |
| `orders.json` | 10 synthetic orders (same-day delivery, curbside, in-club, membership, gas, agent-placed) |
| `tickets.json` | 16 synthetic tickets: 8 categories x 2 channels (forwarded email, member's AI agent) with the facts the extractor pulls |
| `check_scenarios.json` | 8 `umpyr-check/0.1` requests (agent, mandate, cart, masked member, context) with the expected decision |

## Confirmed vs illustrative

Clauses with `confirmed: true` quote a bjs.com or help.bjs.com page read in a browser on 2026-10-28 (bjs.com returns
403 to server-side fetches; the URL is on each clause). Clauses with `confirmed: false` have a `practice://` source and
are **illustrative practice rules** the UI labels as such: unapproved-substitution refund (BJ-SDD-4), late perishable
damage needs a photo (BJ-RET-7), pickup-fee refund when the order is ready 30+ minutes after check-in (BJ-PU-4),
clipped-coupon refund (BJ-CPN-2), duplicate capture reversal (BJ-PAY-3), agent intent-mismatch handling (BJ-AGT-1).
BJ's publishes no general post-purchase price-adjustment policy (only a day-of-purchase TV price match), so the
price-adjustment ticket escalates to a person by design.

The impact metrics on the Resolve page header and every timing on the Check page are illustrative.

## Ontology extensions (all additive; `packages/ontology`)

- `Fault` gains `agent`, `agent_platform`, `third_party`, `indeterminate` (enums.py).
- New module `umpyr_ontology/check.py` (`umpyr-check/0.1`): `Agent`, `Mandate` (+ `MandateItem`, `MandateSignature`),
  `Cart` (+ `CartLine`), `MaskedCustomer` (card/velocity/membership/history aggregates, hashes only), `CheckContext`,
  `CheckRequest`, `CheckDecision` (+ `LineDiff`, `ModelFire`, `CheckAction`, `Commitment`, `LedgerRef`).
- Demo-side (TypeScript, `apps/web/src/lib/bjs/types.ts`), not yet in the Pydantic models: `Item` + `pack_size`,
  `units_per_case`, `is_perishable`, `is_fuel`, `unit`, `department`, `return_window_days`, `condition`,
  `substituted_with`, `coupon_id`, `coupon_value`; `Order` + `fulfilment`, `charges[]`, `member`, `placed_by`,
  `agent_id`, `check` (the Umpyr Check record an order carries), `payments` (derived counts); new `Fulfilment`
  (method, partner, picked_by, substitutions_allowed, ready_delay_minutes, ...); new `Member` (tier, fee, renewal,
  prior_fee_refunds, fingerprints_30d, regions_30d); `Claim` + `quantity_intended`, `quantity_received`,
  `cases_to_keep`, `coupon_id`, `advertised_price`, `wants_cancel_membership`, `used_benefits_since_renewal`,
  `hold_age_hours`; new claim reasons `missing_item`, `substituted_item`, `spoiled`, `pickup_not_ready`,
  `pickup_cancelled_late`, `coupon_not_applied`, `price_adjustment`, `membership_refund`, `agent_quantity_error`,
  `duplicate_charge`, `preauth_hold`.

## Engine boundary

`apps/web/src/lib/bjs/adapters.ts` is the only thing the pages call. `LocalResolveAdapter.run(ticket, options)` and
`LocalCheckAdapter.decide(request, ledger)` are deterministic simulations; `RemoteResolveAdapter` and
`RemoteCheckAdapter` call `POST /workspaces/bjs/cases/replay` and `POST /check/decisions` with the same shapes.
Hash commitments are real SHA-256 over sorted-key canonical JSON (`sha256.ts`, FIPS vectors in `bjs.test.ts`); the
audit signature is HMAC-SHA256 with a demo key, labelled as such (production: per-tenant Ed25519).
