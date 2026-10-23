"""Focused tests for the resumable archive-acquisition slice (X0).

No test makes a network call: Databento responses are faked via
``FakeClient`` and downloads via an injected ``fetch_file``/``disk_usage``.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent))  # repo root

from scripts.move_exhaustion_v2.data.archive_fetch import (  # noqa: E402
    ApiKeyError,
    ArchiveFetchError,
    ChecksumError,
    CostExceededError,
    HeadroomError,
    active_job_id,
    atomic_write_json,
    batch_fingerprint,
    check_headroom,
    download_job,
    file_matches,
    http_fetch,
    load_api_key,
    load_batch_config,
    load_file_state,
    load_jobs_index,
    main,
    parse_cost,
    parse_file_entry,
    parse_job,
    record_job,
    submit_job,
    validate_batch_config,
    verify_job,
)

UTC = timezone.utc
FIXED_NOW = datetime(2026, 8, 11, 12, 0, 0, tzinfo=UTC)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_http_fetch_requires_authentication(tmp_path):
    with pytest.raises(ApiKeyError):
        http_fetch("https://example.invalid/file", tmp_path / "file")


def make_remote(name: str, data: bytes, *, status: str = "done") -> dict:
    return {
        "filename": name,
        "size": len(data),
        "hash": f"sha256:{sha256_bytes(data)}",
        "urls": {"https": f"https://batch.example.invalid/{name}"},
        "status": status,
    }


def make_fetch(payloads: dict[str, bytes]):
    calls: list[tuple[str, Path, int]] = []

    def fetch(url: str, dest: Path, resume_from: int = 0) -> None:
        dest = Path(dest)
        calls.append((url, dest, resume_from))
        data = payloads[url]
        if resume_from:
            with open(dest, "ab") as fh:
                fh.write(data[resume_from:])
        else:
            dest.write_bytes(data)

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


JOB = {
    "job_id": "GLBX-20220101-00000000-ABCDE",
    "cost": 0.0,
    "state": "done",
}


class FakeClient:
    """In-memory stand-in for databento.Historical (batch + metadata)."""

    def __init__(
        self,
        *,
        cost: float = 0.0,
        job: dict | None = None,
        jobs: list[dict] | None = None,
        files: list[dict] | None = None,
    ) -> None:
        self.cost = cost
        self.job = dict(job if job is not None else JOB)
        self.jobs = [dict(j) for j in (jobs if jobs is not None else [JOB])]
        self.files = [dict(f) for f in (files or [])]
        self.get_cost_calls: list[dict] = []
        self.submit_kwargs: dict | None = None
        self.list_files_job_ids: list[str] = []
        self.metadata = self
        self.batch = self

    def get_cost(self, **kwargs):
        self.get_cost_calls.append(kwargs)
        return self.cost

    def submit_job(self, **kwargs):
        self.submit_kwargs = dict(kwargs)
        return self.job

    def list_jobs(self, states=None):
        return self.jobs

    def list_files(self, job_id):
        self.list_files_job_ids.append(job_id)
        return self.files


# ---------------------------------------------------------------------------
# Frozen batch config
# ---------------------------------------------------------------------------


def test_frozen_batch_matches_authorization():
    cfg = load_batch_config()
    b = cfg["batch"]
    assert cfg["archive_root"] == r"C:\FRDBs\FutMBP\move_exhaustion_v2_source"
    assert b["dataset"] == "GLBX.MDP3"
    assert b["symbols"] == "ZN.FUT"
    assert b["stype_in"] == "parent"
    assert b["schema"] == "mbp-1"
    assert b["start_inclusive"] == "2022-01-01"
    assert b["end_exclusive"] == "2026-04-02"
    assert b["encoding"] == "csv"
    assert b["compression"] == "zstd"
    assert b["split_duration"] == "month"
    assert b["one_shot_only"] is True
    assert b["estimated_cost_usd_at_approval"] == 0.0
    validate_batch_config(b)  # must not raise


def test_batch_fingerprint_stable_and_sensitive():
    b = load_batch_config()["batch"]
    assert batch_fingerprint(b) == batch_fingerprint(b)
    mutated = dict(b)
    mutated["end_exclusive"] = "2026-04-03"
    assert batch_fingerprint(mutated) != batch_fingerprint(b)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dataset", "XNAS.ITCH"),
        ("symbols", "ES.FUT"),
        ("stype_in", "raw_symbol"),
        ("schema", "mbp-10"),
        ("encoding", "dbn"),
        ("compression", "none"),
        ("split_duration", "day"),
        ("one_shot_only", False),
        ("estimated_cost_usd_at_approval", 1.0),
    ],
)
def test_validate_batch_config_rejects_deviations(field, value):
    b = dict(load_batch_config()["batch"])
    b[field] = value
    with pytest.raises(ArchiveFetchError):
        validate_batch_config(b)


def test_validate_batch_config_rejects_missing_fields():
    with pytest.raises(ArchiveFetchError, match="missing required fields"):
        validate_batch_config({})


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def test_parse_cost_variants():
    assert parse_cost(0) == 0.0
    assert parse_cost("0.0000") == 0.0
    assert parse_cost(1.25) == 1.25
    with pytest.raises(ArchiveFetchError):
        parse_cost(None)
    with pytest.raises(ArchiveFetchError):
        parse_cost(-1)
    with pytest.raises(ArchiveFetchError):
        parse_cost("nan")
    with pytest.raises(ArchiveFetchError):
        parse_cost(True)


def test_parse_job_extracts_id_and_cost():
    parsed = parse_job({"job_id": JOB["job_id"], "cost": 0.0, "state": "queued"})
    assert parsed == {
        "job_id": JOB["job_id"],
        "cost_usd": 0.0,
        "state": "queued",
    }
    parsed2 = parse_job({"id": JOB["job_id"], "cost_usd": "0.0000", "status": "done"})
    assert parsed2["cost_usd"] == 0.0
    assert parsed2["state"] == "done"


def test_parse_job_missing_cost_fails_closed_when_required():
    with pytest.raises(ArchiveFetchError, match="refusing to assume a cost"):
        parse_job({"job_id": JOB["job_id"], "state": "queued"}, require_cost=True)
    parsed = parse_job({"job_id": JOB["job_id"], "state": "queued"}, require_cost=False)
    assert parsed["cost_usd"] is None


def test_parse_job_missing_id_rejected():
    with pytest.raises(ArchiveFetchError, match="missing job_id"):
        parse_job({"cost": 0.0})


def test_parse_file_entry_and_unsafe_names():
    data = b"abc"
    entry = make_remote("GLBX-2022-01.csv.zst", data)
    parsed = parse_file_entry(entry)
    assert parsed["filename"] == "GLBX-2022-01.csv.zst"
    assert parsed["size"] == 3
    assert parsed["sha256"] == sha256_bytes(data)
    assert parsed["status"] == "done"

    for bad in (
        "../evil.csv.zst",
        "a\\b.csv.zst",
        "a/b.csv.zst",
        ".hidden.csv.zst",
        "x.part",
    ):
        with pytest.raises(ArchiveFetchError, match="unsafe"):
            parse_file_entry(make_remote(bad, data))


def test_parse_file_entry_rejects_bad_hash_or_missing_urls():
    entry = make_remote("x.csv.zst", b"abc")
    entry["hash"] = "md5:123"
    with pytest.raises(ArchiveFetchError, match="hash"):
        parse_file_entry(entry)
    entry2 = make_remote("x.csv.zst", b"abc")
    del entry2["urls"]
    with pytest.raises(ArchiveFetchError, match="missing"):
        parse_file_entry(entry2)
    entry3 = make_remote("x.csv.zst", b"abc")
    del entry3["hash"]
    with pytest.raises(ArchiveFetchError, match="hash"):
        parse_file_entry(entry3)


# ---------------------------------------------------------------------------
# Submit: exact config, cost recheck, one-shot
# ---------------------------------------------------------------------------


def test_submit_refuses_nonzero_estimate_before_submitting():
    cfg = load_batch_config()
    client = FakeClient(cost=1.0)
    with pytest.raises(CostExceededError, match="estimated cost"):
        submit_job(client, cfg)
    assert client.submit_kwargs is None


def test_submit_uses_exact_frozen_params():
    cfg = load_batch_config()
    client = FakeClient(cost=0.0)
    job = submit_job(client, cfg)
    assert job["job_id"] == JOB["job_id"]
    kwargs = client.submit_kwargs
    b = cfg["batch"]
    assert kwargs["dataset"] == b["dataset"]
    assert kwargs["symbols"] == b["symbols"]
    assert kwargs["schema"] == b["schema"]
    assert kwargs["start"] == b["start_inclusive"]
    assert kwargs["end"] == b["end_exclusive"]
    assert kwargs["encoding"] == "csv"
    assert kwargs["compression"] == "zstd"
    assert kwargs["split_duration"] == "month"
    assert kwargs["stype_in"] == "parent"
    assert kwargs["stype_out"] == "instrument_id"
    assert kwargs["delivery"] == "download"
    estimate_call = client.get_cost_calls[-1]
    assert estimate_call["dataset"] == b["dataset"]
    assert estimate_call["schema"] == b["schema"]
    assert estimate_call["stype_in"] == b["stype_in"]


def test_submit_response_nonzero_cost_fails_closed():
    cfg = load_batch_config()
    job = dict(JOB)
    job["cost"] = 5.0
    client = FakeClient(cost=0.0, job=job)
    with pytest.raises(CostExceededError, match="do not download"):
        submit_job(client, cfg)


def test_record_job_is_one_shot(tmp_path):
    job = parse_job(JOB)
    record_job(
        tmp_path,
        job,
        batch_fingerprint(load_batch_config()["batch"]),
        now=lambda: FIXED_NOW,
    )
    assert load_jobs_index(tmp_path)["jobs"][JOB["job_id"]]["state"] == "done"
    assert active_job_id(tmp_path) == JOB["job_id"]
    with pytest.raises(ArchiveFetchError, match="already submitted"):
        record_job(tmp_path, job, "fp", now=lambda: FIXED_NOW)


def test_manifest_atomic_write_roundtrip(tmp_path):
    path = tmp_path / "nested" / "state.json"
    atomic_write_json(path, {"a": [1, 2], "b": {"c": "d"}})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": [1, 2], "b": {"c": "d"}}
    assert not list(tmp_path.rglob("*.tmp"))


# ---------------------------------------------------------------------------
# Download: resume, quarantine, checksum, headroom, state
# ---------------------------------------------------------------------------


def test_download_skips_complete_and_downloads_missing(tmp_path):
    good_data = b"good-file-content"
    new_data = b"brand-new-file-content"
    remote_files = [
        make_remote("2022-01.csv.zst", good_data),
        make_remote("2022-02.csv.zst", new_data),
    ]
    job_dir = tmp_path / JOB["job_id"]
    job_dir.mkdir()
    (job_dir / "2022-01.csv.zst").write_bytes(good_data)

    payloads = {remote_files[1]["urls"]["https"]: new_data}
    fetch = make_fetch(payloads)
    client = FakeClient(files=remote_files)
    report = download_job(
        client,
        JOB["job_id"],
        tmp_path,
        tmp_path / "manifests",
        fetch_file=fetch,
        now=lambda: FIXED_NOW,
    )
    assert report["skipped"] == 1
    assert report["downloaded"] == 1
    assert len(fetch.calls) == 1
    assert fetch.calls[0][1].name == "2022-02.csv.zst.part"
    assert (job_dir / "2022-02.csv.zst").read_bytes() == new_data
    assert not list(job_dir.glob("*.part"))
    state = load_file_state(tmp_path / "manifests", JOB["job_id"])
    assert {s["status"] for s in state.values()} == {"verified"}
    assert client.list_files_job_ids == [JOB["job_id"]]


def test_download_resumes_partial_part(tmp_path):
    data = b"0123456789"
    remote = make_remote("2022-03.csv.zst", data)
    job_dir = tmp_path / JOB["job_id"]
    job_dir.mkdir()
    part = job_dir / "2022-03.csv.zst.part"
    part.write_bytes(data[:4])
    fetch = make_fetch({remote["urls"]["https"]: data})
    client = FakeClient(files=[remote])
    download_job(
        client,
        JOB["job_id"],
        tmp_path,
        tmp_path / "manifests",
        fetch_file=fetch,
        now=lambda: FIXED_NOW,
    )
    assert fetch.calls == [(remote["urls"]["https"], part, 4)]
    assert (job_dir / "2022-03.csv.zst").read_bytes() == data


def test_download_quarantines_corrupt_final(tmp_path):
    data = b"correct-bytes"
    remote = make_remote("2022-04.csv.zst", data)
    job_dir = tmp_path / JOB["job_id"]
    job_dir.mkdir()
    (job_dir / "2022-04.csv.zst").write_bytes(b"legacy partial garbage")
    fetch = make_fetch({remote["urls"]["https"]: data})
    client = FakeClient(files=[remote])
    download_job(
        client,
        JOB["job_id"],
        tmp_path,
        tmp_path / "manifests",
        fetch_file=fetch,
        now=lambda: FIXED_NOW,
    )
    assert (job_dir / "2022-04.csv.zst").read_bytes() == data
    corrupt = list(job_dir.glob("2022-04.csv.zst.corrupt-*"))
    assert len(corrupt) == 1
    assert corrupt[0].read_bytes() == b"legacy partial garbage"


@pytest.mark.parametrize("bad_bytes", [b"wrong", b"correct-bytZ"])
def test_download_checksum_failure_fails_closed(tmp_path, bad_bytes):
    data = b"correct-bytes"
    remote = make_remote("2022-05.csv.zst", data)
    job_dir = tmp_path / JOB["job_id"]
    job_dir.mkdir()
    fetch = make_fetch({remote["urls"]["https"]: bad_bytes})
    client = FakeClient(files=[remote])
    with pytest.raises(ChecksumError):
        download_job(
            client,
            JOB["job_id"],
            tmp_path,
            tmp_path / "manifests",
            fetch_file=fetch,
            now=lambda: FIXED_NOW,
        )
    assert not (job_dir / "2022-05.csv.zst").exists()
    assert not list(job_dir.glob("*.part"))
    state = load_file_state(tmp_path / "manifests", JOB["job_id"])
    assert state["2022-05.csv.zst"]["status"] == "failed"


def test_download_headroom_guard_blocks_before_fetch(tmp_path):
    data = b"x" * 90
    remote = make_remote("2022-06.csv.zst", data)
    job_dir = tmp_path / JOB["job_id"]
    job_dir.mkdir()
    fetch = make_fetch({remote["urls"]["https"]: data})
    client = FakeClient(files=[remote])
    tight = lambda _p: SimpleNamespace(total=1000, used=900, free=100)  # noqa: E731
    with pytest.raises(HeadroomError, match="headroom"):
        download_job(
            client,
            JOB["job_id"],
            tmp_path,
            tmp_path / "manifests",
            fetch_file=fetch,
            disk_usage=tight,
            now=lambda: FIXED_NOW,
        )
    assert not fetch.calls
    assert not (job_dir / "2022-06.csv.zst").exists()


def test_check_headroom_math():
    roomy = lambda _p: SimpleNamespace(total=1000, used=0, free=100)  # noqa: E731
    check_headroom(Path("."), 80, headroom=0.2, disk_usage=roomy)
    with pytest.raises(HeadroomError):
        check_headroom(Path("."), 81, headroom=0.2, disk_usage=roomy)
    # Nothing to fetch: never consults the drive.
    check_headroom(Path("."), 0, headroom=0.2, disk_usage=roomy)
    with pytest.raises(HeadroomError, match="invalid headroom"):
        check_headroom(Path("."), 1, headroom=1.0, disk_usage=roomy)


# ---------------------------------------------------------------------------
# Verify: quarantine mismatches, report missing/extraneous
# ---------------------------------------------------------------------------


def test_verify_quarantines_mismatches_and_reports(tmp_path):
    good_data = b"good"
    bad_data = b"bad!"
    remote_good = make_remote("2022-07.csv.zst", good_data)
    remote_bad = make_remote("2022-08.csv.zst", bad_data)
    remote_missing = make_remote("2022-09.csv.zst", b"missing")
    job_dir = tmp_path / JOB["job_id"]
    job_dir.mkdir()
    (job_dir / "2022-07.csv.zst").write_bytes(good_data)
    (job_dir / "2022-08.csv.zst").write_bytes(b"tampered")
    (job_dir / "stale-legacy.bin").write_bytes(b"junk")
    client = FakeClient(files=[remote_good, remote_bad, remote_missing])
    report = verify_job(
        client,
        JOB["job_id"],
        tmp_path,
        tmp_path / "manifests",
        now=lambda: FIXED_NOW,
    )
    assert report["verified"] == ["2022-07.csv.zst"]
    assert report["missing"] == ["2022-09.csv.zst"]
    assert [i["filename"] for i in report["invalid"]] == ["2022-08.csv.zst"]
    assert report["invalid"][0]["quarantined_to"] is not None
    assert "2022-08.csv.zst" not in {p.name for p in job_dir.iterdir()}
    assert report["extraneous"] == ["stale-legacy.bin"]
    state = load_file_state(tmp_path / "manifests", JOB["job_id"])
    assert state["2022-07.csv.zst"]["status"] == "verified"
    assert state["2022-08.csv.zst"]["status"] == "failed"


def test_verify_without_quarantine_only_reports(tmp_path):
    bad_data = b"bad!"
    remote_bad = make_remote("2022-10.csv.zst", bad_data)
    job_dir = tmp_path / JOB["job_id"]
    job_dir.mkdir()
    (job_dir / "2022-10.csv.zst").write_bytes(b"tampered")
    client = FakeClient(files=[remote_bad])
    report = verify_job(
        client,
        JOB["job_id"],
        tmp_path,
        tmp_path / "manifests",
        quarantine=False,
        now=lambda: FIXED_NOW,
    )
    assert report["invalid"][0]["quarantined_to"] is None
    assert (job_dir / "2022-10.csv.zst").exists()


def test_file_matches_requires_size_and_hash(tmp_path):
    data = b"payload"
    remote = make_remote("f.csv.zst", data)
    p = tmp_path / "f.csv.zst"
    p.write_bytes(data)
    assert file_matches(remote, p)
    p.write_bytes(b"payload!")
    assert not file_matches(remote, p)
    assert not file_matches(remote, tmp_path / "missing.csv.zst")


# ---------------------------------------------------------------------------
# API key handling (never printed)
# ---------------------------------------------------------------------------


def test_load_api_key_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABENTO_API_KEY", "env-secret-key")
    assert load_api_key(tmp_path / "missing.env") == "env-secret-key"


def test_load_api_key_from_keys_env(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    env = tmp_path / "keys.env"
    env.write_text("DATABENTO_API_KEY=file-secret-key\n# comment\n", encoding="utf-8")
    assert load_api_key(env) == "file-secret-key"


def test_load_api_key_missing_fails_without_value(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    with pytest.raises(ApiKeyError, match="DATABENTO_API_KEY not found"):
        load_api_key(tmp_path / "missing.env")


# ---------------------------------------------------------------------------
# CLI end-to-end (submit -> status -> download -> verify) with fakes
# ---------------------------------------------------------------------------


def test_cli_end_to_end(tmp_path, capsys):
    data1 = b"month-2022-01"
    data2 = b"month-2022-02"
    remote_files = [
        make_remote("2022-01.csv.zst", data1),
        make_remote("2022-02.csv.zst", data2),
    ]
    payloads = {
        remote_files[0]["urls"]["https"]: data1,
        remote_files[1]["urls"]["https"]: data2,
    }
    fetch = make_fetch(payloads)
    manifests = tmp_path / "manifests"
    root = tmp_path / "source"
    env = tmp_path / "keys.env"
    env.write_text("DATABENTO_API_KEY=super-secret-test-key\n", encoding="utf-8")

    clients: list[FakeClient] = []

    def factory(_key: str) -> FakeClient:
        client = FakeClient(files=remote_files)
        clients.append(client)
        return client

    common = [
        "--keys-env",
        str(env),
        "--manifests-dir",
        str(manifests),
        "--download-root",
        str(root),
    ]
    rc = main(
        ["submit", *common],
        client_factory=factory,
        fetch_file=fetch,
        now=lambda: FIXED_NOW,
    )
    assert rc == 0
    assert "submitted job_id=GLBX-20220101-00000000-ABCDE" in capsys.readouterr().out

    rc = main(
        ["status", *common],
        client_factory=factory,
        fetch_file=fetch,
        now=lambda: FIXED_NOW,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "state=done" in out
    assert "remote_files=2" in out

    rc = main(
        ["download", *common],
        client_factory=factory,
        fetch_file=fetch,
        now=lambda: FIXED_NOW,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "downloaded=2 skipped=0" in out
    assert (root / JOB["job_id"] / "2022-01.csv.zst").read_bytes() == data1
    assert (root / JOB["job_id"] / "2022-02.csv.zst").read_bytes() == data2

    rc = main(
        ["verify", *common],
        client_factory=factory,
        fetch_file=fetch,
        now=lambda: FIXED_NOW,
    )
    assert rc == 0
    assert "verified=2 invalid=0 missing=0" in capsys.readouterr().out

    # Corrupt a file; verify must now fail (exit 1) and quarantine it.
    (root / JOB["job_id"] / "2022-01.csv.zst").write_bytes(b"tampered!")
    rc = main(
        ["verify", *common],
        client_factory=factory,
        fetch_file=fetch,
        now=lambda: FIXED_NOW,
    )
    assert rc == 1
    out = capsys.readouterr().out
    assert "invalid=1" in out
    assert "quarantined_to=" in out
    assert not (root / JOB["job_id"] / "2022-01.csv.zst").exists()


def test_cli_submit_is_one_shot(tmp_path, capsys):
    manifests = tmp_path / "manifests"
    env = tmp_path / "keys.env"
    env.write_text("DATABENTO_API_KEY=secret\n", encoding="utf-8")
    clients: list[FakeClient] = []

    def factory(_key: str) -> FakeClient:
        client = FakeClient()
        clients.append(client)
        return client

    common = ["--keys-env", str(env), "--manifests-dir", str(manifests)]
    assert main(["submit", *common], client_factory=factory, now=lambda: FIXED_NOW) == 0
    assert main(["submit", *common], client_factory=factory, now=lambda: FIXED_NOW) == 2
    assert len(clients) == 1  # second submit never built a client / reached the API
    assert clients[0].submit_kwargs is not None
    out = capsys.readouterr().out
    assert "super-secret-test-key" not in out
    assert "secret" not in out


def test_cli_download_refuses_job_not_done(tmp_path, capsys):
    job = dict(JOB)
    job["state"] = "queued"
    manifests = tmp_path / "manifests"
    env = tmp_path / "keys.env"
    env.write_text("DATABENTO_API_KEY=secret\n", encoding="utf-8")

    def factory(_key: str) -> FakeClient:
        return FakeClient(job=job, jobs=[job], files=[make_remote("2022-01.csv.zst", b"x")])

    common = [
        "--keys-env",
        str(env),
        "--manifests-dir",
        str(manifests),
        "--download-root",
        str(tmp_path / "source"),
    ]
    assert main(["submit", *common], client_factory=factory, now=lambda: FIXED_NOW) == 0
    rc = main(
        ["download", *common],
        client_factory=factory,
        fetch_file=make_fetch({}),
        now=lambda: FIXED_NOW,
    )
    assert rc == 2
    assert "requires 'done'" in capsys.readouterr().err


def test_cli_status_does_not_request_files_while_processing(tmp_path, capsys):
    job = {**JOB, "state": "processing"}
    client = FakeClient(job=job, jobs=[job])
    manifests = tmp_path / "manifests"
    env = tmp_path / "keys.env"
    env.write_text("DATABENTO_API_KEY=secret\n", encoding="utf-8")
    record_job(
        manifests,
        {**job, "cost_usd": 0.0},
        batch_fingerprint(load_batch_config()["batch"]),
    )

    rc = main(
        ["status", "--keys-env", str(env), "--manifests-dir", str(manifests)],
        client_factory=lambda _key: client,
    )
    assert rc == 0
    assert client.list_files_job_ids == []
    assert "state=processing" in capsys.readouterr().out
