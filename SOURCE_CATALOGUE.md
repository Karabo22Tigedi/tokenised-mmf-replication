# Documentary source catalogue

The full inspected bibliography, including held versions and source URLs, is [references_revised.bib](drafts/independent/references_revised.bib). The dissertation supplies page, section and provision pinpoints. These source records distinguish the study's documentary vintage from later online versions.

## Prospectuses and permission evidence

[prep_permissions_matrix.csv](data/processed/prep_permissions_matrix.csv) records the source URL, accession, held raw filename, page/section and quoted basis for the permission coding. Main bibliography keys are `FranklinOnChain2026`, `WisdomTreeTMMDF2025`, `JPMorganOnChainLTMMF2026` and `FidelityTDF2026`. JPMorgan's August prospectus, October supplement and October SAI are distinct versions; do not treat their statements as a single undated text.

The permission matrix's quoted fragments remain inspectable here. Whole captured prospectus/SAI HTML and general reading PDFs are not redistributed. Original captured-file digests and disposition are in [input_provenance.csv](docs/input_provenance.csv). Quote verification against full omitted HTML requires the corresponding held documents; the offline numerical runner does not perform that documentary comparison.

## Issuer addresses and explorer labels

The original issuer parser used these held files:

- `2026-10-16_franklin_benji_contracts.html`
- `2026-10-16_franklin_stellar.toml`
- `2026-10-16_wisdomtree_wtgxx_fund_details.pdf`
- `2026-10-16_jpm_jltxx_press_release.html`

The published address extract preserves each returned record's source filename and verification status. The original parser is retained as `_original_issuer_addresses` in the publication script for inspection; its source-specific extraction logic remains visible. The retained TOML, original file hashes, bibliography and original parser identify the source trail. The WisdomTree PDF digest is `46b89ce70270121b4ece40200f833dc89ca37dd0b69447691ba715695497396e` (data as of 30 September 2026). Explorer title facts have per-source hashes in `data/source_facts/label_titles.csv`; these labels are ancillary identification evidence, not issuer confirmation.

Original retrieval endpoints, recorded with the held-source research catalogue:

| Held input | Source |
|---|---|
| Franklin contracts page | [BENJI contracts](https://digitalassets.franklintempleton.com/benji/benji-contracts/) |
| Franklin Stellar metadata | [Issuer stellar.toml](https://www.franklintempleton.com/.well-known/stellar.toml) |
| WisdomTree fund details, 30 September 2026 vintage | [WTGXX fund-details endpoint](https://dataspanapi.wisdomtree.com/pdr/documents/WEBSITE_FUND_DETAILS/WDT/US/EN-US/WTGXX_2) |
| JPMorgan, 13 May 2026 announcement | [Second tokenised fund on Ethereum](https://am.jpmorgan.com/us/en/asset-management/institutional/about-us/media/press-releases/jp-morgan-asset-management-launches-second-tokenized-fund-on-ethereum/) |

For each title-extract filename `ethereum_ADDRESS.html`, the original explorer URL is `https://etherscan.io/address/ADDRESS`; for `arbitrum_ADDRESS.html`, it is `https://arbiscan.io/address/ADDRESS`. Replace `ADDRESS` with the filename's full address, without `.html`. These endpoints identify the sources; their current content may differ from the captured October 17–18 titles.

## Legal and policy materials

The bibliography identifies the Financial Markets Act, Companies Act, CISCA, FAIS crypto-asset declaration, Exchange Control Regulations, authorised-dealer manual, NPS Act and held draft Crypto Asset Manual/CFM Regulations. The legal discussion's conclusions depend on the cited provisions and the token/register distinction, not on a script's output. Draft instruments remain drafts in the manuscript's held vintage. Numerical reproduction does not establish the legal analysis or verify every source quotation.

## Filing and transaction evidence

Held N-MFP3 XML, filing catalogues, response records and original manifests are under `data/raw/`; processed filing tables retain accession/date fields. The raw freeze and two exploratory additions are separate vintages. `data/processed/key_numbers.csv` identifies calculation outputs, units and dates. Navigate from a manuscript item through `EVIDENCE_MAP.md` to the relevant processed result and script, then to its held source inputs.
