"""The Terraform plan guard (scripts/tf_guard.py). Offline, on hand-made plan JSON."""

import importlib.util
import sys
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "scripts" / "tf_guard.py"
spec = importlib.util.spec_from_file_location("tf_guard", PATH)
guard = importlib.util.module_from_spec(spec)
sys.modules["tf_guard"] = guard
spec.loader.exec_module(guard)


def rc(address, rtype, actions, importing=False, name=None):
    change = {
        "actions": actions,
        "before": {"name": name} if name else {},
        "after": {"name": name} if name else {},
    }
    if importing:
        change["importing"] = {"id": "x"}
    return {"address": address, "type": rtype, "name": address.split(".")[-1], "change": change}


def test_a_clean_import_passes():
    plan = {
        "resource_changes": [
            rc("snowflake_warehouse.bank_wh", "snowflake_warehouse", ["no-op"], importing=True)
        ]
    }
    problems, counts = guard.check(plan)
    assert problems == [] and counts == {"import": 1, "add": 0, "change": 0, "destroy": 0, "replace": 0}


def test_import_only_blocks_any_change():
    plan = {"resource_changes": [
        rc("snowflake_warehouse.bank_wh", "snowflake_warehouse", ["update"], importing=True),
        rc("snowflake_database.new", "snowflake_database", ["create"]),
    ]}  # fmt: skip
    problems, counts = guard.check(plan)
    assert len(problems) == 2 and counts["change"] == 1 and counts["add"] == 1


def test_any_vm_change_is_blocked_even_with_allow_changes():
    for actions in (["update"], ["delete", "create"], ["delete"]):
        plan = {"resource_changes": [rc("oci_core_instance.vm", "oci_core_instance", actions)]}
        problems, _ = guard.check(plan, allow_changes=True)
        assert problems and "on the VM is never allowed" in problems[0]


def test_only_sandbox_objects_may_be_destroyed():
    sandbox = rc(
        "module.sandbox.snowflake_database.db", "snowflake_database", ["delete"], name="BANK_HEALTH_SANDBOX"
    )
    prod = rc("snowflake_database.bank_health", "snowflake_database", ["delete"], name="BANK_HEALTH")
    assert guard.check({"resource_changes": [sandbox]}, allow_changes=True)[0] == []
    (problem,) = guard.check({"resource_changes": [prod]}, allow_changes=True)[0]
    assert "delete of a non-SANDBOX object" in problem
    replace = rc(
        "snowflake_role.r", "snowflake_account_role", ["delete", "create"], name="BANK_PIPELINE_ROLE"
    )
    assert (
        "replace of a non-SANDBOX object"
        in guard.check({"resource_changes": [replace]}, allow_changes=True)[0][0]
    )


def test_allow_changes_permits_creating_the_sandbox():
    plan = {
        "resource_changes": [
            rc("snowflake_database.sb", "snowflake_database", ["create"], name="BANK_HEALTH_SANDBOX")
        ]
    }
    problems, counts = guard.check(plan, allow_changes=True)
    assert problems == [] and counts["add"] == 1
    assert guard.check(plan)[0]  # but never in import-only mode
