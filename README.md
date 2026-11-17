# Tokenised money-market funds: dissertation evidence and replication

Researcher: Karabo Tigedi. This repository accompanies the dissertation on operational transfer authorisation, legal record authority and tokenised government money-market funds. The empirical window is 1 January–30 September 2026; documentary and exploratory vintages are identified separately.

**Version 1.1** (prepared 17–18 November 2026) supersedes v1.0 for the final dissertation. It adds the dated exploratory Fidelity class comparison (`data/processed/exploratory/2026-11-17/`, script 24), the matching final dissertation PDF and source, and a reproduction stage for script 24. Version 1.0 remains available unchanged at its tag.

## Read the dissertation and inspect its evidence

- [Dissertation PDF](drafts/independent/dissertation_revised.pdf)
- [Table, figure and claim evidence map](EVIDENCE_MAP.md)
- [Scientific rules, calibration and provenance](METHODS.md)
- [Data availability and limitations](DATA_AVAILABILITY.md)
- [Documentary source catalogue](SOURCE_CATALOGUE.md)
- [Recorded validation and its limits](VALIDATION.md)

The main numerical index is [key_numbers.csv](data/processed/key_numbers.csv). Each row names a quantity, value, unit, date and underlying output. The evidence map also identifies exploratory outputs that do not appear in that index. In GitHub, open a CSV and select **Raw** or **Download** when a large preview is unavailable.

## Verify or reproduce

Download the complete repository or clone it. Python 3.13 was used for the recorded checks. From the repository root:

```text
python -I -B verify.py
python -m pip install -r requirements.txt
python -I -B reproduce.py --quick
```

`verify.py` needs no third-party Python packages and checks the selected release files against their SHA-256 inventory. The quick route reruns the rules tests, re-derives the exploratory Fidelity class comparison from the packaged filings (v1.1), builds the numerical index/verdicts and renders the figure. It is not a recomputation of every measurement from raw records. See [REPRODUCTION.md](REPRODUCTION.md) for the main and extended routes, exact coverage and output comparisons.

The replication runner blocks network calls and uses a disposable copy. It never refreshes the study or overwrites the distributed reference outputs. Its logs and comparison results are saved under `.reproduction/`.

## What this repository contains

| Location | Contents |
|---|---|
| `drafts/independent/` | Current dissertation PDF, LaTeX source and bibliography |
| `figures/` | Dissertation calendar figure and title-page logo |
| `data/processed/` | Frozen measurements, sensitivities, exploratory results and numerical index |
| `data/raw/` | Selected held numeric source records, complete frozen Stellar cache and original input manifests |
| `data/source_facts/` | Documented address/module/title extracts from held issuer and explorer documents |
| `scripts/` | Analysis code and tests, with documented publication input adapters |
| `docs/` | Original-input provenance and publication code-change records |

This is a curated snapshot with its own publication history. Original research-history identifiers quoted in the dissertation or methods document refer to the private working record, not to commits in this repository. Private correspondence, previous drafts, evaluation reports and administrative submission documents are outside this research package.

The study has material limits: incomplete Aptos history, a non-issuer-confirmed Fidelity token address, post-pilot calibration, and a distinction between technical signatures and legal authority. Reproduction checks establish computational consistency of the included evidence; they do not remove those limits or establish the legal interpretation.

Third-party documents retain their original rights. Inclusion of factual extracts and public numeric records does not grant rights over the underlying publications. No general licence over third-party material is asserted here.
