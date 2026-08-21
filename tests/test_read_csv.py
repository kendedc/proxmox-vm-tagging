"""Unit tests for the read_csv lookup — parsing and Excel export quirks."""

from __future__ import annotations

import pathlib
import sys

import pytest

ROLE = pathlib.Path(__file__).resolve().parent.parent / "roles" / "proxmox_tagging"

from ansible.errors import AnsibleError  # noqa: E402
from ansible.parsing.dataloader import DataLoader  # noqa: E402
from ansible.plugins.loader import lookup_loader  # noqa: E402

# Go through the real plugin loader so DOCUMENTATION defaults are registered
lookup_loader.add_directory(str(ROLE / "lookup_plugins"))

HEADER = "VMID,Name,Environment,Tags\n"
BODY = "100,web-prod-01,Production,web;public\n200,db-prod-01,Production,tier-data\n"


def load(path, **options):
    """Run the lookup the way Ansible would, with options applied"""
    lookup = lookup_loader.get("read_csv", loader=DataLoader())
    return lookup.run([str(path)], variables={}, **options)


class TestCsv:
    def test_reads_rows_keyed_by_header(self, tmp_path):
        source = tmp_path / "tags.csv"
        source.write_text(HEADER + BODY, encoding="utf-8")

        rows = load(source)

        assert len(rows) == 2
        assert rows[0]["VMID"] == "100"
        assert rows[0]["Tags"] == "web;public"
        assert rows[0]["_row"] == 2

    def test_strips_the_excel_utf8_bom(self, tmp_path):
        source = tmp_path / "tags.csv"
        source.write_text(HEADER + BODY, encoding="utf-8-sig")

        rows = load(source)

        # a leaked BOM would make the key '﻿VMID' and silently break matching
        assert "VMID" in rows[0]

    def test_handles_cp1252_export(self, tmp_path):
        source = tmp_path / "tags.csv"
        source.write_bytes(
            (HEADER + "100,café-01,Production,web\n").encode("cp1252")
        )

        rows = load(source, encoding="cp1252")

        assert rows[0]["Name"] == "café-01"

    def test_quoted_field_containing_commas(self, tmp_path):
        source = tmp_path / "tags.csv"
        source.write_text(HEADER + '100,web-01,Production,"web,public"\n', encoding="utf-8")

        rows = load(source)

        assert rows[0]["Tags"] == "web,public"

    def test_blank_rows_are_dropped(self, tmp_path):
        source = tmp_path / "tags.csv"
        source.write_text(HEADER + BODY + ",,,\n", encoding="utf-8")

        assert len(load(source)) == 2

    def test_trailing_whitespace_is_trimmed(self, tmp_path):
        source = tmp_path / "tags.csv"
        source.write_text(HEADER + "100 , web-01 ,Production,web \n", encoding="utf-8")

        rows = load(source)

        assert rows[0]["VMID"] == "100"
        assert rows[0]["Name"] == "web-01"

    def test_header_row_offset(self, tmp_path):
        source = tmp_path / "tags.csv"
        source.write_text("exported 2026-08-14\n" + HEADER + BODY, encoding="utf-8")

        rows = load(source, header_row=2)

        assert rows[0]["VMID"] == "100"


class TestHeaderAutoDetection:
    """The sheet's preamble has already changed twice; auto must survive it"""

    def test_headers_on_line_1(self, tmp_path):
        source = tmp_path / "tags.csv"
        source.write_text(HEADER + BODY, encoding="utf-8")

        rows = load(source, header_marker="VMID")

        assert rows[0]["VMID"] == "100"
        assert rows[0]["_row"] == 2

    def test_spacer_row_then_headers_on_line_2(self, tmp_path):
        source = tmp_path / "tags.csv"
        source.write_text("\n" + HEADER + BODY, encoding="utf-8")

        rows = load(source, header_marker="VMID")

        assert rows[0]["VMID"] == "100"
        # row numbers stay relative to the file, so the first record is line 3
        assert rows[0]["_row"] == 3

    def test_title_and_spacer_then_headers_on_line_3(self, tmp_path):
        source = tmp_path / "tags.csv"
        source.write_text("Metadata Standard:,link\n\n" + HEADER + BODY, encoding="utf-8")

        rows = load(source, header_marker="VMID")

        assert rows[0]["VMID"] == "100"
        assert rows[0]["_row"] == 4

    def test_bom_does_not_defeat_marker_matching(self, tmp_path):
        source = tmp_path / "tags.csv"
        source.write_text("\n" + HEADER + BODY, encoding="utf-8-sig")

        rows = load(source, header_marker="VMID")

        assert rows[0]["VMID"] == "100"

    def test_marker_never_found_is_reported(self, tmp_path):
        source = tmp_path / "tags.csv"
        source.write_text(HEADER + BODY, encoding="utf-8")

        with pytest.raises(AnsibleError, match="no row contains"):
            load(source, header_marker="GuestID")

    def test_without_a_marker_first_non_empty_row_wins(self, tmp_path):
        source = tmp_path / "tags.csv"
        source.write_text("\n\n" + HEADER + BODY, encoding="utf-8")

        rows = load(source)

        assert rows[0]["VMID"] == "100"

    def test_explicit_row_still_overrides_auto(self, tmp_path):
        source = tmp_path / "tags.csv"
        source.write_text("\n" + HEADER + BODY, encoding="utf-8")

        rows = load(source, header_row=2, header_marker="VMID")

        assert rows[0]["VMID"] == "100"

    def test_header_row_past_end_of_file_is_reported(self, tmp_path):
        source = tmp_path / "tags.csv"
        source.write_text(HEADER, encoding="utf-8")

        with pytest.raises(AnsibleError, match="past the end"):
            load(source, header_row=99)

    def test_non_numeric_header_row_is_reported(self, tmp_path):
        source = tmp_path / "tags.csv"
        source.write_text(HEADER + BODY, encoding="utf-8")

        with pytest.raises(AnsibleError, match="row number or 'auto'"):
            load(source, header_row="third")


class TestSemicolonDelimiter:
    """A non-English Excel writes csv with semicolons, still csv"""

    def test_semicolon_delimited(self, tmp_path):
        source = tmp_path / "tags.csv"
        source.write_text("VMID;Name\n100;web-01\n", encoding="utf-8")

        rows = load(source, delimiter=";")

        assert rows[0]["Name"] == "web-01"


class TestCsvOnly:
    """Anything that is not a .csv must fail loudly, not parse as garbage"""

    def test_xlsx_is_rejected(self, tmp_path):
        source = tmp_path / "tags.xlsx"
        source.write_bytes(b"PK\x03\x04binary workbook")

        with pytest.raises(AnsibleError, match="not a .csv file"):
            load(source)

    def test_tsv_is_rejected(self, tmp_path):
        source = tmp_path / "tags.tsv"
        source.write_text("VMID\tName\n100\tweb-01\n", encoding="utf-8")

        with pytest.raises(AnsibleError, match="not a .csv file"):
            load(source)

    def test_the_error_names_the_excel_export_option(self, tmp_path):
        source = tmp_path / "tags.xlsx"
        source.write_bytes(b"PK\x03\x04")

        with pytest.raises(AnsibleError, match="CSV UTF-8"):
            load(source)

    def test_missing_header_row_is_reported(self, tmp_path):
        source = tmp_path / "tags.csv"
        source.write_text("", encoding="utf-8")

        with pytest.raises(AnsibleError, match="header row"):
            load(source)
