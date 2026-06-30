---
name: fabric-cu-roi
description: >
  Combine Microsoft Fabric Capacity Unit (CU) cost with report usage and engagement to find low-ROI
  content: items that cost a lot of capacity but get few interactions or users.
  Use when the user asks which reports or items are expensive but underused, cost per session or per
  user, what to optimize or decommission, or to rank content by cost efficiency.
  Triggers: "low ROI reports", "expensive but unused", "cost per session", "cost per user",
  "which reports to decommission", "wasteful capacity", "CU vs usage", "cost efficiency".
  Get the cost side with `query-fabric-capacity-cu` and the engagement side from the BI Pixie MCP
  server (or any usage source). For raw CU figures alone, use `query-fabric-capacity-cu`.
allowed-tools: Bash
---

# Fabric CU ROI (cost vs engagement)

CU tells you what something **costs**. Usage tells you what it is **worth**. This skill joins the two
so an agent can answer "what is expensive but barely used?" The signal: **high CU + low interactions
= low ROI** (a candidate to optimize, right-size, or retire).

Use this together with [`query-fabric-capacity-cu`](../query-fabric-capacity-cu/SKILL.md) (the cost
side) and an engagement source (the value side).

## Step 1: Get the cost side (CU per item)

Use `query-fabric-capacity-cu` to pull CU per item over a window, keyed by item name and workspace.
Chargeback is ideal (per-user, ~30 days). Keep `Item name`, `Workspace name`, and `CU_s`. That skill
bundles `scripts/run_dax.py` (resolve-by-name plus `DEFINE MPARAMETER` handling) for this step.

## Step 2: Get the value side (engagement per report)

You need per-report usage: sessions, interactions, and distinct users. **Prefer the BI Pixie MCP
server** ([`bipixie-mcp`](https://pypi.org/project/bipixie-mcp/), public preview) for this. It
exposes typed, read-only tools over the "BI Pixie" semantic model, stays in sync with the model, and
runs in any MCP client. Install it once (there is a one-click "Install in VS Code" button on the BI
Pixie portal Overview at [app.bipixie.com](https://app.bipixie.com)) and ask the agent for per-report
engagement directly. No DAX to maintain.

Why the MCP server rather than raw DAX here: it is the supported, guarded interface to the BI Pixie
model. These skills exist for the **capacity** models, which have no first-party tool; BI Pixie's own
data already has one. So: MCP server for engagement, these skills for capacity, joined in Step 3.

Fallback (no MCP server): query the "BI Pixie" semantic model directly with read-only
`executeQueries`, the same way as the cost side, using measures like `[Report Sessions]`,
`[Total Interactions]`, `[Users]`:

```dax
EVALUATE
SUMMARIZECOLUMNS(
  'Reports'[Report Name],
  "Sessions", [Report Sessions],
  "Interactions", [Total Interactions Within Page],
  "Users", [Users]
)
```

This fallback query is bundled as `${CLAUDE_PLUGIN_ROOT}/examples/bi-pixie-engagement-by-report.dax`
(adjust the measure names to your model). Any usage telemetry works (Workspace Monitoring, custom
logs); you just need sessions/interactions per report to join on the report name.

## Step 3: Join and score

Join the two result sets on report/item name (normalize case and trim workspace suffixes). Then
compute simple, explainable signals:

- **CU per session** = `CU_s / Sessions` (cost efficiency; higher is worse).
- **CU per active user** = `CU_s / Users`.
- **Low-ROI flag** = high CU percentile AND low interactions/sessions percentile.

Example rule of thumb for a flag:

```
low_roi = (CU_s >= p75 of CU_s) AND (Sessions <= p25 of Sessions OR Interactions <= p25 of Interactions)
```

Surface the worst offenders: items in the top quartile of CU that are in the bottom quartile of
engagement. Those are the reports paying for capacity they barely earn.

## Step 4: Report it

Return a ranked table: `Item / Report | CU_s | Sessions | Interactions | Users | CU per session | ROI flag`,
sorted by CU per session descending. Call out:
- **Expensive and unused** (high CU, ~0 sessions): candidates to pause, decommission, or move off the
  capacity.
- **Expensive and passive** (high CU, sessions but few interactions): candidates to redesign (heavy
  refresh or visuals, low actual engagement).
- **Cheap and loved** (low CU, high engagement): your efficient content; leave alone.

## Notes

- Match the time windows on both sides (for example trailing 14 or 30 days) so the ratio is fair.
- Item names can collide across workspaces; include the workspace when joining.
- Aggregate the data agent and SQL-endpoint kinds per logical item, or you will undercount cost
  (one logical item can appear under multiple `Item kind` values).
- This is a cost-efficiency heuristic, not an accounting system; pair it with judgment about
  business-critical-but-low-traffic content (board reports, compliance dashboards).
