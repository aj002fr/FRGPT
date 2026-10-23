#!/usr/bin/env python3
"""Manual-only Autoresearch Hypothesis Lab on port 5057."""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from flask import Flask, jsonify, render_template, request

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

LAB_ROOT = PROJECT_ROOT / "workspace" / "analysis" / "autoresearch_lab"
CAMPAIGN_ROOT = (
    PROJECT_ROOT / "workspace" / "analysis" / "aggression_autoresearch_campaign"
)
LAB_PORT = 5057
ASSET_VERSION = "20260724_v1"

from scripts.autoresearch_lab.lab_evidence import evidence_payload  # noqa: E402
from scripts.autoresearch_lab.lab_holdout import holdout_availability  # noqa: E402
from scripts.autoresearch_lab.lab_journal import journal_rows, search_counts  # noqa: E402
from scripts.autoresearch_lab.lab_jobs import list_jobs, public_job, submit_job  # noqa: E402
from scripts.autoresearch_lab.lab_matrix import warm_discovery_cache  # noqa: E402
from scripts.autoresearch_lab.lab_prereg import freeze_preregistration  # noqa: E402
from scripts.autoresearch_lab.lab_sandbox import sandbox_metadata  # noqa: E402

app = Flask(__name__, template_folder="templates", static_folder="static")


@app.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response


@app.get("/")
def index() -> str:
    return render_template("index.html", asset_version=ASSET_VERSION)


@app.get("/api/config")
def api_config():
    return jsonify(
        {
            "success": True,
            "port": LAB_PORT,
            "label": "EXPLORATORY / DISCOVERY-ONLY / NOT EVIDENCE",
            "manual_only": True,
            "campaign_root": str(CAMPAIGN_ROOT),
            "output_root": str(LAB_ROOT),
        }
    )


@app.get("/api/evidence")
def api_evidence():
    try:
        return jsonify(evidence_payload())
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.get("/api/sandbox/meta")
def api_sandbox_meta():
    try:
        return jsonify(sandbox_metadata())
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/sandbox/jobs", methods=["GET", "POST"])
def api_sandbox_jobs():
    try:
        if request.method == "POST":
            job = submit_job(request.get_json(force=True) or {})
            return jsonify({"success": True, "job": job}), 202
        return jsonify({"success": True, "jobs": list_jobs()})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.get("/api/sandbox/jobs/<job_id>")
def api_sandbox_job(job_id: str):
    try:
        return jsonify({"success": True, "job": public_job(job_id)})
    except KeyError:
        return jsonify({"success": False, "error": "job not found"}), 404


@app.get("/api/sandbox/journal")
def api_sandbox_journal():
    rows = journal_rows()
    return jsonify(
        {
            "success": True,
            "counts": search_counts(),
            "recent": rows[-25:][::-1],
        }
    )


@app.post("/api/preregistrations")
def api_preregistrations():
    try:
        frozen = freeze_preregistration(request.get_json(force=True) or {})
        return jsonify({"success": True, "preregistration": frozen}), 201
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.get("/api/holdout-availability")
def api_holdout_availability():
    try:
        return jsonify(holdout_availability())
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


def main() -> None:
    parser = argparse.ArgumentParser(description="Autoresearch Hypothesis Lab")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=LAB_PORT)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    LAB_ROOT.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=warm_discovery_cache, daemon=True).start()
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
