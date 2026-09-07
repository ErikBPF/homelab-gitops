# Native Discord delivery drill — 2026-09-07

**Status:** Native firing/recovery and cleanup verified; operator receipt and wording confirmation pending.

The operator explicitly selected all four next steps, including notification
receipt. Expect one firing and one resolved message titled `[DRILL] Native
Discord delivery` through the existing `discord` / `discord-incidents` receiver.
The unchanged policy groups by alert name, isolating this uniquely named drill.
All payload fields are synthetic; no event values or credentials are included.

A temporary native Grafana rule uses the existing Prometheus/reduce/threshold
shape and `vector(time() < bool CUTOFF)`. It fires immediately on evaluation,
then becomes false after 180 seconds even if the controller disconnects. The
receiver's 30-second group wait and five-minute group interval remain unchanged.
A ten-minute recovery deadline bounds the operation. No production rule,
workload, contact point, silence or routing policy is changed.

The controller records only hashes of pre-existing rule payloads and the safe
canary payload. Observe the exact canary identity firing, its terminal normal
state, native Discord counter changes as supporting evidence, and operator
receipt/usefulness. Counters alone cannot attribute deliveries in concurrent
traffic. Delete only this temporary UID after recovery; compare all production
rule hashes. On failure, delete it and stop later drills.

This checks native notification delivery; operator receipt remains required.
It does not prove real security-event detection,
Alloy identity-loss behavior or Loki outage recovery. Those separate A5 drills
and natural scheduled-job evidence must pass before formal seven-day closure.

## Execution receipt

- Created `delivery-drill-20260907` September 7 at **21:14:04 UTC**; exact
  expiry `1788815824` (**21:17:04 UTC**), ten-minute deadline 21:24:04 UTC.
- The exact canary entered firing at **21:14:30 UTC**, active and neither
  silenced nor inhibited. At 21:19:57 UTC it was absent from active Alertmanager
  alerts. The native evaluator reported `inactive`, `health=ok`, no error, at
  its **21:20:30 UTC** evaluation.
- Native Discord request/notification counters moved **9 → 10 → 11** across
  the firing/recovery interval. This supports delivery attempts but does not
  attribute individual Discord messages or replace operator receipt.
- Deleted the exact temporary UID at **21:21 UTC**, before its deadline.
  All **62** pre-existing production rule hashes remained identical.
- The operator was asked to confirm the firing/resolved pair and wording.
  No answer has been recorded; receipt/usefulness remains open.
