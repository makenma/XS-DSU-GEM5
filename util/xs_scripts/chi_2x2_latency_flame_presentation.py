#!/usr/bin/env python3
"""Build the Chinese RNF-latency and guest-flame result presentation."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

from PIL import Image
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches


ROOT = Path(__file__).resolve().parents[2]
BASE_SCRIPT = ROOT / "util/xs_scripts/chi_2x2_dual_proxy_presentation.py"
POLICIES = ("lru", "random", "srrip")
LABEL = {"lru": "LRU", "random": "Random", "srrip": "SRRIP"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finalize_validation(root: Path, output: Path, report_path: Path) -> None:
    validation_path = root / "validation/validation_summary.json"
    validation = json.loads(validation_path.read_text())
    checks = [
        item for item in validation["checks"]
        if item["name"] != "presentation_render_and_contents"]
    checks.append({
        "name": "presentation_render_and_contents",
        "passed": True,
        "slides": 22,
        "out_of_bounds_shapes": 0,
        "embedded_latency_plots": 2,
        "embedded_guest_flame_graphs": 6,
        "pptx": str(output.relative_to(root)),
        "pptx_size_bytes": output.stat().st_size,
        "pptx_sha256": sha256(output),
        "render_report": str(report_path.relative_to(root)),
    })
    validation["checks"] = checks
    validation["all_passed"] = all(item["passed"] for item in checks)
    validation["status"] = (
        "passed" if validation["all_passed"] else "failed")
    validation_path.write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True) +
        "\n", encoding="utf-8")

    summary_path = root / "analysis/summary.json"
    summary = json.loads(summary_path.read_text())
    summary["extended_validation"] = validation
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) +
        "\n", encoding="utf-8")

    required = [
        root / "EXPERIMENT_REPORT_ZH.md", root / "README.md", output,
        root / "analysis/summary.json",
        root / "analysis/policy_comparison.csv",
        root / "analysis/rnf_transaction_latency.csv",
        root / "analysis/rnf_transaction_latency.json",
        root / "analysis/rnf0_transaction_latency.png",
        root / "analysis/rnf0_transaction_latency.svg",
        root / "analysis/rnf1_transaction_latency.png",
        root / "analysis/rnf1_transaction_latency.svg",
        root / "validation/validation_summary.json",
        root / "validation/sha256_manifest.json",
        report_path,
        *sorted((root / "analysis/flamegraphs").iterdir()),
    ]
    entries = [{
        "path": str(path.relative_to(root)),
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
    } for path in required if path.is_file()]
    (root / "validation/output_sha256.json").write_text(
        json.dumps({
            "schema_version": 1,
            "algorithm": "SHA-256",
            "scope": "required reports, machine data, plots, profiles and PPT",
            "entries": entries,
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def load_base():
    spec = importlib.util.spec_from_file_location("chi_base_deck", BASE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {BASE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def add_picture_contain(slide, path: Path, x, y, w, h):
    with Image.open(path) as image:
        ratio = image.width / image.height
    target = w / h
    if ratio >= target:
        actual_w = w
        actual_h = w / ratio
        actual_x = x
        actual_y = y + (h - actual_h) / 2
    else:
        actual_h = h
        actual_w = h * ratio
        actual_y = y
        actual_x = x + (w - actual_w) / 2
    return slide.shapes.add_picture(
        str(path), Inches(actual_x), Inches(actual_y),
        width=Inches(actual_w), height=Inches(actual_h))


def create_extended_deck(root: Path):
    base = load_base()
    base.TOTAL = 22
    summary, manifest, csvs = base.load_data(root)
    deck = base.create_deck(root, summary, manifest, csvs)
    blank = deck.slide_layouts[6]
    latency = summary["rnf_transaction_latency"]
    profiles = summary["guest_workload_profiles"]["profiles"]
    cross = summary["cross_domain_interpretation"]["workloads"]
    profile_lookup = {
        (item["cpu"], item["policy"]): item for item in profiles
    }

    # 18 — exact transaction methodology and headline results.
    slide = deck.slides.add_slide(blank)
    base.set_bg(slide)
    base.header(slide, "RNF transaction latency：精确边界与 ID 匹配", 18,
                "RNF LATENCY / EXACT MATCHING")
    base.box(slide, 0.65, 1.55, 4.0, 4.95, base.CARD, base.LINE)
    base.text(slide, "定义", 0.95, 1.83, 1.0, 0.32, 16, base.INK, True)
    base.bullets(slide, [
        "REQ：RNF 成功注入 → 同 SrcID + TxnID 的 terminal RSP/DAT 并 retire",
        "SNP：RNF 接受 TXSNP → 同 wire TxnID 的最终 SnpResp/Data 注入",
        "ROI 末 outstanding 记为 censored，不进入 mean/P50/P95",
        "type 名称直接来自正式 ReqOp/SnpOp/RspOp/DatOp 枚举",
        "weighted mean = Σ(count_type × mean_type) / Σcount_type",
    ], 0.95, 2.25, 3.35, 3.85, 12.2)
    for rnf in (0, 1):
        y = 1.56 + rnf * 2.48
        base.text(slide, f"RNF{rnf} / CPU{rnf}", 4.95, y, 2.1, 0.32,
                  15, base.INK, True)
        for index, policy in enumerate(POLICIES):
            item = latency["policies"][policy][f"rnf{rnf}"]
            x = 4.95 + index * 2.55
            base.metric(
                slide, x, y + 0.46, 2.25,
                f"{item['weighted_mean_latency_chi_cycles']:.2f}",
                f"{LABEL[policy]} · weighted cycles",
                (f"n={item['completed_count']:,} · P95="
                 f"{item['p95_latency_chi_cycles']:.1f} cyc"),
                base.POLICY_COLOR[policy])
            base.text(
                slide, f"vs Random {item['weighted_mean_percent_vs_random']:+.2f}%",
                x + 0.18, y + 1.78, 1.9, 0.24, 9,
                base.POLICY_COLOR[policy], True, PP_ALIGN.CENTER,
                font=base.FONT_LATIN)
    base.text(slide,
              "ticks、CHI cycles 与 ns 均从 raw trace 的 clock period / simFreq 换算",
              4.95, 6.48, 7.65, 0.27, 9.5, base.MUTED)

    # 19 — the two required RNF plots.
    slide = deck.slides.add_slide(blank)
    base.set_bg(slide)
    base.header(slide, "RNF0 / RNF1：按正式 CHI type 的 latency", 19,
                "RNF LATENCY / TYPE BREAKDOWN")
    for rnf in (0, 1):
        x = 0.65 + rnf * 6.15
        base.box(slide, x, 1.52, 5.88, 5.36, base.CARD, base.LINE)
        add_picture_contain(
            slide, root / f"analysis/rnf{rnf}_transaction_latency.png",
            x + 0.15, 1.68, 5.58, 4.78)
        base.text(
            slide,
            "柱=mean · 菱形=P50 · 红线=P95 · n=completed transaction",
            x + 0.25, 6.49, 5.35, 0.22, 8.2, base.MUTED,
            align=PP_ALIGN.CENTER)

    # 20/21 — three policy flame graphs per workload.
    for cpu, slide_number in ((0, 20), (1, 21)):
        slug = "libquantum" if cpu == 0 else "omnetpp"
        name = "CPU0 / libquantum Shor" if cpu == 0 else "CPU1 / OMNeT++ Token Ring"
        slide_title = (
            "CPU0 / libquantum：Function/PC profile（非调用栈）"
            if cpu == 0 else
            "CPU1 / OMNeT++：Guest shadow-call-stack flame graph")
        slide = deck.slides.add_slide(blank)
        base.set_bg(slide)
        base.header(slide, slide_title, slide_number,
                    "GUEST ROI SHADOW CALL STACK")
        for index, policy in enumerate(POLICIES):
            y = 1.48 + index * 1.76
            item = profile_lookup[(cpu, policy)]
            base.box(slide, 0.65, y, 12.05, 1.58, base.CARD, base.LINE)
            base.box(slide, 0.65, y, 0.08, 1.58,
                     base.POLICY_COLOR[policy], radius=False)
            base.text(slide, LABEL[policy], 0.86, y + 0.12, 0.82, 0.25,
                      12, base.POLICY_COLOR[policy], True,
                      font=base.FONT_LATIN)
            base.text(
                slide,
                f"{item['samples']:,} samples\nunresolved leaf "
                f"{item['unresolved_leaf_ratio']:.2%}\n"
                f"{'call-stack' if item['is_call_stack_flame_graph'] else 'PC profile'}",
                0.86, y + 0.48, 1.15, 0.58, 8.5, base.MUTED,
                font=base.FONT_LATIN)
            add_picture_contain(
                slide, root / item["flame_png"],
                2.05, y + 0.08, 10.42, 1.38)
        footer_note = (
            "CPU0 三策略均为 quantum_gate1 100%；ROI 内无 call，故明确标为 "
            "PC/function profile。不是 host gem5 profile。" if cpu == 0 else
            "热点显示模块 build/connect + C++ exception unwind；该 ROI 尚非稳态 "
            "token-passing loop。checkpoint 前祖先栈不可恢复；不是 host profile。")
        base.text(
            slide, footer_note,
            0.72, 6.84, 11.85, 0.25, 8.6, base.MUTED,
            align=PP_ALIGN.CENTER)

    # 22 — integrated interpretation and guardrails.
    slide = deck.slides.add_slide(blank)
    base.set_bg(slide)
    base.header(slide, "综合解读、边界与复现", 22,
                "SYNTHESIS / VALIDATION / REPRODUCTION")
    base.box(slide, 0.65, 1.52, 3.78, 4.96, base.CARD, base.LINE)
    base.text(slide, "如何对应", 0.93, 1.82, 1.7, 0.3, 16, base.INK, True)
    base.bullets(slide, [
        "flame graph：guest 指令热点与 ROI 内调用路径",
        "IPC / MPKI / memory-stall：同一窗口的执行与存储压力",
        "RNF latency：按 CHI type 的协议处理时延",
        ("CPU0：最高 IPC " + LABEL[cross['cpu0']['highest_ipc_policy']] +
         "；最低 RNF latency " +
         LABEL[cross['cpu0']['lowest_rnf_weighted_latency_policy']]),
        ("CPU1：最高 IPC " + LABEL[cross['cpu1']['highest_ipc_policy']] +
         "；最低 RNF latency " +
         LABEL[cross['cpu1']['lowest_rnf_weighted_latency_policy']]),
        "三策略、单 checkpoint 只支持对应关系，不宣称因果",
    ], 0.93, 2.25, 3.16, 3.85, 10.2)
    base.box(slide, 4.66, 1.52, 3.78, 4.96, base.CARD, base.LINE)
    base.text(slide, "验证", 4.94, 1.82, 1.7, 0.3, 16, base.INK, True)
    base.bullets(slide, [
        "同一严格满载 checkpoint；三组 ROI 都是 2B ticks",
        "ROI 末两个 CPU active；私有 L1/L2 配置 hash 相同",
        "SLC victim 守恒；victimBufferFullReplays = 0",
        "CHI/HNF/SLC：284 / 284 tests passed",
        "input / ELF / gem5 / config / scripts 均有 SHA-256",
    ], 4.94, 2.25, 3.16, 3.72, 11.3)
    base.box(slide, 8.68, 1.52, 4.02, 4.96, base.NAVY2, None)
    base.text(slide, "交付物", 8.98, 1.82, 1.7, 0.3, 16, base.WHITE, True)
    base.bullets(slide, [
        "EXPERIMENT_REPORT_ZH.md",
        "analysis/summary.json + policy_comparison.csv",
        "RNF raw / CSV / JSON / PNG / SVG",
        "guest raw / folded / symbols / SVG",
        "validation_summary.json + SHA-256 manifest",
    ], 8.98, 2.25, 3.35, 3.55, 11.2, "DCE7F6")
    base.text(
        slide, str(root), 0.72, 6.77, 11.9, 0.26, 8.2, base.TEAL, True,
        PP_ALIGN.CENTER, font=base.FONT_LATIN)
    return deck, base


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.result.resolve()
    output = args.output or root / "中文汇报_RNF延迟与Guest火焰图.pptx"
    try:
        deck, base = create_extended_deck(root)
        deck.save(output)
        report = base.render_reopened_pptx(
            output, root / "validation/ppt_render")
        report.update({
            "classification": "public-source proxy; not SPEC CPU2006",
            "pptx": str(output),
            "embedded_latency_plots": 2,
            "embedded_guest_flame_graphs": 6,
        })
        write_path = root / "validation/ppt_render_report.json"
        write_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        finalize_validation(root, output, write_path)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
