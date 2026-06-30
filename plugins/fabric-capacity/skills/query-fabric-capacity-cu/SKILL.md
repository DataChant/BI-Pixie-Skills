---
name: query-fabric-capacity-cu
description: >
  Read Microsoft Fabric Capacity Unit (CU) usage per item, operation, and user from the Capacity
  Metrics and Chargeback apps by running read-only DAX through the Power BI Execute Queries REST API.
  Works from a Power BI Pro workspace with no Premium, no XMLA endpoint, and no notebook.
  Use when the user asks how much CU something consumes, which items or users are burning capacity,
  top CU consumers, throttling, or capacity cost.
  Triggers: "Fabric CU usage", "capacity units by item", "what is consuming my capacity",
  "top CU consumers", "Capacity Metrics app", "Chargeback app", "CU by user", "cost of a report".
  For capacity health, throttling, overload, blocked workspaces, and the per-window who/what drill,
  use `diagnose-fabric-capacity`. For cost-efficiency (CU vs engagement), use `fabric-cu-roi`. For
  Fabric CLI sign-in and workspace discovery, use Microsoft's skills-for-fabric.
allowed-tools: Bash
---

# Query Fabric Capacity CU (Pro, no XMLA)

Read CU usage from the Microsoft Fabric **Capacity Metrics** and **Chargeback** semantic models with
a single read-only `executeQueries` REST call. No Premium, no XMLA endpoint, no notebook.

For the full schema and a question-to-table map, see
[`references/capacity-model-guide.md`](../../references/capacity-model-guide.md). For capacity
**health** (throttling, overload, blocked workspaces, the per-window who/what drill), use
[`diagnose-fabric-capacity`](../diagnose-fabric-capacity/SKILL.md).

## Prerequisites

- Microsoft [skills-for-fabric](https://github.com/microsoft/skills-for-fabric) for sign-in and
  workspace/dataset discovery, OR the Azure CLI (`az login`) / Fabric CLI (`fab auth login`) as a
  **capacity admin**.
- The **Capacity Metrics** and/or **Chargeback** app installed; tenant setting
  "Semantic model Execute Queries REST API" enabled.

## Step 1: Resolve the model by name (never hard-code the GUID)

The dataset GUID rotates on every app reinstall; the names are stable. Resolve:
- Capacity Metrics: workspace `Microsoft Fabric Capacity Metrics`, model `Fabric Capacity Metrics`.
- Chargeback: workspace `Microsoft Fabric Chargeback Reporting`, model `Fabric Chargeback Reporting`.

With the Fabric CLI:

```bash
fab api "workspaces" -q "text.value[?displayName=='Microsoft Fabric Chargeback Reporting'].id"
fab api "workspaces/<workspaceId>/items?type=SemanticModel" -q "text.value[].{n:displayName,id:id}"
```

## Step 2: Run the query

Endpoint (one query per request, response cap 100k rows / 1M values / 15 MB / 120 req/min):

```
POST https://api.powerbi.com/v1.0/myorg/datasets/{datasetId}/executeQueries
```

### Chargeback (simplest, recommended): fully imported, per-user, ~30-day history

No parameter needed. CU by item, operation, user, and experience:

```dax
EVALUATE
TOPN(50,
  FILTER(
    SUMMARIZECOLUMNS(
      'Items'[Item name], 'Items'[Item kind],
      'Chargeback'[User], 'Chargeback'[Experience],
      "CU_s", SUM('Chargeback'[CU (s)])
    ), [CU_s] > 0),
  [CU_s], DESC)
```

### Capacity Metrics: DirectQuery facts, needs the capacity parameter

The one detail that trips people up: the M parameter must be wrapped in a `DEFINE` block. A bare
`MPARAMETER` line is rejected ("The syntax for 'MPARAMETER' is incorrect").

```dax
DEFINE
MPARAMETER 'CapacitiesList' = { "<capacity-guid-lowercase>" }
EVALUATE
TOPN(50,
  FILTER(
    SUMMARIZECOLUMNS(
      'Items'[Item name], 'Items'[Item kind], 'Items'[Workspace name],
      FILTER(Capacities, Capacities[Capacity Id] = "<capacity-guid-lowercase>"),
      "CU_s", SUM('Metrics By Item And Day'[CU (s)])
    ), [CU_s] > 0),
  [CU_s], DESC)
```

Get the capacity GUID from `EVALUATE SELECTCOLUMNS(Capacities, "id", Capacities[Capacity Id], "state", Capacities[State])`.

## Step 3: The easiest ad-hoc call (az CLI, admin credentials)

As a capacity admin, `az login` then let `az rest` handle the token. Put the query body in a file to
avoid shell-quoting the DAX:

```bash
# query.json: {"queries":[{"query":"EVALUATE ROW(\"CU\", SUM('Chargeback'[CU (s)]))"}]}
az rest --method post \
  --url "https://api.powerbi.com/v1.0/myorg/datasets/<datasetId>/executeQueries" \
  --resource "https://analysis.windows.net/powerbi/api" \
  --body @query.json
```

The Fabric CLI equivalent: `fab api -A powerbi -X post "datasets/<datasetId>/executeQueries" -i query.json`.

## Run it with the bundled script

This plugin ships a small stdlib runner (`scripts/run_dax.py`) that resolves the model by name,
injects the `DEFINE MPARAMETER` prefix when you pass `--capacity`, and prints the rows. It works with
whichever CLI you have: `az login` by default, or `fab auth login` if the Fabric CLI is installed
(it auto-detects, and `fab` adds native name resolution). Force one with `--cli az` or `--cli fab`.

```bash
# Chargeback (no parameter):
python "${CLAUDE_PLUGIN_ROOT}/scripts/run_dax.py" \
  --workspace "Microsoft Fabric Chargeback Reporting" \
  --dataset "Fabric Chargeback Reporting" \
  --dax-file "${CLAUDE_PLUGIN_ROOT}/examples/chargeback-cu-by-item.dax"

# Capacity Metrics (DirectQuery) -- --capacity adds the DEFINE MPARAMETER for you:
python "${CLAUDE_PLUGIN_ROOT}/scripts/run_dax.py" \
  --workspace "Microsoft Fabric Capacity Metrics" \
  --dataset "Fabric Capacity Metrics" --capacity <capacity-guid> \
  --dax-file "${CLAUDE_PLUGIN_ROOT}/examples/capacity-metrics-cu-by-item.dax"
```

Add `--json` for raw JSON output.

Bundled example queries (in `${CLAUDE_PLUGIN_ROOT}/examples/`):
- `list-capacities.dax` - capacities, ids, and state (get the `--capacity` GUID here)
- `chargeback-cu-by-item.dax` - CU by item, kind, workspace (Chargeback)
- `chargeback-cu-by-user.dax` - CU by user and experience (Chargeback)
- `chargeback-cu-by-domain.dax` - CU by Fabric domain and subdomain (Chargeback)
- `capacity-metrics-cu-by-item.dax` - CU by item (Capacity Metrics; run with `--capacity`)
- `cu-and-throttling-by-item.dax` - CU plus throttling, duration, operations, users per item

The `diagnose-fabric-capacity` skill bundles more: `capacity-health-overview.dax`,
`throttling-windows.dax`, `capacity-system-events.dax`, `blocked-workspaces.dax`,
`storage-by-workspace.dax`, `item-history-by-experience.dax` (CU by experience + per-item history),
and `timepoint-operations.dax` (the per-window who/what drill).

## Which model to use

- **Chargeback**: cost attribution. Per-user and per-experience CU, ~30 days, imported (fastest).
- **Capacity Metrics**: capacity health. Throttling, utilization vs limit, overage, memory, storage,
  30-second timepoint detail, hourly grain. Needs the `CapacitiesList` parameter per capacity.

## Gotchas

- `DEFINE MPARAMETER` is mandatory for Capacity Metrics; the Chargeback model needs no parameter.
- For a capacity **outside your home/default region**, also set `MPARAMETER 'RegionName' = "<region>"`
  in the same `DEFINE` block (the runner's `--region` flag), or its fact tables come back empty.
- The models are versioned and column names drift (for example `Capacity Id` vs `capacity Id`).
  Detect the version or resolve columns defensively for anything durable.
- These models are preview and "unsupported for direct query." Read-only `EVALUATE` only.
- For per-user CU, prefer Chargeback. In Capacity Metrics the user identity lives only in the
  parameter-gated 30-second timepoint detail, not in the daily aggregates (which keep a user count).
