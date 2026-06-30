# Fabric capacity model guide (Capacity Metrics + Chargeback)

A map of the two Microsoft Fabric monitoring semantic models so an AI agent can answer almost any
admin question about capacities, workspaces, items, operations, and users by running read-only DAX
through the Power BI Execute Queries REST API. Everything here works from a **Power BI Pro** license,
with no Premium, no XMLA endpoint, and no notebook.

> These are Microsoft preview apps. Microsoft documents the models as supported only for the reports
> inside the apps; treat direct queries as best-effort. Resolve models by **name** (GUIDs rotate on
> reinstall), expect column and measure names to drift between app versions, and persist anything you
> need beyond the built-in retention (about 14 days for Capacity Metrics compute, about 30 days for
> storage and for Chargeback).

## The two models, and which to reach for

| Model | Workspace name | Model name | Grain it adds | Retention |
|-------|----------------|-----------|---------------|-----------|
| **Capacity Metrics** | `Microsoft Fabric Capacity Metrics` | `Fabric Capacity Metrics` | Capacity health: throttling, utilization vs limit, overage, storage, memory, 30-second timepoint detail | ~14 days compute, ~30 days storage |
| **Chargeback** | `Microsoft Fabric Chargeback Reporting` | `Fabric Chargeback Reporting` | Cost attribution: per-**user**, per-**experience**, per-**domain** CU, fully imported | ~30 days |

Use **Chargeback** for "who and what is costing CU" (simplest: imported, no parameter, per-user).
Use **Capacity Metrics** for "is my capacity healthy, throttling, or overloaded, and what is
affected" (the superset for health). For the per-operation user at one 30-second window, use Capacity
Metrics' timepoint detail.

## Access pattern and the two parameters

Endpoint (one query per request; cap 100k rows / 1M values / 15 MB; 120 requests/min/user):

```
POST https://api.powerbi.com/v1.0/myorg/datasets/{datasetId}/executeQueries
```

- **Chargeback**: fully imported. A plain `EVALUATE` works. No parameter.
- **Capacity Metrics**: the dimension tables (Capacities, Items, Workspaces) are imported, but the
  fact tables are DirectQuery and return **nothing** until you set parameters with a `DEFINE` block
  (a bare `MPARAMETER` line is rejected with "The syntax for 'MPARAMETER' is incorrect"):

  - `MPARAMETER 'CapacitiesList' = { "<capacity-guid-lowercase>" }` scopes the CU/throttling/storage
    facts to one (or several) capacities. Required for nearly every Capacity Metrics fact query.
  - `MPARAMETER 'RegionName' = "East US"` scopes to a capacity's region. The home/default region
    works without it, but a capacity in another region needs it. The report sets it on the Health
    page paired with a `TREATAS({"East US"}, 'Capacities'[Region])` filter. Rule of thumb: if a
    non-home-region capacity returns no fact rows, add `RegionName` set to that capacity's `Region`.
  - `MPARAMETER 'TimePoint' = (DATE(y,m,d) + TIME(h,mi,s))` selects one 30-second window for the
    timepoint-detail tables (`Timepoint Interactive Detail`, `Timepoint Background Detail`). Combine
    with `CapacitiesList` in the **same** `DEFINE` block.

  All three go in one `DEFINE` block. The report's own queries (captured via Log Analytics) scope
  with `TREATAS({"<guid>"}, 'Capacities'[Capacity Id])` rather than `FILTER(...)`, and wrap each
  measure in `IGNORE(...)`; both are equivalent to the patterns here. The runner's `--region`,
  `--capacity`, and `--timepoint` flags assemble the block for you.

  ```dax
  DEFINE
  MPARAMETER 'CapacitiesList' = { "b9c73e05-7478-4baa-98df-cb25b619c8a7" }
  MPARAMETER 'TimePoint' = (DATE(2026,6,27) + TIME(12,12,30))
  EVALUATE ...
  ```

**Exception worth knowing:** the cross-capacity **Health** measures (the `... by capacity (last 24
hours)` family) need **no parameter at all**. They aggregate over the imported side and spread across
the `Capacities` dimension, so you get every capacity you administer in one call.

The bundled `scripts/run_dax.py` injects both parameters for you: `--capacity <guid>` and
`--timepoint 2026-06-27T12:12:30`. Pass plain `EVALUATE` statements; let the script build the
`DEFINE`.

## Question -> where to get it

Each row is a real, tested query. The example files live in `examples/`.

| Admin question | Model | Table(s) / measures | Example | Param |
|----------------|-------|---------------------|---------|-------|
| Which capacities are healthy / at risk / throttling right now? | Metrics | `Capacities` + `[Risk status by capacity (last 24 hours)]`, `[Average utilization by capacity ...]`, `[Throttling(s) by capacity ...]`, `[P95 interactive delay by capacity ...]` | `capacity-health-overview.dax` | none |
| Near-real-time health (last hour) | Metrics | same measures, swap `(last 1 hour)` | `capacity-health-overview.dax` | none |
| What is consuming my CU? Top items | Metrics | `Items` + `Metrics By Item And Operation`/`...And Day`[CU (s)] | `cu-and-throttling-by-item.dax` | `--capacity` |
| What changed recently? CU by item over a date range | Metrics | `Items` + `Metrics By Item And Day`[CU (s)] filtered on `[Date]` | `cu-by-item-last-n-days.dax` | `--capacity` |
| CU by user / by experience | Chargeback | `Chargeback`[User], [Experience], [CU (s)] | `chargeback-cu-by-user.dax` | none |
| CU by item with cost attribution | Chargeback | `Items`[Item name] + `Chargeback`[CU (s)] | `chargeback-cu-by-item.dax` | none |
| CU by domain / subdomain (business unit) | Chargeback | `Domains`[Domain],[Subdomain] + `Chargeback`[CU (s)] | `chargeback-cu-by-domain.dax` | none |
| When was the capacity overloaded / paused / resumed? | Metrics | `System Events` | `capacity-system-events.dax` | `--capacity` |
| Which 30-second windows were most stressed? | Metrics | `CU Detail`[Interactive delay %], [Interactive rejection %], [Background rejection %], [Processed overage] | `throttling-windows.dax` | `--capacity` |
| Who and what consumed CU at a specific window? | Metrics | `Timepoint Background/Interactive Detail` joined to `Items` | `timepoint-operations.dax` | `--capacity --timepoint` |
| Which workspaces were blocked (surge protection)? Affected users? | Metrics | `Surge Protection Blocked Workspaces Detail` | `blocked-workspaces.dax` | `--capacity` |
| Storage (GB) by workspace | Metrics | `Storage By Workspaces`[Utilization (GB)] | `storage-by-workspace.dax` | `--capacity` |
| Which items were throttled? | Metrics | `Items Throttled` (same shape as `Items`) | (adapt `cu-and-throttling-by-item.dax`) | `--capacity` |
| Memory footprint per item | Metrics | `Max Memory By Item`[Item size (GB)] | (adapt) | `--capacity` |
| CU by **experience** (AS/ML/Spark/Kusto), with throttling-seconds | Metrics | `Item History Main`[Experience] + `Item History Operation`[CU (s)],[Throttling (s)] | `item-history-by-experience.dax` | `--capacity` |
| An item's day-by-day history | Metrics | `Item History Main` + `Item History Operation`[Day] | `item-history-by-experience.dax` (add `[Day]`) | `--capacity` |
| Billed overage / carryforward over time | Metrics | `[Processed overage]`, `[Overage billing limit CUhr]`, `CU Detail[Processed overage]` | (adapt) | `--capacity` |
| List capacities + ids + state | Metrics | `Capacities` | `list-capacities.dax` | none |

## Capacity Metrics: key tables and columns (real, from the live model)

Dimensions (imported, query freely):

- **Capacities**: `Capacity Id`, `Capacity name`, `SKU`, `State`, `Region`, `Owners`.
- **Items**: `Item Id`, `Item name`, `Item kind`, `Workspace Id`, `Workspace name`, `Capacity Id`,
  `Billable type`, `Users`, `Virtualised item`, `Virtualised workspace`.
- **Items Throttled**: same columns as `Items`, but only items that hit throttling.
- **Workspaces**: `Workspace Id`, `Workspace name`, `Capacity Id`, `Workspace provision state`.
- **Operation Names**: `Operation name`. **Item Kind**: `Item kind`. **Billing Type**: `Billing type`.
  **Workloads**: `Workload kind`. **Dates**: `Date`, `Day`, `Start of month`.

Aggregate facts (DirectQuery, need `CapacitiesList`):

- **Metrics By Item And Operation**: `Operation name`, `Item Id`, `CU (s)`, `Duration (s)`,
  `Operations`, `Users`, `Throttling (min)`, `Billing type`, and per-status counts
  (`Rejected/Successful/Failed/Inprogress/Cancelled/Invalid/Stopped operations`).
- **Metrics By Item And Day** / **And Hour**: same metrics plus `Date`/`Datetime`. Use for trends.
- **Storage By Workspaces** (+ `And Day`/`And Hour`): `Workspace Id`, `Utilization (GB)`,
  `Static storage in GB`, `Capacity Id`.
- **Max Memory By Item**: `Item Id`, `Item size (GB)`, `SKU memory`.
- **CU Detail**: the 30-second utilization series. `Window start time`, `CU (s)`, `CU limit`,
  `Interactive`, `Background`, `Interactive delay %`, `Interactive rejection %`,
  `Background rejection %`, `Auto scale capacity units`, `Processed overage`, `Overage billing limit`.
- **System Events**: `Capacity state`, `Capacity state change reason`, `Capacity state transition time`.
- **Surge Protection Blocked Workspaces Detail**: `Workspace name`, `Blocked date`, `Blocked end`,
  `Blocked duration (hours)`, `Affected users`, `Interactive rejected operations`,
  `Background rejected operations`, `How blocked`.

Timepoint detail (DirectQuery, need `CapacitiesList` + `TimePoint`):

- **Timepoint Interactive Detail** / **Timepoint Background Detail**: `Item` (item GUID; join to
  `Items` for the name), `Operation`, `Operation Id`, `User`, `Status`, `Start`, `End`,
  `Duration (s)`, `Throttling (s)`, `Total CU (s)`, `Timepoint CU (s)`, `% of base capacity`,
  `Billing type`, `Workspace Id`, `Capacity Id`. Per-operation grain (carries the **User**).
- **Timepoint Interactive Summary** / **Timepoint Background Summary**: operation-**type** grain at
  the window (`% of base capacity`, `Duration (s)`, `Operations`, `Throttling (s)`), joined to
  **Items Operations** (`Workspace name`, `Item name`, `Operation name`, `Billing type`) for names.
  Lighter than the Detail tables; use Detail when you need the per-operation user.
- Binding (confirmed via report capture): the report sets `MPARAMETER 'TimePoint' = (DATE(y,m,d) +
  TIME(h,mi,s))` **and** `TREATAS({(DATE(...)+TIME(...))}, 'Timepoints'[Timepoint])`. The MPARAMETER
  alone is sufficient for our queries (the runner's `--timepoint` sets it).

Item History (DirectQuery, needs `CapacitiesList`; the three History params below are optional):

- **Item History Main**: `ArtifactName` (item name), `OperationName`, `Experience`, `ArtifactKind`,
  `Billing type`, `WorkspaceName`, `UtilizationType`, `ItemHistoryUniquKey`.
- **Item History Operation**: `Day`, `CU (s)`, `Duration (s)`, `Throttling (s)`, `Operations`,
  `ItemHistoryUniquKey` (join key to Main). Per item/operation/day aggregates.
- **Item History Operation Detail**: per-operation rows with `WindowStartTime`/`WindowEndTime`,
  `OperationStartTime`/`OperationEndTime`, `Status`, `CU (s)`, `Throttling (s)`, `Operations`.
- Why it matters: this is the **only Capacity Metrics table that carries `Experience`** (AS, ML,
  Kusto, ES, SparkCore, lake, SQLDb) and **throttling in seconds**, per item, operation, and day.
  It does the job of `Metrics By Item And Operation` + Chargeback's experience grain in one query.
- Optional narrowing (list MParameters, each `TREATAS`'d onto its slicer-list table): `MPARAMETER
  'WorkspaceIDHistory' = {"<ws-guid>", ...}` -> `'Item History Workspace List'[WorkspaceId]`;
  `MPARAMETER 'OperationNameHistory' = {"AI Query", ...}` -> `'Item History Operation Name List'
  [OperationName]`; `MPARAMETER 'UsernameHistory' = {"user@x"}` -> `'Item History User List'[user]`.
  Works with just `CapacitiesList` if you do not need to narrow.

Overage / billed (measures, only `CapacitiesList`): `[Processed overage]`,
`[Processed overage over 24hours CUhr]`, `[Overage billing limit CUhr]` give the billed-overage
CU-hours (the Compute page Overages -> Billed view); `CU Detail[Processed overage]` is the raw series.

Date-range filter idiom (from the report): `FILTER(KEEPFILTERS(VALUES('Dates'[Date])),
AND('Dates'[Date] >= DATE(2026,6,10), 'Dates'[Date] < DATE(2026,6,30)))`, grouping trends by
`'Datetime'[Date]`. For a relative window, filter the fact directly:
`FILTER('Metrics By Item And Day', 'Metrics By Item And Day'[Date] >= TODAY() - 7)` (see
`cu-by-item-last-n-days.dax`). `TODAY()` evaluates in the model's timezone, not the caller's.

Health measures (in the disconnected **All Measures** table; call by name, no parameter):

- `[Count of capacities]`, and per-capacity: `[Risk status by capacity (last 24 hours)]`,
  `[Average utilization by capacity (last 24 hours)]`, `[Throttling(s) by capacity (last 24 hours)]`,
  `[P95 interactive delay by capacity (last 24 hours)]`, `[P95 interactive rejection by capacity
  (last 24 hours)]`, `[P95 background rejection by capacity (last 24 hours)]`, `[Usage variance by
  capacity (last 24 hours)]`, `[Users by capacity (last 24 hours)]`, and operation-status counts
  `[Rejected/Failed/Successful/... operations by capacity (last 24 hours)]`. Each has a `(last 1
  hour)` twin, most also have a `(last 7 days)` variant (e.g. `[Average utilization by capacity
  (last 7 days)]`), and a bare (single selected capacity) form.

## Chargeback: key tables and columns

- **Chargeback** (the single imported fact): `Capacity Id`, `Date`, `Item Id`, `Workspace Id`,
  `User`, `Operation name`, `Experience`, `Billing type`, `CU (s)`, `Duration (s)`, `Operations`,
  `Domain unique key`.
- **Items**: `Item Id`, `Item name`, `Item kind`. NOTE: no `Workspace name` here; get it from
  **Workspaces**[`Workspace name`].
- **Workspaces**: `Workspace Id`, `Workspace name`. **Capacities**: `Capacity Id`, `Capacity name`,
  `SKU`, `Region`, `Core count`. **Domains**: `Domain`, `Subdomain`. **Dates**: full calendar.

## Gotchas

- **Throttling units differ**: `Throttling (min)` on the aggregate tables; `Throttling (s)` on the
  timepoint detail and on the health measures.
- **`CU Detail` percentages**: delay/rejection `%` columns are fractions where `> 1.0` means the
  capacity crossed that throttling threshold for the window.
- **Timezone**: all timestamps in the model (`CU Detail`[Window start time], the `[Date]`/`[Day]`
  grains, `System Events` transition times, `TODAY()`) are in the model's clock, not the caller's
  local time. Pass a `--timepoint` exactly as `Window start time` shows it (no local conversion) or
  the drill returns no rows. For a precise sub-day slice use the timepoint drill, not a `[Date]` filter.
- **Resolve item names in timepoint detail**: the detail tables carry the item GUID in `[Item]`; join
  to `Items` (there is a live relationship) to get `Item name` and `Item kind`.
- **User identity (EUII)**: some rows wrap the user as `<euii>name</euii>`, governed by the tenant
  "Show user data in the Fabric Capacity Metrics app" admin setting. Strip the tags if you display.
- **Scope each call**: each request returns at most 100k rows. The aggregates are tiny; only the raw
  timepoint detail is large, and even there each window is capped, so query one window at a time.
- **Chargeback Items has no workspace name** (use the Workspaces table) and adds a Domain/Subdomain
  grain that Capacity Metrics lacks.
- **The workspace name can drift.** Reinstalling or reassigning the app can append a timestamp to the
  workspace name (e.g. `Microsoft Fabric Capacity Metrics 6/28/2026 4:44:46 PM`). Resolve the model
  by the stable `Fabric Capacity Metrics` dataset name, and if the workspace lookup misses, fall back
  to its workspace ID.
