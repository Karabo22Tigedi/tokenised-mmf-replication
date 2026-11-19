# Recorded validation: version 1.0

Python 3.13 was used on Windows. This note reports observed checks, not a dissertation grade or an independent validation of legal conclusions.

- All **55 original unit tests** passed without skips.
- The complete offline scientific route was executed in isolated scratch: scripts 05, 13, 15, 16, 07, 17, 18, 19, 20, 22, 23, final 07 and the revised calendar renderer.
- **80 CSV comparisons covering 76 distinct output files** passed. Headers and every field string match the distributed references exactly. Two documented set/tie-order tables are matched by unique fund/chain/address keys; other outputs retain exact row order. No numerical tolerance was used.
- The figure renderer completed successfully. Its printed burn totals, modal hours/shares and non-business value shares matched the calendar input for all four funds. Nonempty PDF and PNG outputs were checked; binary image identity is not asserted.
- All 3,246 original manifested input sizes/hashes were checked during export. Published address/module/title extracts were independently compared with the original held-source parser results and title pages.

## Repairs to the validation process

The first attempts stopped at two order-only CSV differences. Full records were identical: the original wallet-role writer iterates a set, and a later parking table inherits that order within equal-value ties. The final comparator requires unique keys and identical complete records for those two files; field changes, duplicate keys, missing records and changed headers still fail.

The remaining scientific stages were completed in the same isolated working copy with a separate continuation report, after rechecking all earlier CSV results. The figure then exposed a missing `python-dateutil` dependency in the original validation environment. It was installed in a separate environment and the figure was rerun successfully. Failed reports were preserved in the private validation record. This was a completed analytical route with documented continuations, rather than one uninterrupted successful invocation. The final public entry point implements the two comparison rules directly; dependencies are specified in `requirements.txt`.

Source data, scientific algorithms and reference results were not changed to obtain these passes. The checks establish computational consistency conditional on the included parsed tables and factual extracts. They do not reproduce live acquisition, independently recover omitted third-party texts, complete missing chain history or establish legal record authority. See `DATA_AVAILABILITY.md` and `METHODS.md`.

## Version 1.4 (validated 19 November 2026)

Version 1.4 replaces the dissertation source and PDF with the submitted final version (5,900 counted words, 42 pages): a one-sentence definition of Form N-MFP3 in Section 3.2, the Appendix A glossary as a list, and appendix paragraph-heading punctuation and spacing. Data, scripts and reference results are unchanged. Versions 1.0 to 1.3 remain unchanged at their tags.

- `verify.py` passed on the staged export: 3,335 files.
- `reproduce.py --quick` passed in a clean environment built from `requirements.txt` (Python 3.13, Windows): 5 commands and 10 of 10 CSV comparisons.
- The dissertation PDF in this version is byte-identical to the submitted final PDF.
- The main and extended routes are unchanged from version 1.0 and were not re-executed for this version.

## Version 1.3 (validated 18 November 2026)

Version 1.3 replaces the dissertation source and PDF with the final readability-revised version (5,895 counted words, 41 pages; new Appendix F) and updates this documentation. Data, scripts and reference results are unchanged from version 1.2. Versions 1.0, 1.1 and 1.2 remain unchanged at their tags.

- `verify.py` passed on the staged export: 3,335 files.
- `reproduce.py --quick` passed in a clean environment built from `requirements.txt` (Python 3.13, Windows): 5 commands and 10 of 10 CSV comparisons.
- The dissertation PDF in this version is byte-identical to the submitted final PDF.
- The main and extended routes are unchanged from version 1.0 and were not re-executed for this version.

## Version 1.2

Version 1.2 adds the final, readability-revised dissertation (5,810 counted words, 40 pages) and the exploratory Franklin signer-timing check (script 25 and `data/processed/exploratory/2026-11-18/`), with a quick-route reproduction stage. Versions 1.0 and 1.1 remain unchanged at their tags.

- The package passes `verify.py` (staged export: 3,335 files plus MANIFEST.csv).
- `reproduce.py --quick` passed in a clean environment built from `requirements.txt` (Python 3.13, Windows): 5 commands and 10 of 10 CSV comparisons, including exact reproduction of `franklin_signer_timing.csv` and `franklin_signer_timing_summary.csv` from the packaged Stellar operations.
- Before release, the signer-timing result was independently confirmed from the raw issuer operation history by three separate reviews in the private working record.
- The main and extended routes are unchanged from version 1.0 and were not re-executed for this version.

## Version 1.1

Version 1.1 adds the exploratory Fidelity class comparison (script 24 and `data/processed/exploratory/2026-11-17/`), the final dissertation and a quick-route reproduction stage. Version 1.0 remains unchanged at its tag.

- `verify.py` passed: 3,331 files, 238,056,704 bytes.
- `reproduce.py --quick` passed in a clean environment built from `requirements.txt` (Python 3.13, Windows): 4 commands and 8 of 8 CSV comparisons. They include exact reproduction of `fidelity_class_daily.csv`, `fidelity_class_monthly.csv` and `fidelity_class_gap_summary.csv` from the packaged N-MFP3 XML. The figure renderer completed.
- The fee-term file is a documented source fact, because the prospectus HTML is not redistributed. Its adaptation is recorded in `docs/code_adaptations.csv`.
- Before release, an independent agent audit in the private working record recomputed every exploratory quantity from the raw filings and checked each fee quotation word for word against the held prospectus.
- The main and extended routes are unchanged from version 1.0 and were not re-executed for this version. Their recorded results above still apply to the unchanged frozen outputs.
- Running the quick route in an environment without `requirements.txt` installed failed at the figure step (missing `packaging`). That was an environment fault, not a package fault.
