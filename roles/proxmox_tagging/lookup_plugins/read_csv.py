"""Lookup plugin that reads a csv sheet into a list of row dicts."""

from __future__ import annotations

DOCUMENTATION = r"""
name: read_csv
short_description: Read a csv table into a list of dictionaries
description:
  - Reads a csv file and returns one dict per data row, keyed by the header
    row values.
  - Uses the Python standard library only, so the stock awx-ee execution
    environment runs it with no extra dependency.
  - CSV is the only supported format. Export the sheet from Excel with
    I(Save As -> CSV UTF-8).
options:
  _terms:
    description: Path to the csv file
    required: true
    type: list
    elements: path
  header_row:
    description:
      - 1-indexed row holding the column headers, or C(auto) to find it.
      - C(auto) scans for the first row containing O(header_marker), which
        makes the reader tolerant of title and spacer rows above the table.
    type: raw
    default: auto
  header_marker:
    description:
      - Column name that identifies the header row when O(header_row=auto).
      - With no marker, the first non-empty row is treated as the header.
    type: str
    default: null
  delimiter:
    description: Field delimiter; set to C(;) for a European Excel export
    type: str
    default: ","
  encoding:
    description: Text encoding; utf-8-sig transparently strips an Excel BOM
    type: str
    default: utf-8-sig
  skip_empty:
    description: Drop rows where every cell is empty
    type: bool
    default: true
"""

EXAMPLES = r"""
- name: Load the metadata source table
  ansible.builtin.set_fact:
    rows: "{{ lookup('read_csv', '/data/vm_tags.csv') }}"

- name: Load a table whose headers start on row 3
  ansible.builtin.set_fact:
    rows: "{{ lookup('read_csv', '/data/vm_tags.csv', header_row=3) }}"
"""

RETURN = r"""
_raw:
  description: One dictionary per data row
  type: list
  elements: dict
"""

import csv  # noqa: E402
import datetime  # noqa: E402
import io  # noqa: E402
import os  # noqa: E402

from ansible.errors import AnsibleError  # noqa: E402
from ansible.module_utils.common.text.converters import to_native  # noqa: E402
from ansible.plugins.lookup import LookupBase  # noqa: E402

_ACCEPTED_EXTENSIONS = (".csv",)


def _cell_to_text(value):
    """Normalize one cell into a trimmed string"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _build_headers(header_cells):
    """Turn the header row into stable, non-empty column keys"""
    headers = []
    for index, cell in enumerate(header_cells):
        name = _cell_to_text(cell)
        headers.append(name if name else "column_{0}".format(index + 1))
    return tuple(headers)


def _find_header_row(rows, marker, source):
    """Locate the header row by marker column, tolerating title and spacer rows"""
    for index, row in enumerate(rows, start=1):
        values = [_cell_to_text(cell) for cell in row]
        if marker:
            if marker in values:
                return index
        elif any(values):
            return index

    raise AnsibleError(
        "could not find a header row in {0}: no row contains {1!r}. Check the "
        "file, or pin the row number with pxt_source_header_row".format(
            source, marker
        )
        if marker
        else "could not find a non-empty header row in {0}".format(source)
    )


def _resolve_header_row(rows, header_row, marker, source):
    """Turn the header_row option into a concrete 1-indexed row number"""
    if str(header_row or "auto").strip().lower() == "auto":
        return _find_header_row(rows, marker, source)

    try:
        return int(header_row)
    except (TypeError, ValueError):
        raise AnsibleError(
            "header_row must be a row number or 'auto', got {0!r}".format(header_row)
        )


def _rows_to_records(raw_rows, header_row, marker, skip_empty, source):
    """Turn an iterable of cell tuples into header-keyed dicts"""
    rows = list(raw_rows)
    header_index = _resolve_header_row(rows, header_row, marker, source)

    if header_index > len(rows):
        raise AnsibleError(
            "header row {0} is past the end of {1}, which has {2} rows".format(
                header_index, source, len(rows)
            )
        )

    headers = _build_headers(rows[header_index - 1])
    records = []
    for offset, row in enumerate(rows[header_index:], start=header_index + 1):
        values = [_cell_to_text(cell) for cell in row]
        if skip_empty and not any(values):
            continue

        record = dict(zip(headers, values))
        record["_row"] = offset
        records.append(record)

    return records


class LookupModule(LookupBase):
    def run(self, terms, variables=None, **kwargs):
        self.set_options(var_options=variables, direct=kwargs)

        results = []
        for term in terms:
            path = self.find_file_in_search_path(variables, "files", term) or term
            self._reject_non_csv(path)
            results.extend(self._read_csv(path))
        return results

    def _reject_non_csv(self, path):
        """Fail early and clearly rather than parsing a workbook as text"""
        extension = os.path.splitext(path)[1].lower()
        if extension not in _ACCEPTED_EXTENSIONS:
            raise AnsibleError(
                "{0} is not a .csv file; this role reads csv only. Open the "
                "sheet in Excel and Save As -> CSV UTF-8".format(path)
            )

    def _read_csv(self, path):
        """Read the csv with the standard library"""
        delimiter = self.get_option("delimiter") or ","
        encoding = self.get_option("encoding") or "utf-8-sig"

        try:
            with io.open(path, "r", encoding=encoding, newline="") as handle:
                raw_rows = list(csv.reader(handle, delimiter=delimiter))
        except UnicodeDecodeError as exc:
            raise AnsibleError(
                "could not decode {0} as {1}; if the sheet was exported from "
                "Excel as 'CSV' rather than 'CSV UTF-8', re-export it or set "
                "encoding=cp1252: {2}".format(path, encoding, to_native(exc))
            )
        except (IOError, OSError) as exc:
            raise AnsibleError(
                "could not read {0}: {1}".format(path, to_native(exc))
            )

        return _rows_to_records(
            raw_rows,
            self.get_option("header_row"),
            self.get_option("header_marker"),
            self.get_option("skip_empty"),
            path,
        )
