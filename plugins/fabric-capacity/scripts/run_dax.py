#!/usr/bin/env python3
"""Run read-only DAX against a Power BI / Fabric semantic model via the Execute Queries REST API.

Works with EITHER CLI you already have, no pip installs:
  - Azure CLI (`az login`)        -- ubiquitous; the default when `fab` is not installed.
  - Fabric CLI (`fab auth login`) -- used when present (native workspace/dataset name resolution).
Choose explicitly with `--cli {auto,az,fab}` (default: auto = fab if installed, else az).

Resolves the workspace and dataset by name OR id, optionally injects the `DEFINE MPARAMETER`
prefixes the Capacity Metrics model needs, and prints the rows.

Examples:
  # Chargeback (imported, no parameter):
  python run_dax.py --workspace "Microsoft Fabric Chargeback Reporting" \
    --dataset "Fabric Chargeback Reporting" --dax-file query.dax

  # Capacity Metrics (DirectQuery) -- --capacity adds the required DEFINE MPARAMETER prefix:
  python run_dax.py --workspace "Microsoft Fabric Capacity Metrics" \
    --dataset "Fabric Capacity Metrics" --capacity <guid> --dax-file query.dax

  # Health of every capacity -- --all-regions runs the query once per capacity region:
  python run_dax.py --workspace "Microsoft Fabric Capacity Metrics" \
    --dataset "Fabric Capacity Metrics" --all-regions --dax-file capacity-health-overview.dax
"""
import argparse, json, re, shutil, subprocess, sys, tempfile, os, urllib.request, urllib.error

GUID = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
TIMEPOINT = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?$")
PBI = "https://api.powerbi.com/v1.0/myorg"
PBI_RESOURCE = "https://analysis.windows.net/powerbi/api"

# The cross-capacity health measures ("Risk status by capacity (last 24 hours)" and the rest of
# that family) answer for ONE region per query: MPARAMETER 'RegionName', which defaults to the home
# region. A capacity anywhere else reads Healthy, 0% utilization and no users while it throttles.
# Measured 2026-09-15 on a Central US F8: Healthy and 0 s of throttling with the parameter unset,
# Throttling and 3,700 s with RegionName = "Central US". So a query that uses these measures reads
# every region unless the caller names one with --region.
HEALTH_MEASURE = re.compile(r"\bby capacity \(", re.IGNORECASE)
HAS_DEFINE = re.compile(r"^\s*DEFINE\b", re.IGNORECASE | re.MULTILINE)


def timepoint_expr(s):
    """Turn an ISO timepoint (2026-06-27T12:12:30) into a DAX datetime expression."""
    m = TIMEPOINT.match(s.strip())
    if not m:
        sys.exit("--timepoint must look like 2026-06-27T12:12:30 (30-second window start)")
    y, mo, d, h, mi, se = (int(g or 0) for g in m.groups())
    return "(DATE(%d,%d,%d) + TIME(%d,%d,%d))" % (y, mo, d, h, mi, se)


def resolve(kind, listing, name):
    """Return an id given a name-or-id. Exact (case-insensitive) match, else a unique
    startswith match (handles workspace names that gain a timestamp suffix on reinstall)."""
    if GUID.match(name):
        return name
    low = name.lower()
    exact = [x for x in listing if (x.get("name") or "").lower() == low]
    if len(exact) == 1:
        return exact[0]["id"]
    pre = [x for x in listing if (x.get("name") or "").lower().startswith(low)]
    if not pre:
        sys.exit("%s not found by name: %s" % (kind, name))
    if len(pre) > 1:
        sys.exit("Multiple %ss match %s; pass the id instead." % (kind, name))
    return pre[0]["id"]


def column_name(key):
    """'Capacities[Region]' and '[Region]' both become 'Region'."""
    return key.strip("[]").split("[")[-1]


# ---------- Fabric CLI (`fab`) path ----------
def fab(*args):
    r = subprocess.run(["fab", *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("fab %s failed:\n%s" % (" ".join(args), r.stderr or r.stdout))
    return r.stdout


def fab_json(*args):
    try:
        return json.loads(fab(*args))
    except json.JSONDecodeError:
        sys.exit("Could not parse fab output as JSON.")


def fab_executor(ws, ds):
    """Resolve the model once; return a function that runs one DAX query and returns its rows."""
    def items(raw):  # normalize fab's displayName -> name
        return [{"name": x.get("displayName", ""), "id": x.get("id")} for x in raw]
    ws_id = resolve("Workspace", items(fab_json("api", "workspaces").get("text", {}).get("value", [])), ws)
    ds_id = resolve("Semantic model",
                    items(fab_json("api", "workspaces/%s/items?type=SemanticModel" % ws_id)
                          .get("text", {}).get("value", [])), ds)

    def execute(dax):
        body = {"queries": [{"query": dax}], "serializerSettings": {"includeNulls": True}}
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        try:
            json.dump(body, tmp); tmp.close()
            resp = fab_json("api", "-A", "powerbi", "-X", "post",
                            "datasets/%s/executeQueries" % ds_id, "-i", tmp.name)
        finally:
            os.unlink(tmp.name)
        if resp.get("status_code") not in (200, None):
            sys.exit("executeQueries failed [%s]:\n%s" % (resp.get("status_code"),
                                                          json.dumps(resp.get("text"))[:800]))
        return resp["text"]["results"][0]["tables"][0]["rows"]
    return execute


# ---------- Azure CLI (`az`) path ----------
def az_token():
    cmd = 'az account get-access-token --resource "%s" --query accessToken -o tsv' % PBI_RESOURCE
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("az token failed (run `az login`):\n%s" % (r.stderr or r.stdout))
    return r.stdout.strip()


def pbi(path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(PBI + "/" + path, data=data,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit("Power BI REST %s failed [%s]:\n%s" % (path, e.code, e.read().decode()[:800]))


def az_executor(ws, ds):
    """Resolve the model once; return a function that runs one DAX query and returns its rows."""
    token = az_token()
    if GUID.match(ds):
        ds_id = ds
    else:
        if GUID.match(ws):
            ws_id = ws
        else:
            ws_id = resolve("Workspace", pbi("groups", token).get("value", []), ws)
        ds_id = resolve("Semantic model", pbi("groups/%s/datasets" % ws_id, token).get("value", []), ds)

    def execute(dax):
        body = {"queries": [{"query": dax}], "serializerSettings": {"includeNulls": True}}
        return pbi("datasets/%s/executeQueries" % ds_id, token, body)["results"][0]["tables"][0]["rows"]
    return execute


# ---------- Capacity Metrics parameters and regions ----------
def with_params(dax, region=None, capacity=None, timepoint=None):
    """Fold every requested M parameter into a single DEFINE block (DAX allows only one).
    `timepoint` is the DAX datetime expression from timepoint_expr()."""
    params = []
    if region:
        params.append("MPARAMETER 'RegionName' = \"%s\"" % region.replace('"', '""'))
    if capacity:
        params.append("MPARAMETER 'CapacitiesList' = { \"%s\" }" % capacity)
    if timepoint:
        params.append("MPARAMETER 'TimePoint' = %s" % timepoint)
    return "DEFINE\n" + "\n".join(params) + "\n" + dax if params else dax


def capacity_regions(execute, capacity=None):
    """The distinct Capacities[Region] values, or only that capacity's when --capacity is set.
    DISTINCT rather than VALUES, so the model's blank unknown-member row is not read as a region."""
    regions = "DISTINCT(Capacities[Region])"
    if capacity:
        regions = 'CALCULATETABLE(%s, Capacities[Capacity Id] = "%s")' % (regions, capacity)
    rows = execute("EVALUATE " + regions)
    return sorted({(next(iter(r.values()), None) or "") for r in rows})


def read_every_region(execute, dax, capacity=None, timepoint=None):
    """Run the query once per capacity region and keep each capacity's rows from the read of its
    OWN region. Under a region that is not its own, a capacity's health measures can answer zero,
    and a zero reads as Healthy, so the reads are never merged by value."""
    regions = capacity_regions(execute, capacity)
    if not regions:
        sys.exit("Found no capacity%s in the Capacities table, so there is no region to read."
                 % (" matching --capacity" if capacity else ""))
    merged = []
    for region in regions:
        print("Reading region: %s" % (region or "(blank, the model's default)"), file=sys.stderr)
        rows = execute(with_params(dax, region or None, capacity, timepoint))
        if not rows:
            continue
        col = next((k for k in rows[0] if column_name(k).lower() == "region"), None)
        if col is None:
            if len(regions) > 1:
                sys.exit("--all-regions keeps each capacity's row from the read of its own region, "
                         "so the query must return Capacities[Region]. Add that column, or pass "
                         "--region to read a single region.")
            merged.extend(rows)
        else:
            merged.extend(r for r in rows if (r.get(col) or "") == region)
    return merged


def main():
    ap = argparse.ArgumentParser(description="Run read-only DAX via az or fab + executeQueries.")
    ap.add_argument("--workspace", required=True, help="Workspace name or id")
    ap.add_argument("--dataset", required=True, help="Semantic model name or id")
    ap.add_argument("--cli", choices=["auto", "az", "fab"], default="auto",
                    help="Which CLI to use for auth (default auto: fab if installed, else az)")
    ap.add_argument("--capacity", help="Capacity GUID; adds MPARAMETER 'CapacitiesList' (Capacity Metrics)")
    where = ap.add_mutually_exclusive_group()
    where.add_argument("--region", help="Read ONE capacity region, e.g. 'East US'; adds MPARAMETER "
                                        "'RegionName'. Unset, the health measures read only your "
                                        "home region.")
    where.add_argument("--all-regions", action="store_true",
                       help="Run the query once per capacity region and keep each capacity's rows "
                            "from the read of its own region (the query must return "
                            "Capacities[Region]). On by default for queries that use the "
                            "'... by capacity (...)' health measures.")
    ap.add_argument("--timepoint", help="30-sec window start, e.g. 2026-06-27T12:12:30; adds "
                                        "MPARAMETER 'TimePoint' for the timepoint-detail tables")
    ap.add_argument("--dax", help="Inline DAX EVALUATE statement")
    ap.add_argument("--dax-file", help="Path to a file with a DAX EVALUATE statement")
    ap.add_argument("--json", action="store_true", help="Print raw rows as JSON")
    a = ap.parse_args()

    dax = a.dax
    if a.dax_file:
        with open(a.dax_file, encoding="utf-8") as f:
            dax = f.read()
    if not dax:
        ap.error("provide --dax or --dax-file")
    timepoint = timepoint_expr(a.timepoint) if a.timepoint else None

    all_regions = a.all_regions
    if not all_regions and not a.region and HEALTH_MEASURE.search(dax) and not HAS_DEFINE.search(dax):
        all_regions = True
        print("This query uses the '... by capacity' health measures, which answer for one region "
              "per query, so every capacity region is read in turn. Pass --region to read one.",
              file=sys.stderr)

    cli = a.cli
    if cli == "auto":
        cli = "fab" if shutil.which("fab") else "az"
    execute = fab_executor(a.workspace, a.dataset) if cli == "fab" \
        else az_executor(a.workspace, a.dataset)
    if all_regions:
        rows = read_every_region(execute, dax, a.capacity, timepoint)
    else:
        rows = execute(with_params(dax, a.region, a.capacity, timepoint))

    if a.json or not rows:
        print(json.dumps(rows, indent=2, default=str))
        return
    cols = list(rows[0].keys())
    hdr = [column_name(c) for c in cols]
    width = [max(len(h), *(len(str(r.get(c, ""))) for r in rows)) for h, c in zip(hdr, cols)]
    fmt = lambda vals: "  ".join(str(v).ljust(w) for v, w in zip(vals, width))
    print(fmt(hdr))
    print(fmt(["-" * w for w in width]))
    for r in rows:
        print(fmt([r.get(c, "") for c in cols]))
    print("\n%d row(s)" % len(rows))


if __name__ == "__main__":
    main()
