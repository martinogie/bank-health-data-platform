r"""Guard for every Terraform plan (Phase 4). Run it on the saved plan before showing or applying it:

    terraform plan -out tfplan
    terraform show -json tfplan > plan.json
    python scripts/tf_guard.py plan.json                    # import-only (the default)
    python scripts/tf_guard.py plan.json --allow-changes    # the SANDBOX demo only

Exit 0 = the plan may be shown for approval; 1 = blocked (it prints why). Rules:
- import-only (default): imports and no-ops only. Any create, update, delete or replace blocks.
  The first plan for imported prod resources must show 0 to add, 0 to change, 0 to destroy.
- allow-changes: creates and updates allowed; a delete or replace only for objects whose name or
  address contains SANDBOX (decision 8, 3 Nov 2026).
- Always: any action other than no-op/read on the VM (oci_core_instance) or its boot volume and
  network card blocks: nothing may recreate or restart the production VM.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

VM_TYPES = {"oci_core_instance", "oci_core_boot_volume", "oci_core_vnic_attachment"}
QUIET = {("no-op",), ("read",)}


def _is_sandbox(rc: dict) -> bool:
    change = rc.get("change") or {}
    names = [rc.get("address", ""), rc.get("name", "")]
    for side in ("before", "after"):
        values = change.get(side) or {}
        names += [
            str(values.get(k, "")) for k in ("name", "display_name", "database", "fully_qualified_name")
        ]
    return any("SANDBOX" in n.upper() for n in names)


def check(plan: dict, allow_changes: bool = False) -> tuple[list[str], dict[str, int]]:
    """(problems, counts). counts: import, add, change, destroy, replace."""
    counts = dict.fromkeys(("import", "add", "change", "destroy", "replace"), 0)
    problems = []
    for rc in plan.get("resource_changes") or []:
        change = rc.get("change") or {}
        actions = tuple(change.get("actions") or ())
        address, rtype = rc.get("address", "?"), rc.get("type", "")
        if change.get("importing"):
            counts["import"] += 1
        if actions in QUIET:
            continue
        replace = "delete" in actions and "create" in actions
        if replace:
            counts["replace"] += 1
        elif actions == ("create",):
            counts["add"] += 1
        elif actions == ("update",):
            counts["change"] += 1
        elif actions == ("delete",):
            counts["destroy"] += 1
        if rtype in VM_TYPES:
            problems.append(f"{address}: {'/'.join(actions)} on the VM is never allowed")
            continue
        if not allow_changes:
            problems.append(f"{address}: {'/'.join(actions)} (import-only: nothing may change)")
        elif ("delete" in actions) and not _is_sandbox(rc):
            problems.append(f"{address}: {'replace' if replace else 'delete'} of a non-SANDBOX object")
    return problems, counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Block unsafe Terraform plans.")
    parser.add_argument("plan_json")
    parser.add_argument("--allow-changes", action="store_true", help="SANDBOX demo: creates/updates allowed")
    args = parser.parse_args(argv)
    plan = json.loads(Path(args.plan_json).read_text(encoding="utf-8"))
    problems, c = check(plan, args.allow_changes)
    print(
        f"plan: {c['import']} to import, {c['add']} to add, {c['change']} to change, "
        f"{c['destroy']} to destroy, {c['replace']} to replace"
    )
    for p in problems:
        print("BLOCKED:", p)
    print("guard: PASS" if not problems else f"guard: FAIL ({len(problems)} problem(s))")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
