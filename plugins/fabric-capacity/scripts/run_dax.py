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
"""
import argparse, json, re, shutil, subprocess, sys, tempfile, os, urllib.request, urllib.error

GUID = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
TIMEPOINT = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?$")
PBI = "https://api.powerbi.com/v1.0/myorg"
PBI_RESOURCE = "https://analysis.windows.net/powerbi/api"


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


def run_via_fab(ws, ds, dax):
    def items(raw):  # normalize fab's displayName -> name
        return [{"name": x.get("displayName", ""), "id": x.get("id")} for x in raw]
    ws_id = resolve("Workspace", items(fab_json("api", "workspaces").get("text", {}).get("value", [])), ws)
    ds_id = resolve("Semantic model",
                    items(fab_json("api", "workspaces/%s/items?type=SemanticModel" % ws_id)
                          .get("text", {}).get("value", [])), ds)
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


def run_via_az(ws, ds, dax):
    token = az_token()
    if GUID.match(ds):
        ds_id = ds
    else:
        if GUID.match(ws):
            ws_id = ws
        else:
            ws_id = resolve("Workspace", pbi("groups", token).get("value", []), ws)
        ds_id = resolve("Semantic model", pbi("groups/%s/datasets" % ws_id, token).get("value", []), ds)
    body = {"queries": [{"query": dax}], "serializerSettings": {"includeNulls": True}}
    return pbi("datasets/%s/executeQueries" % ds_id, token, body)["results"][0]["tables"][0]["rows"]


def main():
    ap = argparse.ArgumentParser(description="Run read-only DAX via az or fab + executeQueries.")
    ap.add_argument("--workspace", required=True, help="Workspace name or id")
    ap.add_argument("--dataset", required=True, help="Semantic model name or id")
    ap.add_argument("--cli", choices=["auto", "az", "fab"], default="auto",
                    help="Which CLI to use for auth (default auto: fab if installed, else az)")
    ap.add_argument("--capacity", help="Capacity GUID; adds MPARAMETER 'CapacitiesList' (Capacity Metrics)")
    ap.add_argument("--region", help="Capacity region, e.g. 'East US'; adds MPARAMETER 'RegionName'. "
                                     "Needed for capacities outside your home/default region.")
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

    # Fold every requested M parameter into a single DEFINE block (DAX allows only one).
    params = []
    if a.region:
        params.append("MPARAMETER 'RegionName' = \"%s\"" % a.region)
    if a.capacity:
        params.append("MPARAMETER 'CapacitiesList' = { \"%s\" }" % a.capacity)
    if a.timepoint:
        params.append("MPARAMETER 'TimePoint' = %s" % timepoint_expr(a.timepoint))
    if params:
        dax = "DEFINE\n" + "\n".join(params) + "\n" + dax

    cli = a.cli
    if cli == "auto":
        cli = "fab" if shutil.which("fab") else "az"
    rows = run_via_fab(a.workspace, a.dataset, dax) if cli == "fab" \
        else run_via_az(a.workspace, a.dataset, dax)

    if a.json or not rows:
        print(json.dumps(rows, indent=2, default=str))
        return
    cols = list(rows[0].keys())
    hdr = [c.strip("[]").split("[")[-1] for c in cols]
    width = [max(len(h), *(len(str(r.get(c, ""))) for r in rows)) for h, c in zip(hdr, cols)]
    fmt = lambda vals: "  ".join(str(v).ljust(w) for v, w in zip(vals, width))
    print(fmt(hdr))
    print(fmt(["-" * w for w in width]))
    for r in rows:
        print(fmt([r.get(c, "") for c in cols]))
    print("\n%d row(s)" % len(rows))


if __name__ == "__main__":
    main()
