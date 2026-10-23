#!/usr/bin/env python3
"""CLI for offline move-exhaustion research.

Never starts dashboards or touches live streams.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Move exhaustion research CLI (offline only)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("e00", help="Run E00 data/causality audit")
    sub.add_parser("e01", help="Run E01 event-detector comparison")
    sub.add_parser("e02", help="Run E02 price-only baselines")
    sub.add_parser("e03", help="Run E03 V2 residual baselines")
    sub.add_parser("e04", help="Run E04 flow-dominance categories")
    sub.add_parser("e05", help="Run E05 DV01/contracts per tick")
    sub.add_parser("e06", help="Run E06 impact-efficiency decay")
    sub.add_parser("e07", help="Run E07 state-dependent impact shortfall")
    sub.add_parser("e08", help="Run E08 flow persistence and termination")
    sub.add_parser("e09", help="Run E09 large-versus-small trade composition")
    sub.add_parser("e10", help="Run E10 TBBO apparent absorption at extreme")
    sub.add_parser("e11", help="Run E11 liquidity-conditioned exhaustion")
    sub.add_parser("e12", help="Run E12 microprice and book-imbalance confirmation")
    sub.add_parser("e13", help="Run E13 fair-value and residual dynamics")
    sub.add_parser("e14", help="Run E14 cross-tenor price/flow confirmation")
    sub.add_parser("e15", help="Run E15 cross-tenor flow confirmation")
    sub.add_parser("e16", help="Run E16 time-of-day/session regimes")
    sub.add_parser("e17", help="Run E17 interaction-only exhaustion rules")
    sub.add_parser("e18", help="Run E18 multivariate classification")
    sub.add_parser("e19", help="Run E19 discrete-time hazard")
    sub.add_parser("e20", help="Run E20 competing risks")
    sub.add_parser("e21", help="Run E21 extension quantile model")
    sub.add_parser("e22", help="Run E22 reversal magnitude")
    sub.add_parser("e23", help="Run E23 MBP-1 refill (or mark unavailable)")
    sub.add_parser("e24", help="Run E24 MBP-10 book-shape incremental")
    sub.add_parser("e25", help="Run E25 MBO queue (or mark unavailable)")
    sub.add_parser("e26", help="Run E26 trading-policy simulation")
    sub.add_parser("e27", help="Run E27 abstention / selective prediction")
    sub.add_parser("e28", help="Run E28 feature-family ablation")
    sub.add_parser("e29", help="Run E29 placebo / falsification tests")
    sub.add_parser("e30", help="Run E30 stability and drift")
    args = parser.parse_args(argv)

    if args.cmd == "e00":
        from scripts.move_exhaustion.reporting.e00 import run_e00

        manifest = run_e00()
        print(json.dumps({"experiment": "E00", "status": manifest["status"], "rows": manifest["join_meta"]["rows_merged"], "dates": manifest["n_dates"]}, indent=2))
        return 0 if manifest["status"] == "passed" else 1

    if args.cmd == "e01":
        from scripts.move_exhaustion.reporting.e01 import run_e01

        manifest = run_e01()
        print(
            json.dumps(
                {
                    "experiment": "E01",
                    "status": manifest["status"],
                    "primary_detector_candidate": manifest["primary_detector_candidate"],
                    "summaries": {
                        k: {
                            "n_events": v.get("n_events"),
                            "n_resolved": v.get("n_resolved"),
                            "exhaustion_rate": v.get("exhaustion_rate"),
                            "unresolved_rate": v.get("unresolved_rate"),
                            "label_audit_ok": v.get("label_audit_ok"),
                        }
                        for k, v in manifest["detector_summaries"].items()
                    },
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e02":
        from scripts.move_exhaustion.reporting.e02 import run_e02

        manifest = run_e02()
        print(
            json.dumps(
                {
                    "experiment": "E02",
                    "status": manifest["status"],
                    "event_universe": manifest["event_universe"],
                    "best_price_only_policy": manifest["best_price_only_policy"],
                    "summaries": {
                        k: {
                            "n_entered": v.get("n_entered"),
                            "hit_rate_resolved": v.get("hit_rate_resolved"),
                            "mean_net_ticks": v.get("mean_net_ticks"),
                            "median_mae_ticks": v.get("median_mae_ticks"),
                        }
                        for k, v in manifest["summaries"].items()
                    },
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e03":
        from scripts.move_exhaustion.reporting.e03 import run_e03

        manifest = run_e03()
        print(
            json.dumps(
                {
                    "experiment": "E03",
                    "status": manifest["status"],
                    "best_v2_policy": manifest["best_v2_policy"],
                    "comparison_to_e02": manifest["comparison_to_e02"],
                    "top": {
                        k: {
                            "n_entered": v.get("n_entered"),
                            "entry_rate": v.get("entry_rate"),
                            "mean_net_ticks": v.get("mean_net_ticks"),
                            "hit_rate": v.get("hit_rate"),
                        }
                        for k, v in sorted(
                            manifest["summaries"].items(),
                            key=lambda kv: kv[1].get("mean_net_ticks") or -1e9,
                            reverse=True,
                        )[:5]
                    },
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e04":
        from scripts.move_exhaustion.reporting.e04 import run_e04

        manifest = run_e04()
        print(
            json.dumps(
                {
                    "experiment": "E04",
                    "status": manifest["status"],
                    "useful_spread": manifest["useful_spread"],
                    "by_category_eval": manifest["by_category_eval"],
                    "thresholds": manifest["thresholds"],
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e05":
        from scripts.move_exhaustion.reporting.e05 import run_e05

        manifest = run_e05()
        print(
            json.dumps(
                {
                    "experiment": "E05",
                    "status": manifest["status"],
                    "best_feature": manifest["best_feature"],
                    "success_features": manifest["success_features"],
                    "lead_features": manifest["lead_features"],
                    "summary": {
                        k: {
                            "spearman": v["monotonicity"].get("spearman"),
                            "auc": v["logistic"].get("auc"),
                            "coef": v["logistic"].get("coef_standardized"),
                            "sign_stable": v.get("sign_stable_across_folds"),
                        }
                        for k, v in manifest["feature_results"].items()
                    },
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e06":
        from scripts.move_exhaustion.reporting.e06 import run_e06

        manifest = run_e06()
        print(
            json.dumps(
                {
                    "experiment": "E06",
                    "status": manifest["status"],
                    "hypothesis_supported": manifest["hypothesis_supported"],
                    "primary_sample": manifest.get("primary_sample"),
                    "best_feature": manifest["best_feature"],
                    "success_features": manifest["success_features"],
                    "lead_features": manifest["lead_features"],
                    "regime_spread_eval": manifest["regime_spread_eval"],
                    "regime_direction_ok": manifest.get("regime_direction_ok"),
                    "ablation_incremental_auc_vs_price_flow": manifest[
                        "ablation_incremental_auc_vs_price_flow"
                    ],
                    "by_regime_eval": manifest["by_regime_eval"],
                    "n_events_landmark": manifest["n_events_landmark"],
                    "n_events_extending": manifest.get("n_events_extending"),
                    "n_already_retracing": manifest.get("n_already_retracing"),
                    "summary": {
                        k: {
                            "spearman": v["monotonicity"].get("spearman"),
                            "auc": v["logistic"].get("auc"),
                            "sign_stable": v.get("sign_stable_across_folds"),
                        }
                        for k, v in (manifest.get("feature_results") or {}).items()
                    },
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e07":
        from scripts.move_exhaustion.reporting.e07 import run_e07

        manifest = run_e07()
        print(
            json.dumps(
                {
                    "experiment": "E07",
                    "status": manifest["status"],
                    "hypothesis_supported": manifest["hypothesis_supported"],
                    "provisional_lead": manifest.get("provisional_lead"),
                    "selected_impact_model": manifest["selected_impact_model"],
                    "impact_models": manifest["impact_models"],
                    "best_feature": manifest["best_feature"],
                    "success_features": manifest["success_features"],
                    "beats_residual_z": manifest["beats_residual_z"],
                    "beats_actual_progress": manifest.get("beats_actual_progress"),
                    "beats_fixed_scaling_on_eval": manifest["beats_fixed_scaling_on_eval"],
                    "corr_shortfall_vs_actual": manifest.get("corr_shortfall_vs_actual"),
                    "summary": {
                        k: {
                            "spearman": v["monotonicity"].get("spearman"),
                            "auc": v["logistic"].get("auc"),
                            "auc_eval": (v.get("logistic_eval_dates") or {}).get("auc"),
                            "sign_stable": v.get("sign_stable_across_folds"),
                        }
                        for k, v in manifest["feature_results"].items()
                    },
                    "model_exhaustion_eval": {
                        k: {
                            "shortfall_auc": (v.get("impact_shortfall") or {}).get("auc"),
                            "cum_auc": (v.get("cum_impact_shortfall") or {}).get("auc"),
                        }
                        for k, v in manifest["model_exhaustion_eval"].items()
                    },
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e08":
        from scripts.move_exhaustion.reporting.e08 import run_e08

        manifest = run_e08()
        print(
            json.dumps(
                {
                    "experiment": "E08",
                    "status": manifest["status"],
                    "hypothesis_supported": manifest["hypothesis_supported"],
                    "best_feature": manifest["best_feature"],
                    "success_features": manifest["success_features"],
                    "lead_features": manifest["lead_features"],
                    "useful_pattern_flags": manifest["useful_pattern_flags"],
                    "flag_results": manifest["flag_results"],
                    "ablations": {
                        k: {"auc": v.get("auc"), "n": v.get("n")}
                        for k, v in manifest["ablations"].items()
                    },
                    "summary": {
                        k: {
                            "spearman": v["monotonicity"].get("spearman"),
                            "auc": v["logistic"].get("auc"),
                            "auc_eval": (v.get("logistic_eval_dates") or {}).get("auc"),
                            "sign_stable": v.get("sign_stable_across_folds"),
                        }
                        for k, v in manifest["feature_results"].items()
                    },
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e09":
        from scripts.move_exhaustion.reporting.e09 import run_e09

        manifest = run_e09()
        print(
            json.dumps(
                {
                    "experiment": "E09",
                    "status": manifest["status"],
                    "hypothesis_supported": manifest["hypothesis_supported"],
                    "best_feature": manifest["best_feature"],
                    "success_features": manifest["success_features"],
                    "lead_features": manifest["lead_features"],
                    "contrast_ok": manifest["contrast_ok"],
                    "hypothesis_contrast_spread_eval": manifest[
                        "hypothesis_contrast_spread_eval"
                    ],
                    "category_spread_eval": manifest["category_spread_eval"],
                    "by_category_eval": manifest["by_category_eval"],
                    "summary": {
                        k: {
                            "spearman": v["monotonicity"].get("spearman"),
                            "auc": v["logistic"].get("auc"),
                            "auc_eval": (v.get("logistic_eval_dates") or {}).get("auc"),
                            "sign_stable": v.get("sign_stable_across_folds"),
                        }
                        for k, v in manifest["feature_results"].items()
                    },
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e10":
        from scripts.move_exhaustion.reporting.e10 import run_e10

        manifest = run_e10()
        print(
            json.dumps(
                {
                    "experiment": "E10",
                    "status": manifest["status"],
                    "hypothesis_supported": manifest["hypothesis_supported"],
                    "direction_ok": manifest.get("direction_ok"),
                    "opposite_stable_lead": manifest.get("opposite_stable_lead"),
                    "survives_controls": manifest["survives_controls"],
                    "best_feature": manifest["best_feature"],
                    "success_features": manifest["success_features"],
                    "lead_features": manifest["lead_features"],
                    "controls": {
                        "controls_only_auc": (manifest.get("controls_only") or {}).get("auc"),
                        "score_only_auc": (manifest.get("score_only") or {}).get("auc"),
                        "score_plus_controls_auc": (manifest.get("score_plus_controls") or {}).get(
                            "auc"
                        ),
                        "score_coef": ((manifest.get("score_plus_controls") or {}).get("coefs") or {}).get(
                            "apparent_absorption_score"
                        ),
                    },
                    "summary": {
                        k: {
                            "spearman": v["monotonicity"].get("spearman"),
                            "auc": v["logistic"].get("auc"),
                            "auc_eval": (v.get("logistic_eval_dates") or {}).get("auc"),
                            "sign_stable": v.get("sign_stable_across_folds"),
                        }
                        for k, v in manifest["feature_results"].items()
                    },
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e11":
        from scripts.move_exhaustion.reporting.e11 import run_e11

        manifest = run_e11()
        print(
            json.dumps(
                {
                    "experiment": "E11",
                    "status": manifest["status"],
                    "hypothesis_supported": manifest["hypothesis_supported"],
                    "provisional_lead": manifest.get("provisional_lead"),
                    "useful_interactions": manifest["useful_interactions"],
                    "bundle_delta_auc": manifest["bundle_delta_auc"],
                    "bundle_delta_auc_eval": manifest.get("bundle_delta_auc_eval"),
                    "n_eval_resolved": manifest.get("n_eval_resolved"),
                    "regime_rates": manifest["regime_rates"],
                    "ablations": {
                        k: {"auc": v.get("auc"), "n": v.get("n"), "ok": v.get("ok")}
                        for k, v in manifest["ablations"].items()
                    },
                    "interaction_deltas": {
                        k: {
                            "delta_auc": v.get("delta_auc"),
                            "strat_spread": v.get("stratified_auc_spread_depth"),
                        }
                        for k, v in manifest["interaction_results"].items()
                    },
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e12":
        from scripts.move_exhaustion.reporting.e12 import run_e12

        manifest = run_e12()
        print(
            json.dumps(
                {
                    "experiment": "E12",
                    "status": manifest["status"],
                    "hypothesis_supported": manifest["hypothesis_supported"],
                    "best_feature": manifest["best_feature"],
                    "success_features": manifest["success_features"],
                    "lead_features": manifest["lead_features"],
                    "useful_patterns": manifest["useful_patterns"],
                    "incremental_auc_vs_price": manifest["incremental_auc_vs_price"],
                    "pattern_results": manifest["pattern_results"],
                    "summary": {
                        k: {
                            "spearman": v["monotonicity"].get("spearman"),
                            "auc": v["logistic"].get("auc"),
                            "auc_eval": (v.get("logistic_eval_dates") or {}).get("auc"),
                            "sign_stable": v.get("sign_stable_across_folds"),
                            "corr_ext": v.get("corr_additional_extension"),
                        }
                        for k, v in manifest["feature_results"].items()
                    },
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e13":
        from scripts.move_exhaustion.reporting.e13 import run_e13

        manifest = run_e13()
        print(
            json.dumps(
                {
                    "experiment": "E13",
                    "status": manifest["status"],
                    "hypothesis_supported": manifest["hypothesis_supported"],
                    "best_feature": manifest["best_feature"],
                    "success_features": manifest["success_features"],
                    "lead_features": manifest["lead_features"],
                    "incremental_auc_vs_static_residual": manifest[
                        "incremental_auc_vs_static_residual"
                    ],
                    "attractive_better_than_chasing": manifest["attractive_better_than_chasing"],
                    "by_category_eval": manifest["by_category_eval"],
                    "summary": {
                        k: {
                            "spearman": v["monotonicity"].get("spearman"),
                            "auc": v["logistic"].get("auc"),
                            "auc_eval": (v.get("logistic_eval_dates") or {}).get("auc"),
                            "sign_stable": v.get("sign_stable_across_folds"),
                        }
                        for k, v in manifest["feature_results"].items()
                    },
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e14":
        from scripts.move_exhaustion.reporting.e14 import run_e14

        manifest = run_e14()
        print(
            json.dumps(
                {
                    "experiment": "E14",
                    "status": manifest["status"],
                    "hypothesis_supported": manifest["hypothesis_supported"],
                    "price_features_available": manifest["price_features_available"],
                    "best_feature": manifest["best_feature"],
                    "success_features": manifest["success_features"],
                    "lead_features": manifest["lead_features"],
                    "direction_ok": manifest["direction_ok"],
                    "contrast_ok": manifest["contrast_ok"],
                    "alone_vs_all_confirm_exhaustion_spread": manifest[
                        "alone_vs_all_confirm_exhaustion_spread"
                    ],
                    "incremental_auc_vs_ty_flow": manifest["incremental_auc_vs_ty_flow"],
                    "by_category_all": manifest["by_category_all"],
                    "summary": {
                        k: {
                            "spearman": v["monotonicity"].get("spearman"),
                            "auc": v["logistic"].get("auc"),
                            "auc_eval": (v.get("logistic_eval_dates") or {}).get("auc"),
                            "sign_stable": v.get("sign_stable_across_folds"),
                        }
                        for k, v in manifest["feature_results"].items()
                    },
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e15":
        from scripts.move_exhaustion.reporting.e15 import run_e15

        manifest = run_e15()
        print(
            json.dumps(
                {
                    "experiment": "E15",
                    "status": manifest["status"],
                    "hypothesis_supported": manifest["hypothesis_supported"],
                    "best_feature": manifest["best_feature"],
                    "success_features": manifest["success_features"],
                    "lead_features": manifest["lead_features"],
                    "curve_direction_ok": manifest["curve_direction_ok"],
                    "residual_direction_ok": manifest["residual_direction_ok"],
                    "contrast_ok": manifest["contrast_ok"],
                    "target_only_vs_curve_wide_exhaustion_spread": manifest[
                        "target_only_vs_curve_wide_exhaustion_spread"
                    ],
                    "incremental_auc_vs_target_flow": manifest[
                        "incremental_auc_vs_target_flow"
                    ],
                    "by_regime_all": manifest["by_regime_all"],
                    "summary": {
                        k: {
                            "spearman": v["monotonicity"].get("spearman"),
                            "auc": v["logistic"].get("auc"),
                            "auc_eval": (v.get("logistic_eval_dates") or {}).get("auc"),
                            "sign_stable": v.get("sign_stable_across_folds"),
                            "coef": v["logistic"].get("coef_standardized"),
                        }
                        for k, v in manifest["feature_results"].items()
                    },
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e16":
        from scripts.move_exhaustion.reporting.e16 import run_e16

        manifest = run_e16()
        print(
            json.dumps(
                {
                    "experiment": "E16",
                    "status": manifest["status"],
                    "hypothesis_supported": manifest["hypothesis_supported"],
                    "recommendation": manifest["recommendation"],
                    "high_vol_threshold": manifest["high_vol_threshold"],
                    "by_regime_eval": manifest["by_regime_eval"],
                    "overlap_counts": manifest["overlap_counts"],
                    "eval_aucs": {
                        "pooled": (manifest.get("pooled") or {}).get("eval_auc"),
                        "indicators": (manifest.get("pooled_with_indicators") or {}).get(
                            "eval_auc"
                        ),
                        "interactions": (manifest.get("pooled_with_interactions") or {}).get(
                            "eval_auc"
                        ),
                        "separate": ((manifest.get("separate_models") or {}).get("overall") or {}).get(
                            "eval_auc"
                        ),
                    },
                    "exclusion_deltas": {
                        k: v.get("delta_eval_auc_vs_pooled_all")
                        for k, v in (
                            (manifest.get("exclusion_tests") or {}).get("filters") or {}
                        ).items()
                    },
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e17":
        from scripts.move_exhaustion.reporting.e17 import run_e17

        manifest = run_e17()
        print(
            json.dumps(
                {
                    "experiment": "E17",
                    "status": manifest["status"],
                    "verdict": manifest["verdict"],
                    "hypothesis_supported": manifest["hypothesis_supported"],
                    "best_model": manifest.get("best_model"),
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e18":
        from scripts.move_exhaustion.reporting.e18 import run_e18

        manifest = run_e18()
        print(
            json.dumps(
                {
                    "experiment": "E18",
                    "status": manifest["status"],
                    "verdict": manifest["verdict"],
                    "hypothesis_supported": manifest["hypothesis_supported"],
                    "best_binary": manifest.get("best_binary"),
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e19":
        from scripts.move_exhaustion.reporting.e19 import run_e19

        manifest = run_e19()
        print(
            json.dumps(
                {
                    "experiment": "E19",
                    "status": manifest["status"],
                    "verdict": manifest["verdict"],
                    "hypothesis_supported": manifest["hypothesis_supported"],
                    "logistic_metrics": manifest.get("logistic_metrics"),
                    "horizon_brier": manifest.get("horizon_brier"),
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e20":
        from scripts.move_exhaustion.reporting.e20 import run_e20

        manifest = run_e20()
        print(
            json.dumps(
                {
                    "experiment": "E20",
                    "status": manifest["status"],
                    "verdict": manifest["verdict"],
                    "hypothesis_supported": manifest["hypothesis_supported"],
                    "cumulative_incidence_eval": manifest.get("cumulative_incidence_eval"),
                    "e18_comparison": manifest.get("e18_comparison"),
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e21":
        from scripts.move_exhaustion.reporting.e21 import run_e21

        manifest = run_e21()
        print(
            json.dumps(
                {
                    "experiment": "E21",
                    "status": manifest["status"],
                    "verdict": manifest["verdict"],
                    "hypothesis_supported": manifest["hypothesis_supported"],
                    "results": manifest.get("results"),
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e22":
        from scripts.move_exhaustion.reporting.e22 import run_e22

        manifest = run_e22()
        print(
            json.dumps(
                {
                    "experiment": "E22",
                    "status": manifest["status"],
                    "verdict": manifest["verdict"],
                    "hypothesis_supported": manifest["hypothesis_supported"],
                    "results": manifest.get("results"),
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e23":
        from scripts.move_exhaustion.reporting.e23 import run_e23

        manifest = run_e23()
        print(json.dumps({"experiment": "E23", "status": manifest["status"], "verdict": manifest["verdict"]}, indent=2))
        return 0

    if args.cmd == "e24":
        from scripts.move_exhaustion.reporting.e24 import run_e24

        manifest = run_e24()
        print(
            json.dumps(
                {
                    "experiment": "E24",
                    "status": manifest["status"],
                    "verdict": manifest["verdict"],
                    "hypothesis_supported": manifest.get("hypothesis_supported"),
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e25":
        from scripts.move_exhaustion.reporting.e25 import run_e25

        manifest = run_e25()
        print(json.dumps({"experiment": "E25", "status": manifest["status"], "verdict": manifest["verdict"]}, indent=2))
        return 0

    if args.cmd == "e26":
        from scripts.move_exhaustion.reporting.e26 import run_e26

        manifest = run_e26()
        print(
            json.dumps(
                {
                    "experiment": "E26",
                    "status": manifest["status"],
                    "verdict": manifest["verdict"],
                    "hypothesis_supported": manifest.get("hypothesis_supported"),
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e27":
        from scripts.move_exhaustion.reporting.e27 import run_e27

        manifest = run_e27()
        print(
            json.dumps(
                {
                    "experiment": "E27",
                    "status": manifest["status"],
                    "verdict": manifest["verdict"],
                    "hypothesis_supported": manifest.get("hypothesis_supported"),
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e28":
        from scripts.move_exhaustion.reporting.e28 import run_e28

        manifest = run_e28()
        print(json.dumps({"experiment": "E28", "status": manifest["status"], "verdict": manifest["verdict"]}, indent=2))
        return 0

    if args.cmd == "e29":
        from scripts.move_exhaustion.reporting.e29 import run_e29

        manifest = run_e29()
        print(
            json.dumps(
                {
                    "experiment": "E29",
                    "status": manifest["status"],
                    "verdict": manifest["verdict"],
                    "n_pass": (manifest.get("metrics") or {}).get("n_pass"),
                    "n_tests": (manifest.get("metrics") or {}).get("n_tests"),
                },
                indent=2,
            )
        )
        return 0

    if args.cmd == "e30":
        from scripts.move_exhaustion.reporting.e30 import run_e30

        manifest = run_e30()
        print(json.dumps({"experiment": "E30", "status": manifest["status"], "verdict": manifest["verdict"]}, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
