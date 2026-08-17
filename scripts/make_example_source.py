#!/usr/bin/env python3
"""Generate the example tag source in both csv and xlsx form."""

from __future__ import annotations

import csv
import pathlib

HEADERS = ("VMID", "Name", "Environment", "Owner", "Application", "Backup", "Tags")

ROWS = (
    (100, "web-prod-01", "Production", "platform", "nginx", "daily", "public;tier-web"),
    (101, "web-prod-02", "Production", "platform", "nginx", "daily", "public;tier-web"),
    (200, "db-prod-01", "Production", "dba", "postgres", "hourly", "tier-data;pci"),
    (300, "app-uat-01", "UAT", "app-team", "billing", "weekly", "tier-app"),
    (400, "jenkins-01", "Development", "devops", "jenkins", "none", ""),
)

FILES_DIR = pathlib.Path(__file__).resolve().parent.parent / "files"


def write_csv(path):
    """Write the example as UTF-8 csv, matching Excel's 'CSV UTF-8' export"""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS)
        writer.writerows(ROWS)
    print("wrote {0}".format(path))


def write_xlsx(path):
    """Write the example workbook, skipped when openpyxl is unavailable"""
    try:
        from openpyxl import Workbook
    except ImportError:
        print("skipping {0}: openpyxl not installed".format(path))
        return

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "VMs"
    sheet.append(HEADERS)
    for row in ROWS:
        sheet.append(row)

    for column, width in zip("ABCDEFG", (8, 20, 14, 14, 16, 10, 28)):
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = "A2"

    workbook.save(path)
    print("wrote {0}".format(path))


def main():
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(FILES_DIR / "vm_tags.example.csv")
    write_xlsx(FILES_DIR / "vm_tags.example.xlsx")


if __name__ == "__main__":
    main()
