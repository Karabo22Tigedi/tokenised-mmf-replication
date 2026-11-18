# Exploratory check, 18 November 2026: Franklin signer timing

Script: `scripts/25_check_franklin_signer_timing_2026_11_18.py`. Run with `python -I -B`. It needs no network access.

## Why it was run
The dissertation (Section 5.2) states that every key that signed Franklin's 44 observed holder-to-holder transfers was a signer on the issuer's own account *when it signed*. The processed classification in `data/processed/addl_signer_function.csv` labels these keys only as "current or former issuer-account signers". This check confirms the timing directly.

## Inputs (unchanged)
- `data/raw/stellar/freeze_2026-10-16/ops/*.jsonl.gz`: every `set_options` operation whose source is the BENJI issuer account. The earliest record in the crawl is 2025-08-28.
- `data/processed/addl_signer_function.csv`: the 44 FOBXX rows with `pair = hh`, giving their timestamps and signature keys.

## Outputs
| File | Content |
|---|---|
| `franklin_signer_timing.csv` | One row per (transfer, key): 88 key uses. Gives the key's weight on the issuer account at signing and its status. |
| `franklin_signer_timing_summary.csv` | One row per key (8 keys): the times it was added to and removed from the issuer account, its first and last use, its number of uses, and how many uses were not by a then-current issuer signer. |

## Result
None of the 88 key uses came from a key that was not then an issuer-account signer.
- Four keys were added on 9 October 2025, used between 23 January and 27 April 2026, and removed on 6 May 2026.
- Four keys were added on 6 May 2026 and used from 11 June 2026.

The frozen trustline records of 16 October 2026 give the issuer account and the sending holder accounts the same thresholds (low 2, medium 2, high 6). The keys used carry weight 1, and each transaction has two signatures. Authority over both kinds of account is therefore joint.

## Status
This is exploratory and was added after the data freeze. It changes no frozen file, frozen measure or verdict. It shows which keys signed and their status on the issuer account. It does not show which person or firm held those keys.
