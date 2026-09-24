# Autoresearch Hypothesis Lab

Manual-only Flask dashboard for reviewing the completed aggression autoresearch campaign, exploring new ideas on the already-spent discovery cohort, and freezing new preregistration drafts.

## Research boundary

- Runs exploratory compute only through `DiscoveryOnlyMatrix`; any split other than `discovery` raises.
- Labels every sandbox result `EXPLORATORY / DISCOVERY-ONLY / NOT EVIDENCE`.
- Reads immutable campaign artifacts and the active `campaign.sqlite` ledger in SQLite `mode=ro`; it never constructs `StateStore` or writes campaign state.
- Writes only under `workspace/analysis/autoresearch_lab/`.
- Creates hash-bound preregistration drafts and non-authorizing manifest scaffolds; it exposes no validation, quarantine, confirmation, or execution endpoint.
- Does not modify or connect to V1/V1.2/V2/V2.1/V3, SOD, Tailscale, Telegram, or automated execution.

## Lanes

1. **Evidence** — campaign decisions, eight-gate cell audits, discovery folds and power, E002 tradability diagnostics, the D001 243-cell map, exposure ledger, and manifest-chain integrity.
2. **Discovery Sandbox** — E001/E002/D001 or isolated custom candidates, parameterized outcomes, optional slices, 999-resample inference, optional circular shifts, and an append-only search journal.
3. **Preregister** — validates `PredictiveExperimentSpecV2` and `ExecutionContractV1`, computes canonical/effective hashes, checks exposure, and freezes a draft without opening a holdout.

## Manual launch

```powershell
py -3 -m scripts.autoresearch_lab.app --host 127.0.0.1 --port 5057
```

Open `http://127.0.0.1:5057/`. The discovery matrix warms in a background thread at startup; the first sandbox metadata request may wait for that checksum-bound cache.

## Lab outputs

```text
workspace/analysis/autoresearch_lab/
├── candidates/<source-digest>.py
├── journal/searches.jsonl
└── preregistrations/<experiment>_<spec-hash-prefix>/
    ├── spec.yaml
    ├── execution_contract.yaml
    ├── journal_excerpt.json
    ├── manifest_scaffold.json
    └── preregistration.json
```

## Verification

```powershell
py -3 -m pytest tests/test_autoresearch_lab.py -q
```

The focused suite verifies the split firewall, read-only campaign boundary, frozen-outcome fidelity, candidate isolation, integrity checks, exposure prechecks, and real campaign projections.
