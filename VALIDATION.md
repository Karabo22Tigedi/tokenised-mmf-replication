# Recorded validation: version 1.0

Validation date: 15 November 2026. Python 3.13 was used on Windows. This note reports observed checks, not a dissertation grade or an independent validation of legal conclusions.

- All **55 original unit tests** passed without skips.
- The complete offline scientific route was executed in isolated scratch: scripts 05, 13, 15, 16, 07, 17, 18, 19, 20, 22, 23, final 07 and the revised calendar renderer.
- **80 CSV comparisons covering 76 distinct output files** passed. Headers and every field string match the distributed references exactly. Two documented set/tie-order tables are matched by unique fund/chain/address keys; other outputs retain exact row order. No numerical tolerance was used.
- The figure renderer completed successfully. Its printed burn totals, modal hours/shares and non-business value shares matched the calendar input for all four funds. Nonempty PDF and PNG outputs were checked; binary image identity is not asserted.
- All 3,246 original manifested input sizes/hashes were checked during export. Published address/module/title extracts were independently compared with the original held-source parser results and title pages.

## Repairs to the validation process

The first attempts stopped at two order-only CSV differences. Full records were identical: the original wallet-role writer iterates a set, and a later parking table inherits that order within equal-value ties. The final comparator requires unique keys and identical complete records for those two files; field changes, duplicate keys, missing records and changed headers still fail.

The remaining scientific stages were completed in the same isolated working copy with a separate continuation report, after rechecking all earlier CSV results. The figure then exposed a missing `python-dateutil` dependency in the original validation environment. It was installed in a separate environment and the figure was rerun successfully. Failed reports were preserved in the private validation record. This was a completed analytical route with documented continuations, rather than one uninterrupted successful invocation. The final public entry point implements the two comparison rules directly; dependencies are specified in `requirements.txt`.

Source data, scientific algorithms and reference results were not changed to obtain these passes. The checks establish computational consistency conditional on the included parsed tables and factual extracts. They do not reproduce live acquisition, independently recover omitted third-party texts, complete missing chain history or establish legal record authority. See `DATA_AVAILABILITY.md` and `METHODS.md`.
