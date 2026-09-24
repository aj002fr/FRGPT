# Aggression Autoresearch Protocol — Frozen Phase 2 Design

## Boundary

This package is a cooperative, tamper-evident research harness, not a security
sandbox. It detects accidental leakage and post-freeze changes by strict
schemas, content hashes, immutable identities, receipts, and transactional
state. Python run by the same Windows user remains trusted.

E001, E002, and D001 methodology is frozen in `registry/experiments/`, with
authoritative methods in `docs/autoresearch_campaign_design.md`. Historical
discovery, one-shot validation, and non-evidentiary quarantine are complete and
immutable. E001/E002 are `not_confirmed`; exposed historical dates must not be
retuned or relabeled as untouched. D001 nominations remain proposal-only.
Confirmation requires a new spec-bound owner approval. This package must not
import live dashboard/research scripts, access `*_live` paths, use network
services, or control processes.

## Ownership and lifecycle

- Candidate code receives causal feature rows only and returns each input UTC
  `formation_ts` plus the exact registered finite feature map. Cells and numeric
  direction are evaluator-owned.
- The fixed evaluator alone owns targets, future paths, costs, censoring,
  sample selection, date weighting, and baseline/augmented comparisons.
- Candidate fitting is discovery-only:
  `fit(discovery) -> frozen_state -> transform(split, frozen_state)`.
  Validation, quarantine, and confirmation are transform-only.
- Candidate execution uses a target-free subprocess, a stripped environment,
  an empty working directory, a timeout, and best-effort socket disabling.
  These controls support the cooperative threat model only.

## Evidence protocol

Primary inference is paired: baseline and augmented losses remain joined while
complete session dates receive equal weight. Validation/confirmation use the
studentized date-cluster wild bootstrap; serial falsification uses within-session
blocks/circular shifts of at least 30 minutes. Registered families use Holm/FWER
with their original denominator. Observation-level tests are not confirmatory.

A unique run identity binds experiment, hypothesis family, split, candidate
hash, frozen-state hash, evaluator hash, and data hash. SQLite transitions are:
`RESERVED -> COMPLETED` or `RESERVED -> FAILED_NO_RESULT`. A crash can be
retried only for the identical failed identity when no statistics were emitted.
Outputs are atomically replaced before completion is committed.

Confirmation requires a separate approval bound to every identity hash,
canonical spec hash, primary claim, exact cutoff/date manifest, approver, and
UTC timestamp. `begin_confirmation()` retires and ledgers the cohort atomically
before targets load. Exposure applies to the whole semantic hypothesis family,
including later children.

