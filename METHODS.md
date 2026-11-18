# Scientific rules, calibration and original-history provenance

The threshold record was committed as `76e76d2` at 2026-10-16T17:24:30+02:00; the raw freeze was committed as `07afeb3` at 2026-10-16T22:42:27+02:00. These timestamps were recorded in the original private research history. These identifiers refer to that original history and are not resolvable as commits in this publication repository. They document the reported sequence; the snapshot alone does not independently prove that sequence. Pilot exposure predates the threshold record.

TR-1 uses 23:59:59 ET report-date cutoffs, with UTC as a sensitivity. TR-2 recognises next-business-day burns of balances parked in the JPMorgan/Fidelity burn wallets on the arrival date. TR-3 adds next-business-day dividend mints only where rule (iii) links them to a reported subscription; all next-day dividends form a sensitivity. TR-4 treats weekends and held Fedwire closures in 2024-2026 as non-business days. Before 2024, only historical role/label processing may use the weekend fallback, with diagnostics.

TR-5 assigns a behavioural TA set from token mint sources, listed Franklin modules, EVM burn transaction senders, redemption parking and forwarding of issuance mints. Non-TA contracts form the upper bound and holder-to-holder transfers the lower bound. TR-6 labels dividend mints through a proportional existing-holder batch (correlation at least 0.95 with a five-times-accrual cap), individual accrual proximity within 2%, or single-chain unlabelled mints on the first ET business day matching N-MFP3 gross subscriptions within 0.01 shares. Other mints are issuance. The exact algorithms and tie handling are in `rules.py`; Appendix A gives the operational rules.

The eleven post-pilot choices are: Stellar initial-balance seeding; TR-3 rule-(iii)-only scope; EVM burn transaction sender with the burn leg off on Stellar/Solana; excluding redemption forwards from the hop leg; dropping the batch-median outlier guard; accrual cap k=5; rule-(iii) first-business-day/0.01-share test; issuance-only hop triggers; N-MFP3-report-date M4 lag; mint-source-only TA batches; and the one-sided incomplete-chain F0 exceedance test. Each available sensitivity is named in 05's `SENSITIVITIES`; UTC, broader TR-3 scope, M4 variant-A lag and all-events M1 also appear in main outputs/07 verdict rows.

F0 requires more than two earlier-month exceedances over 0.10% of class shares, counted on ET rows once per month; incomplete months count only when covered supply already exceeds the filed total. F1 uses pooled non-dividend transfers and fails at upper-bound value at least 5% or count at least 10%. F2 uses non-business issuance/redemption value at least 10%. F3 uses outside-top-ten non-TA supply at least 25%, only for Franklin/WisdomTree at 30 September. F4 is the conjunction of business-only dividend dates, TA batches and median lag at least one business day. A source-only TA batch holds by construction on these token mint definitions. Rule-(iii) main lag is also one business day by construction; its monthly-accrual-size sensitivity is the informative alternative. Historical partial F4 labels must remain distinguished from the full conjunction.

Stellar supply uses events from 1 September 2025; role/dividend rules use events from 1 January 2026, seeded by backing out current balances. Negative backed-out seeds receive no positive seed. Earlier mints without a yield benchmark cannot use the individual-accrual rule or batch cap. Skipped holder-created claimable balances have only a conditional omission bound under fixed roles, not a guaranteed bound after complete role reassignment. Diagnostic counts are in `freeze_diagnostics.csv` and `freeze_diag_stellar_claimable.csv`.

Original methodological record SHA-256 values (records themselves remain outside this snapshot): `preregistration.md` = `2ac816b82d147e9098f76850d23e3555ece7957da6bcb20fd693d2470096d471`; `preregistration_implementation_notes.md` = `5520a3f4ad895050b4095cdfedd3074edd0a846b9d069cb2b233e3ae82397fc9`. These identify records inspected here, not evidence that a fresh checkout contains them.


## Version 1.2: exploratory Franklin signer-timing check (18 November 2026)

`scripts/25_check_franklin_signer_timing_2026_11_18.py` was added after the readability review of the final dissertation. It is not a pre-specified measure, and it changes no frozen rule, threshold or output.

What it does:
- It reads every `set_options` operation sourced by the BENJI issuer account in the frozen Stellar operation crawl. The earliest record in the crawl is 28 August 2025.
- For each of the 44 FOBXX holder-to-holder transfers in `addl_signer_function.csv`, it reports each signature key's weight on the issuer account at the transaction time.

Result: none of the 88 key uses came from a key that was not then an issuer-account signer. The frozen account records give the issuer and sending holder accounts the same thresholds (2/2/6), and the keys used carry weight 1, so authority over both is joint. It does not identify who held the keys.

## Version 1.1: exploratory Fidelity class comparison (17 November 2026)

After supervisor feedback, a dated exploratory analysis of the existing held data was added: `scripts/24_explore_fidelity_class_gap_2026_11_17.py`. It is not a pre-specified measure, and it changes no frozen rule, threshold or output.

What it does:
- It re-parses the 22 original Fidelity N-MFP3 filings for series S000089156, with no amendments.
- It compares the Liquidity (C000255665) and OnChain (C000261149) seven-day net yields on the same dates.
- It decomposes the full-period gap arithmetically by month and by the five launch-week OnChain reports.
- It treats overlapping seven-day yields with a non-overlapping weekly sample.
- It reports gross-minus-net yields and filed waivers. The balance reconstruction behind the waiver rates is approximate.
- It lists same-day cross-class flows, labelled only as consistent with conversion.

How to read it:
- The decomposition is arithmetic, not causal.
- The launch-week values are reported as filed. They are consistent with an incomplete seven-day calculation window, but no held source documents that convention.
- No causal cost of tokenisation is identified.

An independent agent audit in the private working record recomputed every quantity from the raw XML. The release reproduces the CSV outputs exactly on the quick route. See `data/processed/exploratory/2026-11-17/README.md`.
