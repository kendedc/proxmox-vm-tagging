"""Unit tests for the tag_plan filter — the piece that decides what gets written."""

from __future__ import annotations

import pathlib
import sys

import pytest

ROLE = pathlib.Path(__file__).resolve().parent.parent / "roles" / "proxmox_tagging"
sys.path.insert(0, str(ROLE / "filter_plugins"))

from tag_plan import normalize_tag, split_tags, tag_plan  # noqa: E402

CFG = {
    "vmid_column": "VMID",
    "name_column": "Name",
    "tags_column": "Tags",
    "tag_columns": {"Environment": "env", "Owner": "owner"},
}


def guest(vmid, name, tags, node="pve01", gtype="qemu"):
    return {"vmid": vmid, "name": name, "tags": tags, "node": node, "type": gtype}


class TestNormalizeTag:
    def test_spaces_become_separators(self):
        assert normalize_tag("App Team") == "app-team"

    def test_illegal_characters_are_replaced(self):
        assert normalize_tag("prod/eu:west") == "prod-eu-west"

    def test_leading_junk_is_stripped(self):
        assert normalize_tag("--prod") == "prod"

    def test_case_is_preserved_when_asked(self):
        assert normalize_tag("Prod", lowercase=False) == "Prod"

    def test_empty_input_yields_none(self):
        assert normalize_tag("   ") is None


class TestSplitTags:
    def test_multiple_delimiters_and_dedupe(self):
        assert split_tags("web; api,web|db") == ["api", "db", "web"]

    def test_blank_cell(self):
        assert split_tags("") == []


class TestTagPlan:
    def test_detects_a_drifted_guest(self):
        rows = [{"_row": 2, "VMID": "100", "Environment": "Production", "Tags": "web"}]
        plan = tag_plan(rows, [guest(100, "web-01", "web")], **CFG)

        assert plan["stats"]["changed"] == 1
        change = plan["changes"][0]
        assert change["added"] == ["env-production"]
        assert change["removed"] == []
        assert change["desired_tags"] == ["env-production", "web"]

    def test_already_correct_guest_is_not_changed(self):
        rows = [{"_row": 2, "VMID": "100", "Environment": "Production", "Tags": "web"}]
        plan = tag_plan(rows, [guest(100, "web-01", "env-production;web")], **CFG)

        assert plan["stats"]["changed"] == 0
        assert plan["stats"]["unchanged"] == 1

    def test_replace_mode_removes_stale_tags(self):
        rows = [{"_row": 2, "VMID": "100", "Tags": "web"}]
        plan = tag_plan(rows, [guest(100, "web-01", "web;decommissioned")], **CFG)

        assert plan["changes"][0]["removed"] == ["decommissioned"]

    def test_merge_mode_never_removes(self):
        rows = [{"_row": 2, "VMID": "100", "Tags": "web"}]
        plan = tag_plan(
            rows, [guest(100, "web-01", "web;decommissioned")], mode="merge", **CFG
        )

        assert plan["stats"]["changed"] == 0

    def test_protected_prefixes_survive_replace(self):
        rows = [{"_row": 2, "VMID": "100", "Tags": "web"}]
        plan = tag_plan(
            rows,
            [guest(100, "web-01", "web;monitoring-critical")],
            protected_prefixes=["monitoring-"],
            **CFG,
        )

        assert plan["stats"]["changed"] == 0

    def test_row_matching_no_guest_is_reported(self):
        rows = [{"_row": 2, "VMID": "999", "Tags": "web"}]
        plan = tag_plan(rows, [guest(100, "web-01", "")], **CFG)

        assert plan["missing_in_proxmox"] == [{"row": 2, "identifier": "999"}]
        assert plan["stats"]["changed"] == 0

    def test_guest_absent_from_sheet_is_left_alone(self):
        rows = [{"_row": 2, "VMID": "100", "Tags": "web"}]
        plan = tag_plan(rows, [guest(100, "web-01", "web"), guest(101, "web-02", "web")], **CFG)

        assert [item["vmid"] for item in plan["unmanaged"]] == [101]

    def test_name_fallback_when_vmid_is_blank(self):
        rows = [{"_row": 2, "VMID": "", "Name": "WEB-01", "Tags": "web"}]
        plan = tag_plan(rows, [guest(100, "web-01", "")], **CFG)

        assert plan["changes"][0]["vmid"] == 100

    def test_ambiguous_name_is_rejected(self):
        rows = [{"_row": 2, "VMID": "", "Name": "web-01", "Tags": "web"}]
        plan = tag_plan(rows, [guest(100, "web-01", ""), guest(101, "web-01", "")], **CFG)

        assert plan["invalid_rows"][0]["row"] == 2

    def test_non_numeric_vmid_is_rejected(self):
        rows = [{"_row": 2, "VMID": "one hundred", "Tags": "web"}]
        plan = tag_plan(rows, [guest(100, "web-01", "")], **CFG)

        assert "not a number" in plan["invalid_rows"][0]["reason"]

    def test_duplicate_vmid_is_rejected(self):
        rows = [
            {"_row": 2, "VMID": "100", "Tags": "web"},
            {"_row": 3, "VMID": "100", "Tags": "db"},
        ]
        plan = tag_plan(rows, [guest(100, "web-01", "")], **CFG)

        assert plan["invalid_rows"][0]["row"] == 3

    def test_lxc_guests_keep_their_type(self):
        rows = [{"_row": 2, "VMID": "200", "Tags": "ct"}]
        plan = tag_plan(rows, [guest(200, "ct-01", "", gtype="lxc")], **CFG)

        assert plan["changes"][0]["type"] == "lxc"

    def test_invalid_mode_raises(self):
        with pytest.raises(Exception):
            tag_plan([], [], mode="upsert")

    def test_stats_percentage(self):
        rows = [{"_row": 2, "VMID": "100", "Tags": "web"}]
        plan = tag_plan(rows, [guest(100, "web-01", ""), guest(101, "web-02", "")], **CFG)

        assert plan["stats"]["changed_pct"] == 50.0
