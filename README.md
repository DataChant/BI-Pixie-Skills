# BI Pixie Skills

An open-source collection of AI-agent **skills** for Power BI and Microsoft Fabric, from the
team behind [BI Pixie](https://bipixie.com). Each skill pairs a common Fabric or Power BI task
with BI Pixie's perspective on how content is actually used, so an AI agent in VS Code (Claude Code,
GitHub Copilot, Cursor, Codex) can answer real questions about usage, engagement, and cost.

The skills layer on top of Microsoft's official
[skills-for-fabric](https://github.com/microsoft/skills-for-fabric): use the official skills for
Fabric CLI sign-in and workspace discovery, and these for the analysis on top. Everything here is
read-only, runs from a **Power BI Pro** license, and needs no Premium, no XMLA endpoint, and no
notebook.

Division of labor: these skills cover the **capacity** models (Capacity Metrics, Chargeback), which
have no first-party tool. For BI Pixie's **own** engagement data, use the
[BI Pixie MCP server](https://pypi.org/project/bipixie-mcp/) (typed, read-only, public preview). The
`fabric-cu-roi` skill joins the two: capacity cost from here, report engagement from the MCP server.

## Skills

| Skill | What it does |
|-------|--------------|
|[`query-fabric-capacity-cu`](plugins/fabric-capacity/skills/query-fabric-capacity-cu/SKILL.md) | Read Fabric Capacity Unit (CU) usage per item, operation, and user from the **Capacity Metrics** and **Chargeback** apps via the Execute Queries DAX REST API. Works on a Pro workspace. Includes **what a Fabric data agent question costs** in CU seconds. |
|[`diagnose-fabric-capacity`](plugins/fabric-capacity/skills/diagnose-fabric-capacity/SKILL.md) | Answer almost any admin question about a capacity: **health and risk**, **throttling and overload**, utilization vs limit, overage, **blocked workspaces and affected users**, storage, the capacity state timeline, and a 30-second **timepoint drill** that names the exact items, operations, and users that consumed CU. |
|[`fabric-cu-roi`](plugins/fabric-capacity/skills/fabric-cu-roi/SKILL.md) | Join CU cost with report usage to surface **low-ROI** content (high CU, low interactions). |

All three skills ship in the **`fabric-capacity`** plugin, alongside a bundled
[model guide](plugins/fabric-capacity/references/capacity-model-guide.md) (full schema + a
question-to-table map), a stdlib [DAX runner](plugins/fabric-capacity/scripts/run_dax.py), and a set
of ready-to-run [example queries](plugins/fabric-capacity/examples/).

## Install

This repo is a plugin marketplace for both **Claude Code** and **GitHub Copilot CLI**. The commands
are the same in either tool. Add the marketplace, then install the plugin:

```
/plugin marketplace add DataChant/BI-Pixie-Skills
/plugin install fabric-capacity@bi-pixie-skills
```

The skills are also plain [`SKILL.md`](https://agentskills.io/specification) files, so they work
with GitHub Copilot, Codex, Cursor, or any other SKILL-aware agent.

**Copy the whole `plugins/fabric-capacity/` directory, not just a skill folder.** The three skills
share one `scripts/run_dax.py`, one `references/capacity-model-guide.md` and one `examples/` set,
and those sit beside `skills/` rather than inside each skill, so a lone skill folder arrives without
its runner, its model guide or any of its queries.

| Agent | Where it looks |
|-------|----------------|
| Claude Code | the plugin install above, or `.claude/skills/` |
| GitHub Copilot | the plugin install above, or `.github/skills/` / `~/.copilot/skills/` |
| Codex / Cursor | the agent's own skills directory |

The commands in each skill refer to `${CLAUDE_PLUGIN_ROOT}`, which only Claude Code sets. Outside a
plugin install, point it at wherever you copied `fabric-capacity`, or substitute that path:

```bash
export CLAUDE_PLUGIN_ROOT=~/.copilot/skills/fabric-capacity   # adjust to where you copied it
```

## Prerequisites

- Microsoft [skills-for-fabric](https://github.com/microsoft/skills-for-fabric) installed in your
  agent (provides Fabric CLI auth, workspace/dataset discovery, generic DAX execution).
- The Azure CLI (`az login`) or the [Fabric CLI](https://aka.ms/fabric-cli) (`fab auth login`),
  signed in as a **capacity admin**. The bundled runner uses whichever you have (auto-detect;
  `fab` adds native name resolution).
- The relevant data source for the skill (for the capacity skills: the **Microsoft Fabric Capacity
  Metrics** and/or **Chargeback** app installed; the tenant setting "Semantic model Execute Queries
  REST API" enabled).

## How it works (in one paragraph)

The Capacity Metrics and Chargeback apps publish semantic models you can query read-only with
`POST /v1.0/myorg/datasets/{datasetId}/executeQueries`. The Capacity Metrics model gates its
DirectQuery fact tables behind a `CapacitiesList` Power Query parameter that you set with a
`DEFINE MPARAMETER` prefix (the one detail that is easy to get wrong), plus a `TimePoint` parameter
for the 30-second drill. Its cross-capacity **health** measures need no capacity parameter, but they
read one region at a time through a `RegionName` parameter, so a capacity outside the region being
read looks healthy and idle; the bundled runner reads every region your capacities are in. The
Chargeback model is fully imported, so a plain `EVALUATE` works and it adds per-user,
per-experience, and per-domain grain. Always resolve the models by **name** (the GUIDs rotate on
reinstall). See each skill for copy-paste queries, or the
[model guide](plugins/fabric-capacity/references/capacity-model-guide.md) for the full map.

## BI Pixie MCP server (the engagement side)

The [`fabric-cu-roi`](plugins/fabric-capacity/skills/fabric-cu-roi/SKILL.md) skill joins capacity
**cost** (from these skills) with report **engagement** (which costs you the most CU for the least
usage?). The engagement half comes from the BI Pixie MCP server, a published, read-only package, so
there is nothing to host or register. Install it once:

```bash
# Zero-install runner (recommended); first run launches a guided setup:
uvx bipixie-mcp

# Or install into the current environment:
pip install bipixie-mcp
```

[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_BI_Pixie_MCP-0098FF?logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=bipixie&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22bipixie-mcp%22%5D%7D)

The first run signs you in, lets you pick your **BI Pixie** semantic model, and writes a ready-to-paste
`.mcp.json` block. Full setup guide: [bipixie.com/docs/cloud/mcp-server](https://bipixie.com/docs/cloud/mcp-server/).
The MCP server is the supported interface to BI Pixie's own model; these skills cover the **capacity**
models, which have no first-party tool.

## Compatible agents

Installable as a marketplace plugin in **Claude Code** and **GitHub Copilot CLI** (same
`/plugin` commands). The skills themselves are plain `SKILL.md` files with REST + DAX patterns, so
they also adapt directly to Cursor, Codex, or any SKILL-aware client.

## A note on scope

The capacity skills read the Microsoft Capacity Metrics and Chargeback semantic models, which
Microsoft documents as supported only for their built-in reports (preview, unsupported for direct
query). Treat the technique as best-effort: resolve by name, expect schema drift between app
versions, and persist what you extract if you need history beyond the apps' retention (about 14 days
for Capacity Metrics compute, about 30 days for Chargeback).

## License

[MIT](LICENSE). Built by [Gil Raviv](https://www.linkedin.com/in/gilraviv),
[DataChant Consulting](https://bipixie.com/about).
