#!/usr/bin/env python3
"""
IDAPython batch script: decompile a list of addresses found by
scripts/verify_signatures.py and write the Hex-Rays output to a JSON report.

Run from IDA text-mode (batch), e.g.:

    idat.exe -A -S"<abs path>/ida_decompile_targets.py" -Lida.log db.i64

Input JSON (env IDA_TARGETS_JSON or default next to this script):
    [{"id": "NormalTick", "addr": 174207268}, ...]

Output (env IDA_REPORT_JSON or default next to this script):
    [{"id": ..., "addr": ..., "func_name": ..., "start": ..., "end": ...,
      "ok": true, "decompiled": "..."}]

The script only reads the database: it creates no names and writes no bytes,
then exits without saving.
"""

import json
import os
from pathlib import Path

import ida_hexrays
import ida_funcs
import ida_name
import idaapi


def main() -> int:
    base = Path(__file__).resolve().parent
    targets_path = Path(os.environ.get("IDA_TARGETS_JSON", base / "ida_targets.json"))
    report_path = Path(os.environ.get("IDA_REPORT_JSON", base / "ida_decompile_report.json"))

    targets = json.loads(targets_path.read_text(encoding="utf-8"))

    ida_hexrays.init_hexrays_plugin()
    report = []
    for item in targets:
        entry = {
            "id": item.get("id"),
            "addr": item.get("addr"),
            "func_name": None,
            "start": None,
            "end": None,
            "ok": False,
            "error": None,
            "decompiled": None,
        }
        ea = int(item["addr"], 0) if isinstance(item["addr"], str) else int(item["addr"])
        entry["addr"] = hex(ea)
        func = ida_funcs.get_func(ea)
        if func is None:
            entry["error"] = "no function at address"
            report.append(entry)
            continue
        entry["start"] = hex(func.start_ea)
        entry["end"] = hex(func.end_ea)
        entry["func_name"] = ida_name.get_name(ea)
        try:
            cf = ida_hexrays.decompile(ea)
            if cf is None:
                entry["error"] = "decompile returned None"
            else:
                entry["decompiled"] = str(cf)
                entry["ok"] = True
        except Exception as exc:  # noqa: BLE001
            entry["error"] = repr(exc)
        report.append(entry)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {report_path} with {len(report)} entries")
    idaapi.qexit(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
