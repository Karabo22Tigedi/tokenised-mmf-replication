# Data availability and provenance

The package distributes the held numerical evidence needed for the documented main and extended offline computations, including the complete frozen Stellar cache. It does not acquire a new vintage. Missing Aptos history remains missing, and no omitted chain is imputed as zero. Fidelity's token-address status remains unverified by an issuer source.

## Three kinds of input

1. **Byte-identical frozen files.** Selected raw XML, JSON/JSONL, compressed response pages, TOML and derived-pull CSVs retain the original bytes. Every original manifested input was checked against its archived size and SHA-256 during export. Distributed processed CSVs retain their saved bytes.
2. **Explicit factual extracts.** `data/source_facts/issuer_contracts.csv` contains the 18 address/status/source-file records returned by the original issuer parser. `franklin_modules.csv` contains the module-address facts returned by the original measurement parser. `label_titles.csv` contains exact normalised HTML titles, original paths and source hashes. They replace full third-party source pages for offline computation. The issuer-status qualifications are part of the data, including the Fidelity qualification. A successful run does not independently revalidate these extracts against omitted documents.
3. **A documented transformed response log.** In the October 17 `evm_calls.jsonl`, full `SourceCode` and `SwarmSource` fields in explorer contract-source responses are omitted. The analysis uses retained ABI, contract-name, implementation and other fields. All remaining records are retained. Its original digest and transformation are recorded in `docs/input_provenance.csv`; its release digest is in `MANIFEST.csv`.

Whole third-party reading PDFs, issuer PDFs, captured web pages, private notes and acquisition text logs are not redistributed. Citation metadata, source URLs, held versions and permission-matrix quotations identify the documentary evidence. Downloading a source's latest version does not necessarily reproduce the held version. Rights or access restrictions may require the reader to obtain documents directly from their publisher or official source. The package does not claim that excluded texts can be regenerated from a digest.

4. **Version 1.1 exploratory addition (17 November 2026).** `data/processed/exploratory/2026-11-17/` holds a dated exploratory analysis of the existing held Fidelity N-MFP3 filings. It covers the same-date class gap, its decomposition, the overlap of seven-day yields, gross-minus-net yields, filed waivers and cross-class flows. It acquired no new data and changed no frozen file or measure. Its fee-term file uses the held 485BPOS prospectus. That HTML is not redistributed. `data/source_facts/fidelity_fee_terms.csv` is the verbatim extract: fee-table values, audited Financial Highlights ratios, and quoted sentences with headings and offsets. Those quotations were checked word for word against the held HTML in an independent audit in the private working record.

## Manifests

`data/raw/FREEZE_MANIFEST_2026-10-16.csv` and the two `ADDITION_MANIFEST` files are unmodified historical inventories. Some paths in them are deliberately absent from this curated snapshot. `docs/input_provenance.csv` gives each original path, size, hash and release disposition. `docs/code_adaptations.csv` records publication-specific script changes and their original/release hashes.

`MANIFEST.csv` covers the files actually distributed, except itself. `verify.py` validates those bytes and reports unexpected files outside `.git/` and `.reproduction/`. An inventory hash detects changes relative to that inventory; it is not a digital signature, independent timestamp or source-authenticity certificate.

## Interpretation

Calibration decisions followed exposure to the July–September pilot. Earlier-month checks are useful robustness evidence but do not establish wholly prospective preregistration. Technical transaction signers, account categories and on-chain ownership do not by themselves identify beneficial owners or decide legal record authority. F4's historical partial codes do not score its unobserved lag condition. See the dissertation and `METHODS.md` for the consequences of these limits.
