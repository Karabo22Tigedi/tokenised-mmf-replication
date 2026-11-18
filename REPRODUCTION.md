# Offline reproduction

Use a complete checkout. The working directory may be anywhere; examples assume the repository root. Python 3.13 and the versions in `requirements.txt` were used. IANA timezone data is necessary, including on Windows.

Install the requirements before running a reproduction route. If using a virtual environment, create it outside the checkout: the inventory verifier deliberately rejects unlisted files in the release directory. Use that environment's Python for both installation and execution. On Windows a sibling environment can be created with `python -m venv ../mmf-replication-env`; its interpreter is `../mmf-replication-env/Scripts/python.exe` (on Linux/macOS, `../mmf-replication-env/bin/python`).

```text
python -m pip install -r requirements.txt
python -I -B verify.py
python -I -B reproduce.py --quick
python -I -B reproduce.py --main
python -I -B reproduce.py --extended
```

Run `python -I -B reproduce.py --help` for the authoritative command options. The main route includes quick checks; the extended route includes both main and quick checks. Inspect each run's result and logs rather than inferring that a successful quick run covers the main or extended routes.

## Coverage

The quick route runs the original unit tests, then (from version 1.1) script 24, the exploratory Fidelity class comparison dated 17 November 2026, then (from version 1.2) script 25, the Franklin signer-timing check dated 18 November 2026, which must reproduce `data/processed/exploratory/2026-11-18/franklin_signer_timing.csv` and `franklin_signer_timing_summary.csv` exactly from the packaged Stellar operations and signer table, then script 07's numerical index and historical verdicts, and the revised calendar renderer. Script 24 re-parses the packaged Fidelity N-MFP3 XML filings and must reproduce `data/processed/exploratory/2026-11-17/fidelity_class_daily.csv`, `fidelity_class_monthly.csv` and `fidelity_class_gap_summary.csv` exactly. Its fee-term file is a documented source fact (see below), not a regenerated result. The quick route also compares the generated index, verdict and twin-description CSVs with their distributed references. The figure is regenerated from the frozen calendar counts; PDF/PNG binary identity is not expected because rendering metadata and platforms can differ.

The main route runs script 05 with the study's full window, earlier-month test dates, quarter ends and sensitivities. It derives earlier-month dates from governing rows of `freeze_nmfp_summary.csv`, rather than shortening the test to a single month. It compares regenerated outputs cell for cell with their distributed references, including row order except for the explicitly unordered records described below.

The extended route runs the main computation, then scripts 13, 15, 16, 07, 17, 18, 19, 20, 22, 23 and final 07, then the revised figure renderer. It reconstructs event lists, signer/control classifications, concentration, earlier-month and ordering diagnostics, routing/cash legs and supporting descriptive results.

The runner verifies the release inventory first, copies inputs and code into a new `.reproduction/` run directory, removes the outputs each stage declares it will regenerate, and runs those stages there. Failures and mismatches stop the run and retain the evidence. Original reference files remain unchanged. A successful process exit alone is insufficient: the runner also checks declared outputs and their CSV content. Network operations and subprocess creation from within scientific scripts are blocked by a Python audit hook; this is an execution safeguard, not a general security sandbox.

Two files have order ties that can vary between Python processes: `freeze_wallet_roles.csv` is emitted from a set, and `addl_ta_hop_parking.csv` inherits that order for equal-value records. For these files, comparison matches unique `(fund, chain, address)` keys and requires identical headers and every field value; missing, duplicate or different records fail. There is no numerical tolerance. All other declared CSVs retain the ordered comparison. The recorded reproduction exposed these ordering differences; the saved classifications and values themselves were identical.

## Starting points and limits

The main computation starts from frozen parsed N-MFP3 tables, event/call records, supply snapshots and the complete Stellar operation cache. The extended route starts from those records plus the October 17–18 exploratory response caches. It is **not** a fresh acquisition from live APIs or a full independent re-extraction of every documentary input.

Whole issuer PDF/HTML pages are replaced by factual CSV extracts produced with the original held-source parsers. Version 1.1 applies the same rule to the Fidelity 485BPOS prospectus: `data/source_facts/fidelity_fee_terms.csv` holds the verbatim fee-table values, audited expense ratios and quoted sentences with their headings, and script 24 copies it when the HTML is absent. Explorer titles are preserved in a title extract. The input adapter and exact original/exported script hashes are recorded in `docs/code_adaptations.csv`. Unused full Solidity source and Swarm metadata are omitted from the exported October 17 call log. No analytical code reads those omitted fields. See `DATA_AVAILABILITY.md` for the distinction between original raw records, extracts and publication copies.

Original raw manifests also identify omitted documents. They are provenance records, not the selected release inventory. The release inventory is `MANIFEST.csv`; the disposition of every original manifested input is in `docs/input_provenance.csv`.

Acquisition helpers remain in some scientific modules because analysis imports their ABI/address utilities. Do not execute acquisition entry points: doing so would change the vintage and require additional configuration. Contact strings have been removed from publication copies. Original `run_freeze.py` and its private workflow dependencies are intentionally outside this package.

## Build the dissertation

From the repository root, with a TeX installation supplying the packages declared in the source and the `agsm` bibliography style:

```text
pdflatex -interaction=nonstopmode -halt-on-error -output-directory=drafts/independent drafts/independent/dissertation_revised.tex
bibtex drafts/independent/dissertation_revised
pdflatex -interaction=nonstopmode -halt-on-error -output-directory=drafts/independent drafts/independent/dissertation_revised.tex
pdflatex -interaction=nonstopmode -halt-on-error -output-directory=drafts/independent drafts/independent/dissertation_revised.tex
pdflatex -interaction=nonstopmode -halt-on-error -output-directory=drafts/independent drafts/independent/dissertation_revised.tex
```

Use a disposable checkout if building, because these commands overwrite the supplied PDF and generate auxiliary files. Check the last log and repeat the final LaTeX command if it still requests a rerun; table-of-contents page references must settle. The source's bibliography path assumes execution from the repository root. The calendar figure and logo resolve through `figures/`. The supplied PDF is the reference version; local TeX versions and timestamps may change binary output even when content is unchanged.
