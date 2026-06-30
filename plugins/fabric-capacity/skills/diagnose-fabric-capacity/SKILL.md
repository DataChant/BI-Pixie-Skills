---
name: diagnose-fabric-capacity
description: >
  Answer almost any admin question about Microsoft Fabric capacities by running read-only DAX over
  the Capacity Metrics and Chargeback apps via the Power BI Execute Queries REST API, on a Pro
  license (no Premium, no XMLA, no notebook). Covers capacity health and risk, throttling and
  overload, utilization vs limit, overage, blocked workspaces and affected users, storage and memory,
  the capacity state timeline, and a 30-second timepoint drill naming the exact items, operations,
  and users that consumed CU.
  Use when asked why a capacity is slow or throttling, whether it is healthy or at risk, what
  overloaded it, which workspaces were blocked or users affected, or which items burn capacity.
  Triggers: "is my capacity throttling", "capacity health", "capacity at risk", "what overloaded my
  capacity", "blocked workspaces", "affected users", "overage", "who used my capacity".
  For pure CU/cost reading use query-fabric-capacity-cu; for cost-vs-engagement ROI use fabric-cu-roi.
allowed-tools: Bash
---

# Diagnose a Fabric capacity (Pro, no XMLA)

Everything an admin needs to understand their capacities, workspaces, and affected items and users,
read straight from the Microsoft **Capacity Metrics** and **Chargeback** semantic models with one
read-only `executeQueries` REST call at a time. No Premium, no XMLA endpoint, no notebook.

The full schema, the question-to-table map, and every parameter are in
[`references/capacity-model-guide.md`](../../references/capacity-model-guide.md). Read it when you
need a table or measure name that is not in this file.

## Setup

- Sign in once as a **capacity admin** with `az login` (the runner's default) or, if you have the
  Fabric CLI, `fab auth login`. The runner auto-detects; force one with `--cli az` / `--cli fab`.
- The **Capacity Metrics** and/or **Chargeback** app must be installed, and the tenant setting
  "Semantic model Execute Queries REST API" enabled.
- Resolve the models by **name** (GUIDs rotate): workspace `Microsoft Fabric Capacity Metrics`, model
  `Fabric Capacity Metrics`; workspace `Microsoft Fabric Chargeback Reporting`, model
  `Fabric Chargeback Reporting`.

The bundled runner handles auth, name resolution, and the parameters:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/run_dax.py" \
  --workspace "Fabric Capacity Metrics" --dataset "Fabric Capacity Metrics" \
  [--capacity <guid>] [--region "East US"] [--timepoint 2026-06-27T12:12:30] \
  --dax-file "${CLAUDE_PLUGIN_ROOT}/examples/<file>.dax"   # or --dax "EVALUATE ..."
```

`--capacity` adds `MPARAMETER 'CapacitiesList'`; `--region` adds `MPARAMETER 'RegionName'` (needed
only for a capacity outside your home/default region; add it if such a capacity returns no rows);
`--timepoint` adds `MPARAMETER 'TimePoint'`. Pass plain `EVALUATE` statements; the runner builds the
single `DEFINE` block. Add `--json` for raw rows. If the workspace name lookup misses (a reinstall
can append a timestamp to it), pass the workspace ID instead.

## The diagnostic workflow (top down)

Drill from "all my capacities" to "the exact operation at one 30-second window".

### 1. Health of every capacity (no parameter)

Start here. The `... by capacity (last 24 hours)` measures spread across all capacities you
administer, so one call ranks them by risk, utilization, throttling, P95 delays, and affected users.

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/run_dax.py" \
  --workspace "Microsoft Fabric Capacity Metrics" --dataset "Fabric Capacity Metrics" \
  --dax-file "${CLAUDE_PLUGIN_ROOT}/examples/capacity-health-overview.dax"
```

`Risk` values include `Healthy`, `At Risk of Throttling`, `Throttling`, `Interactive Rejection`,
`Background Rejection`, `Overage Billing Active`, `Suspended`. Swap `(last 24 hours)` for
`(last 1 hour)` in the query for near-real-time. Get the GUID of any capacity of interest from
`examples/list-capacities.dax`.

### 2. What happened on a capacity, and when (`--capacity <guid>`)

- **State timeline** (overloaded / paused / resumed, with the reason):
  `examples/capacity-system-events.dax`.
- **Most-stressed 30-second windows** (interactive delay %, rejection %, overage). A `%` above 1.0
  means the capacity crossed that throttling threshold for the window:
  `examples/throttling-windows.dax`. Note the worst window's `Window` start time for step 4.

### 3. What is consuming the capacity (`--capacity <guid>`)

- **Top items by CU**, with throttling, duration, operations, and users:
  `examples/cu-and-throttling-by-item.dax`.
- **What changed recently** (CU by item over a date range, e.g. last 7 days):
  `examples/cu-by-item-last-n-days.dax` (edit the day count, or swap in two fixed dates). Use this
  for "why did utilization rise this week?" before drilling into a single window.
- **CU by experience, and an item's day-by-day history** (the one Capacity Metrics table with
  `Experience` plus throttling in seconds): `examples/item-history-by-experience.dax`. Narrow it with
  the optional `WorkspaceIDHistory` / `OperationNameHistory` / `UsernameHistory` list params.
- **Storage by workspace**: `examples/storage-by-workspace.dax`.
- **Billed overage** (is the capacity paying overage?): the `[Processed overage]` and
  `[Overage billing limit CUhr]` measures (need only `--capacity`); see the model guide.
- **Blocked workspaces and affected users** (workspace surge protection):
  `examples/blocked-workspaces.dax` (no rows means nothing was blocked).
- For **per-user** or **per-experience** cost, switch to the Chargeback model (see
  `query-fabric-capacity-cu`); Chargeback is imported and needs no parameter.

### 4. Who and what at one window (`--capacity <guid> --timepoint <iso>`)

The deepest drill. Take a window start time from step 2 and list the named items, operations, and
users that consumed CU in that 30-second window:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/run_dax.py" \
  --workspace "Microsoft Fabric Capacity Metrics" --dataset "Fabric Capacity Metrics" \
  --capacity <guid> --timepoint 2026-06-26T12:44:30 \
  --dax-file "${CLAUDE_PLUGIN_ROOT}/examples/timepoint-operations.dax"
```

Edit the example to read `Timepoint Interactive Detail` instead of `Timepoint Background Detail` for
interactive operations. This is the only place the model exposes the **user** behind an individual
operation.

## How to report it

Give the admin a short, ranked answer, not a raw dump:

- **Lead with risk**: name the capacity, its risk state, peak utilization, and whether and when it
  throttled.
- **Attribute**: the top few items (and users, from Chargeback or the timepoint drill) driving CU,
  with their share.
- **Affected**: any blocked workspaces and the users/requests they hit.
- **Recommend**: scale up, enable autoscale or overage, reschedule heavy background jobs off the
  peak, or right-size the worst items. Pair with usage analytics (see `fabric-cu-roi`) to separate
  "expensive and used" from "expensive and idle".

## Gotchas

- `Throttling (min)` on aggregate tables vs `Throttling (s)` on timepoint detail and health measures.
- The timepoint detail tables carry the item GUID in `[Item]`; the example joins to `Items` for the
  name (a live relationship exists).
- User identity can arrive as `<euii>name</euii>` depending on the tenant "Show user data" setting.
- These are preview, unsupported models. Resolve by name, expect schema drift, and persist anything
  you need beyond ~14 days (compute) or ~30 days (storage, Chargeback).
