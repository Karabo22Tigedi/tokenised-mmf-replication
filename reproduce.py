#!/usr/bin/env python3
"""Reproduce packaged results in a disposable copy; never refresh source data.

Usage: python -I -B reproduce.py [--quick | --main | --extended]
Quick (default): original 55 tests, key numbers/verdicts/twin descriptives, figure.
Main: quick plus frozen M0--M4 and sensitivity outputs from script 05.
Extended: main plus the explicitly listed offline exploratory scripts.

CSV comparisons require identical headers and cells, without numerical tolerance.
Row order is exact except freeze_wallet_roles.csv and addl_ta_hop_parking.csv:
set iteration and inherited equal-value ties make their row order immaterial.
Both require unique (fund, chain, address) keys and exact full rows.
CSV newline/quoting differences alone are immaterial.
Figures are rendered and checked against their numerical inputs and reported
values; PDF/PNG binary identity is not required (metadata and renderers vary).
All commands and comparisons are recorded under .reproduction/run-*/report.json.
Python dependencies must already be installed; this program installs nothing.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
KEY_OUTPUTS = ("key_numbers.csv", "freeze_falsifier_verdicts.csv",
               "freeze_twin_descriptives.csv", "freeze_fdit_oos_residual_diagnostic.csv")
# rules.tr5_roles() builds its output by iterating addrs | set(reasons), and 05
# writes that dict's iteration order. Those rows have no meaningful sequence.
# Script 19's parking table sorts by fund/value, so ties inherit that set order.
# Only these two tables ignore row order; all fields/key uniqueness stay exact.
KEYED_CSV = {name: ("fund", "chain", "address") for name in
             ("freeze_wallet_roles.csv", "addl_ta_hop_parking.csv")}
MAIN_OUTPUTS = tuple("freeze_" + name + ".csv" for name in (
    "wallet_roles", "mint_labels", "m0_reconciliation", "m0_oos_codepath",
    "m1_secondary", "m2_calendar", "m2_flow_match", "m3_holding", "m4_servicing",
    "measures_status", "sens_m0", "sens_m1", "sens_m2", "sens_m3", "sens_m4",
    "sens_labels", "sens_roles", "diagnostics"))
EXTENDED = (
    ("13_addl_f1_transfers.py", ("addl_f1_events.csv",)),
    ("15_addl_analysis.py", tuple("addl_" + name + ".csv" for name in (
        "signer_function", "signer_summary", "concentration", "top_counterparties",
        "pm_wallet_profile", "m1_excl_pm", "stellar_issuer_controls", "evm_admin_functions",
        "solana_mint_controls", "fyoxx_quarter_gaps", "fyoxx_parked_lots",
        "fragmentation_supply", "fragmentation_transfers", "nmfp_rec_ben",
        "nmfp_rec_ben_summary", "summary_numbers"))),
    ("16_addl_netting_days.py", ("addl_nmfp_netting_days.csv",)),
    # Script 17 reads the new script-15 key numbers: run 07 at this dependency.
    ("07_key_numbers.py", KEY_OUTPUTS),
    ("17_addl_turnover.py", ("addl_turnover.csv",)),
    ("18_addl_oos_and_ordering.py", tuple("addl_" + name + ".csv" for name in (
        "oos_role_changes", "oos_measures", "oos_verdicts", "ordering_days",
        "ordering_summary", "ordering_hours", "mls_cashleg", "f1_minsize", "summary_numbers_18"))),
    ("19_addl_swap_and_owner.py", tuple("addl_" + name + ".csv" for name in (
        "swap_transfers", "swap_hours", "swap_counterparties", "swap_summary",
        "ownermatch_wallets", "ownermatch_summary", "ta_hop_parking", "reclass_m1",
        "frax_custodian", "frax_transfers", "frax_pairs", "turnover_vs_reds",
        "netting_buffer", "xchain_pairs", "xchain_summary", "summary_numbers_19"))),
    ("20_addl_div_mint_timing.py", ("addl_divtiming_summary.csv", "addl_divtiming_days.csv")),
    ("22_addl2_cashlegs.py", tuple("addl2_" + name + ".csv" for name in (
        "tokens", "legs", "tx", "tx_pairs", "summary", "patterns", "summary_numbers_22"))),
    ("23_addl_contract_venue_ratio.py", ("addl_contract_venue_ratio.csv",)),
)

# This executes the same module/script and arguments as each logical command.
# -I removes user/PYTHONPATH injection; -B avoids modifying the release/imports.
# The audit hook denies network activity and child processes, and confines Python
# file mutations to scratch. It is a guard for these trusted packaged programs,
# not a general operating-system sandbox for arbitrary hostile native extensions.
BOOTSTRAP = r'''
import os, pathlib, runpy, sys
base = pathlib.Path(sys.argv[1]).resolve()
def confined(path):
    if isinstance(path, int):
        return
    if not pathlib.Path(os.fsdecode(path)).resolve().is_relative_to(base):
        raise PermissionError("offline reproduction: write outside scratch denied")
def audit(event, args):
    if event.startswith("socket.") or event in {"urllib.Request", "subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn"}:
        raise PermissionError("offline reproduction: network/child process denied: " + event)
    if event == "open":
        path, mode, flags = args
        if (mode and any(c in mode for c in "wax+")) or (flags and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
            confined(path)
    elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.truncate", "os.chmod", "os.utime"}:
        confined(args[0])
    elif event in {"os.rename", "os.link", "os.symlink"}:
        confined(args[0]); confined(args[1])
sys.addaudithook(audit)
kind, target, arguments = sys.argv[2], sys.argv[3], sys.argv[4:]
sys.argv = [target] + arguments
if kind == "module":
    runpy.run_module(target, run_name="__main__", alter_sys=True)
else:
    runpy.run_path(target, run_name="__main__")
'''


def csv_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.reader(stream))


def csv_dicts(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def compare_csv(expected: Path, actual: Path) -> dict:
    result = {"path": actual.name, "comparison": "exact CSV cells, headers and row order",
              "numeric_tolerance": "none", "ok": False}
    try:
        left, right = csv_rows(expected), csv_rows(actual)
        result.update(expected_rows=max(0, len(left) - 1), actual_rows=max(0, len(right) - 1))
        if not left or not right or not left[0] or not right[0]:
            raise ValueError("missing CSV header")
        if expected.name in KEYED_CSV:
            keys = KEYED_CSV[expected.name]
            result.update(comparison="unique keyed rows; exact headers and all field strings",
                          key_columns=list(keys), row_order="not meaningful: set iteration or inherited equal-value ties")
            if left[0] != right[0]:
                raise ValueError("CSV headers differ")
            positions = [left[0].index(key) for key in keys]
            def ordered(table, label):
                identities = set()
                for row in table[1:]:
                    if len(row) != len(table[0]):
                        raise ValueError(f"{label}: row has wrong number of fields")
                    identity = tuple(row[i] for i in positions)
                    if identity in identities:
                        raise ValueError(f"{label}: duplicate key {identity!r}")
                    identities.add(identity)
                return [table[0]] + sorted(table[1:], key=lambda row: tuple(row[i] for i in positions))
            left, right = ordered(left, "expected"), ordered(right, "actual")
        count, examples = 0, []
        for i in range(max(len(left), len(right))):
            a = left[i] if i < len(left) else []
            b = right[i] if i < len(right) else []
            for j in range(max(len(a), len(b))):
                av = a[j] if j < len(a) else None
                bv = b[j] if j < len(b) else None
                if av != bv:
                    count += 1
                    if len(examples) < 12:
                        examples.append({"csv_row": i + 1, "column": j + 1,
                                         "expected": av, "actual": bv})
            if a == b == [] and (i >= len(left) or i >= len(right)):
                count += 1
        result.update(ok=count == 0, mismatched_cells=count, examples=examples)
    except (OSError, UnicodeError, csv.Error, ValueError) as error:
        result["error"] = str(error)
    return result


class Runner:
    def __init__(self, directory: Path, mode: str, timeout: int):
        self.directory, self.work, self.timeout = directory, directory / "work", timeout
        self.report = {"mode": mode, "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                       "python": sys.version, "commands": [], "comparisons": [], "errors": [],
                       "comparison_policy": "exact CSV headers/cells/row order; two documented set/tie-order tables use unique fund/chain/address keys; no numeric tolerance",
                       "scope": "offline packaged inputs; source reacquisition and legal source checks are separate"}

    def save(self) -> None:
        self.report["ok"] = (not self.report["errors"]
            and all(x["ok"] for x in self.report["commands"])
            and all(x["ok"] for x in self.report["comparisons"]))
        (self.directory / "report.json").write_text(
            json.dumps(self.report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def command(self, target: str, args: tuple | list = (), module: bool = False) -> dict:
        number = len(self.report["commands"]) + 1
        logical = [sys.executable, "-I", "-B"] + (["-m", target] if module else ["scripts/" + target]) + list(args)
        actual = [sys.executable, "-I", "-B", "-X", "utf8", "-c", BOOTSTRAP, str(self.work),
                  "module" if module else "script", target if module else str(self.work / "scripts" / target)] + list(args)
        stem = f"{number:02d}-{Path(target).stem}"
        record = {"command": logical, "wrapper": "isolated Python with offline/write audit hook",
                  "stdout": stem + ".stdout.txt", "stderr": stem + ".stderr.txt", "ok": False}
        self.report["commands"].append(record)
        environment = os.environ.copy()
        for key in ("HOME", "USERPROFILE", "TMP", "TEMP", "TMPDIR", "MPLCONFIGDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
            environment[key] = str(self.work / ".runtime")
        environment["MPLBACKEND"] = "Agg"
        print("Running:", " ".join(logical), flush=True)
        started = time.monotonic()
        try:
            with (self.directory / record["stdout"]).open("w", encoding="utf-8") as out, \
                    (self.directory / record["stderr"]).open("w", encoding="utf-8") as err:
                completed = subprocess.run(actual, cwd=self.work, env=environment,
                                           stdout=out, stderr=err, timeout=self.timeout, check=False)
            record.update(exit_code=completed.returncode, ok=completed.returncode == 0)
        except (OSError, subprocess.TimeoutExpired) as error:
            record.update(exit_code=None, error=str(error))
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        self.save()
        if not record["ok"]:
            raise RuntimeError(f"command failed: {target}; see {record['stderr']}")
        return record

    def stage(self, script: str, outputs: tuple, args: tuple | list = ()) -> None:
        # Remove carried expected output before executing, so a no-op cannot pass.
        for name in outputs:
            expected = ROOT / "data" / "processed" / name
            if not expected.is_file():
                raise RuntimeError(f"expected packaged result missing: {expected.relative_to(ROOT)}")
            (self.work / "data" / "processed" / name).unlink(missing_ok=True)
        self.command(script, args)
        for name in outputs:
            comparison = compare_csv(ROOT / "data" / "processed" / name,
                                     self.work / "data" / "processed" / name)
            comparison["stage"] = script
            self.report["comparisons"].append(comparison)
        self.save()
        failed = [x["path"] for x in self.report["comparisons"] if not x["ok"]]
        if failed:
            raise RuntimeError("CSV results differ: " + ", ".join(failed))

    def tests(self) -> None:
        record = self.command("unittest", ["discover", "-s", "scripts", "-p", "test_*.py"], module=True)
        output = (self.directory / record["stderr"]).read_text(encoding="utf-8")
        found = re.search(r"Ran (\d+) tests? in", output)
        count = int(found.group(1)) if found else None
        record["tests_run"] = count
        if count != 55 or re.search(r"\bskipped=|\bexpected failures=|\bunexpected successes=", output):
            raise RuntimeError(f"expected the original 55 tests without skips, got {count}; inspect test log")
        self.save()

    def figure(self) -> None:
        files = [self.work / "figures" / ("fig_calendar_revised." + ext) for ext in ("pdf", "png")]
        for path in files:
            path.unlink(missing_ok=True)
        record = self.command("render_candidate_calendar.py")
        output = (self.directory / record["stdout"]).read_text(encoding="utf-8")
        checks = {"stage": "render_candidate_calendar.py", "path": "fig_calendar_revised.{pdf,png}",
                  "comparison": "figure formats and printed plotted values against packaged calendar CSV", "ok": True,
                  "funds": []}
        for row in csv_dicts(ROOT / "data" / "processed" / "freeze_m2_calendar.csv"):
            counts = {int(h): int(n) for h, n in (x.split(":") for x in row["burn_hour_counts"].split(";") if x)}
            total, modal = sum(counts.values()), int(row["modal_burn_hour_et"])
            line = (f"{row['fund']}: {total} burns, modal {modal:02d} ET, "
                    f"modal share {100 * counts.get(modal, 0) / total:.1f}%, "
                    f"non-business value {float(row['nonbusiness_share_pct']):.4f}%")
            ok = line in output.splitlines()
            checks["funds"].append({"fund": row["fund"], "expected_printed_values": line, "ok": ok})
            checks["ok"] &= ok
        for path in files:
            if not path.is_file() or path.stat().st_size == 0:
                checks["ok"] = False
                continue
            with path.open("rb") as stream:
                header = stream.read(24)
            if path.suffix == ".pdf":
                checks["ok"] &= header.startswith(b"%PDF-")
            else:
                valid = header.startswith(b"\x89PNG\r\n\x1a\n") and header[12:16] == b"IHDR"
                checks["ok"] &= valid
                if valid:
                    checks["png_dimensions"] = list(struct.unpack(">II", header[16:24]))
                    checks["ok"] &= all(x > 0 for x in checks["png_dimensions"])
        self.report["comparisons"].append(checks)
        self.save()
        if not checks["ok"]:
            raise RuntimeError("figure output or displayed numerical values differ")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modes = parser.add_mutually_exclusive_group()
    for mode in ("quick", "main", "extended"):
        modes.add_argument("--" + mode, dest="mode", action="store_const", const=mode)
    parser.set_defaults(mode="quick")
    parser.add_argument("--timeout", type=int, default=7200, help="seconds allowed for each command (default 7200)")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    # Load verifier without relying on cwd or the script directory being on sys.path.
    spec = importlib.util.spec_from_file_location("examiner_verify", ROOT / "verify.py")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    verification = verifier.verify(ROOT)
    if not verification["ok"]:
        print(json.dumps(verification, indent=2))
        return 1
    container = ROOT / ".reproduction"
    if container.is_symlink() or getattr(container, "is_junction", lambda: False)():
        print("FAIL: .reproduction must not be a symlink or junction", file=sys.stderr)
        return 1
    container.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=container))
    runner = Runner(directory, args.mode, args.timeout)
    runner.report["manifest_verification"] = verification
    print(f"Scratch and logs: {directory}", flush=True)
    try:
        for name in ("scripts", "data", "figures"):
            shutil.copytree(ROOT / name, runner.work / name, symlinks=False)
        (runner.work / ".runtime").mkdir()
        runner.tests()
        if args.mode in {"main", "extended"}:
            oos = sorted({r["report_date"] for r in csv_dicts(ROOT / "data/processed/freeze_nmfp_summary.csv")
                          if r["governing"] == "1" and r["report_date"] < "2026-07-31"})
            if not oos:
                raise RuntimeError("no governing earlier out-of-sample dates found")
            runner.report["out_of_sample_dates"] = oos
            runner.stage("05_measures.py", MAIN_OUTPUTS, [
                "--prefix", "freeze", "--stellar-tag", "freeze_2026-10-16",
                "--window", "2026-01-01:2026-09-30",
                "--insample", "2026-07-31,2026-08-31,2026-09-30",
                "--oos", ",".join(oos), "--reveal-oos", "--sensitivities",
                "--quarter-ends", "2026-03-31,2026-06-30,2026-09-30"])
        if args.mode == "extended":
            for script, outputs in EXTENDED:
                runner.stage(script, outputs)
        runner.stage("07_key_numbers.py", KEY_OUTPUTS)
        runner.figure()
    except Exception as error:
        runner.report["errors"].append(f"{type(error).__name__}: {error}")
    finally:
        runner.report["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        runner.save()
    passed = sum(x["ok"] for x in runner.report["comparisons"])
    print(f"{'PASS' if runner.report['ok'] else 'FAIL'}: {len(runner.report['commands'])} commands; "
          f"{passed}/{len(runner.report['comparisons'])} comparisons passed")
    for error in runner.report["errors"]:
        print(error, file=sys.stderr)
    print(f"Report: {directory / 'report.json'}")
    print("Scope: offline packaged inputs; raw-source reacquisition and legal source verification are separate.")
    return 0 if runner.report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
