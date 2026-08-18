"""Unit tests for the metadata diff engine — what actually gets written."""

from __future__ import annotations

import pathlib
import sys

import pytest

ROLE = pathlib.Path(__file__).resolve().parent.parent / "roles" / "proxmox_tagging"
sys.path.insert(0, str(ROLE / "filter_plugins"))

from metadata_plan import (  # noqa: E402
    normalize_notes,
    notes_plan,
    parse_notes,
    render_notes,
    resolve_targets,
)

COLUMNS = {
    "Owner": "Owner",
    "Environment": "Environment",
    "Application": "Application",
    "Internet Facing": "InternetFacing",
    "Business Service": "BusinessService",
}

CFG = {"metadata_columns": COLUMNS}


def guest(vmid, name, node="server1", gtype="qemu"):
    return {"vmid": vmid, "name": name, "node": node, "type": gtype}


def config(vmid, description):
    """Shape of one registered uri GET result"""
    return {"item": {"vmid": vmid}, "json": {"data": {"description": description}}}


def plan_for(rows, guests, configs, **overrides):
    """Run both stages the way the role does"""
    options = dict(CFG, **overrides)
    resolved = resolve_targets(rows, guests, **options)
    return notes_plan(resolved, configs, **options)


class TestNormalizeNotes:
    def test_crlf_becomes_lf(self):
        assert normalize_notes("a=1\r\nb=2") == "a=1\nb=2"

    def test_trailing_blank_lines_are_dropped(self):
        assert normalize_notes("a=1\n\n\n") == "a=1"

    def test_trailing_spaces_are_dropped(self):
        assert normalize_notes("a=1   \nb=2  ") == "a=1\nb=2"

    def test_none_is_empty(self):
        assert normalize_notes(None) == ""


class TestParseNotes:
    def test_splits_managed_from_free_text(self):
        managed, unmanaged = parse_notes(
            "Owner=a@b.com\nmigrated from esx\nEnvironment=PRO", {"Owner", "Environment"}
        )

        assert managed == {"Owner": "a@b.com", "Environment": "PRO"}
        assert unmanaged == ["migrated from esx"]

    def test_unknown_keys_count_as_free_text(self):
        _, unmanaged = parse_notes("Rack=A17", {"Owner"})

        assert unmanaged == ["Rack=A17"]

    def test_value_containing_equals_is_kept_whole(self):
        managed, _ = parse_notes("Owner=a=b", {"Owner"})

        assert managed["Owner"] == "a=b"


class TestRenderNotes:
    def test_renders_key_value_lines_in_order(self):
        text = render_notes([["Owner", "a@b.com"], ["Environment", "PRO"]], [])

        assert text == "Owner=a@b.com\nEnvironment=PRO"

    def test_preserved_lines_follow_the_managed_block(self):
        text = render_notes([["Owner", "a@b.com"]], ["migrated from esx"])

        assert text == "Owner=a@b.com\nmigrated from esx"


class TestPlan:
    def test_writes_metadata_to_an_empty_notes_field(self):
        rows = [{
            "_row": 4, "VMID": "134", "Owner": "wai.kwong@digitaledgedc.com",
            "Environment": "PRO", "Application": "Proxmox Auto Inventory",
            "Internet Facing": "No", "Business Service": "Infrastructure Automation",
        }]
        plan = plan_for(rows, [guest(134, "lhkgautoinv1")], [config(134, "")])

        assert plan["stats"]["changed"] == 1
        assert plan["changes"][0]["desired_notes"] == (
            "Owner=wai.kwong@digitaledgedc.com\n"
            "Environment=PRO\n"
            "Application=Proxmox Auto Inventory\n"
            "InternetFacing=No\n"
            "BusinessService=Infrastructure Automation"
        )

    def test_is_idempotent_on_a_second_run(self):
        rows = [{"_row": 4, "VMID": "134", "Owner": "a@b.com", "Environment": "PRO"}]
        current = "Owner=a@b.com\nEnvironment=PRO"

        plan = plan_for(rows, [guest(134, "vm")], [config(134, current)])

        assert plan["stats"]["changed"] == 0
        assert plan["stats"]["unchanged"] == 1

    def test_blank_columns_are_omitted_not_written_empty(self):
        rows = [{"_row": 4, "VMID": "134", "Owner": "a@b.com", "Environment": ""}]

        plan = plan_for(rows, [guest(134, "vm")], [config(134, "")])

        assert plan["changes"][0]["desired_notes"] == "Owner=a@b.com"

    def test_free_text_owner_is_written_literally(self):
        rows = [{"_row": 4, "VMID": "106", "Owner": "Can remove"}]

        plan = plan_for(rows, [guest(106, "gideon")], [config(106, "")])

        assert plan["changes"][0]["desired_notes"] == "Owner=Can remove"

    def test_fully_blank_row_clears_the_managed_keys(self):
        rows = [{"_row": 4, "VMID": "136", "Owner": "", "Environment": ""}]

        plan = plan_for(rows, [guest(136, "dns1")], [config(136, "Owner=stale@b.com")])

        assert plan["changes"][0]["desired_notes"] == ""

    def test_fully_blank_row_is_skipped_when_write_empty_is_off(self):
        rows = [{"_row": 4, "VMID": "136", "Owner": ""}]

        plan = plan_for(
            rows, [guest(136, "dns1")], [config(136, "")], write_empty=False
        )

        assert plan["stats"]["targets"] == 0

    def test_free_text_lines_are_preserved_by_default(self):
        rows = [{"_row": 4, "VMID": "134", "Owner": "new@b.com"}]
        current = "Owner=old@b.com\nmigrated from esx 2024"

        plan = plan_for(rows, [guest(134, "vm")], [config(134, current)])

        assert plan["changes"][0]["desired_notes"] == (
            "Owner=new@b.com\nmigrated from esx 2024"
        )
        assert plan["changes"][0]["preserved_lines"] == ["migrated from esx 2024"]

    def test_free_text_is_wiped_when_preserve_is_off(self):
        rows = [{"_row": 4, "VMID": "134", "Owner": "new@b.com"}]
        current = "Owner=old@b.com\nmigrated from esx 2024"

        plan = plan_for(
            rows, [guest(134, "vm")], [config(134, current)], preserve_unmanaged=False
        )

        assert plan["changes"][0]["desired_notes"] == "Owner=new@b.com"

    def test_preserving_free_text_stays_idempotent(self):
        rows = [{"_row": 4, "VMID": "134", "Owner": "a@b.com"}]
        settled = "Owner=a@b.com\nmigrated from esx"

        plan = plan_for(rows, [guest(134, "vm")], [config(134, settled)])

        assert plan["stats"]["changed"] == 0

    def test_lxc_guests_keep_their_type(self):
        rows = [{"_row": 4, "VMID": "136", "Owner": "a@b.com"}]

        plan = plan_for(
            rows, [guest(136, "dns1", gtype="lxc")], [config(136, "")]
        )

        assert plan["changes"][0]["type"] == "lxc"

    def test_missing_description_key_is_treated_as_empty(self):
        rows = [{"_row": 4, "VMID": "134", "Owner": "a@b.com"}]
        bare = {"item": {"vmid": 134}, "json": {"data": {"cores": 4}}}

        plan = plan_for(rows, [guest(134, "vm")], [bare])

        assert plan["changes"][0]["current_notes"] == ""


class TestResolution:
    def test_row_matching_no_guest_is_reported(self):
        resolved = resolve_targets([{"_row": 4, "VMID": "999"}], [guest(134, "vm")], **CFG)

        assert resolved["missing_in_proxmox"] == [{"row": 4, "identifier": "999"}]

    def test_guest_absent_from_sheet_is_left_alone(self):
        resolved = resolve_targets(
            [{"_row": 4, "VMID": "134"}], [guest(134, "a"), guest(135, "b")], **CFG
        )

        assert [item["vmid"] for item in resolved["unmanaged"]] == [135]

    def test_name_fallback_when_vmid_is_blank(self):
        resolved = resolve_targets(
            [{"_row": 4, "VMID": "", "Name": "LHKGAUTOINV1", "Owner": "a@b.com"}],
            [guest(134, "lhkgautoinv1")],
            **CFG,
        )

        assert resolved["targets"][0]["vmid"] == 134

    def test_ambiguous_name_is_rejected(self):
        resolved = resolve_targets(
            [{"_row": 4, "VMID": "", "Name": "dup"}],
            [guest(1, "dup"), guest(2, "dup")],
            **CFG,
        )

        assert resolved["invalid_rows"][0]["row"] == 4

    def test_non_numeric_vmid_is_rejected(self):
        resolved = resolve_targets(
            [{"_row": 4, "VMID": "one"}], [guest(134, "vm")], **CFG
        )

        assert "not a number" in resolved["invalid_rows"][0]["reason"]

    def test_duplicate_vmid_is_rejected(self):
        resolved = resolve_targets(
            [{"_row": 4, "VMID": "134"}, {"_row": 5, "VMID": "134"}],
            [guest(134, "vm")],
            **CFG,
        )

        assert resolved["invalid_rows"][0]["row"] == 5

    def test_empty_metadata_columns_is_rejected(self):
        with pytest.raises(Exception, match="metadata_columns"):
            resolve_targets([], [], metadata_columns={})


class TestStats:
    def test_changed_percentage_is_against_cluster_size(self):
        rows = [{"_row": 4, "VMID": "134", "Owner": "a@b.com"}]

        plan = plan_for(
            rows, [guest(134, "a"), guest(135, "b")], [config(134, "")]
        )

        assert plan["stats"]["changed_pct"] == 50.0
        assert plan["stats"]["guests"] == 2
        assert plan["stats"]["targets"] == 1
