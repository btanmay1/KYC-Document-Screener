"""Screen a folder of KYC packets and write the reviewer queue, findings log, accuracy and time-model reports.

    python -m kycscreen.screen                         # generate 500 synthetic packets, screen them, score accuracy
    python -m kycscreen.screen --packets path/to/dir   # screen your own packets (no ground truth needed)
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import pandas as pd

from .checks import Screener, route
from .extract import read_document
from .generate import generate

ROOT = Path(__file__).resolve().parent.parent


def screen_folder(packets: Path, rules: dict, watchlist: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    s = Screener(rules, watchlist)
    queue, findings = [], []
    t0 = time.perf_counter()
    for d in sorted(p for p in packets.iterdir() if p.is_dir()):
        form = json.loads((d / "application.json").read_text())
        docs = [read_document(p) for p in sorted(d.glob("*.pdf"))]
        fs = s.run(form, docs)
        decision = route(fs, rules)
        queue.append({"application_id": form["application_id"], "applicant": form["full_name"], "product": form["product"],
                      "submitted_date": form["submitted_date"], "documents": len(docs),
                      "document_types": ", ".join(sorted(x.doc_type for x in docs)), "decision": decision,
                      "high_findings": sum(f.severity == "High" for f in fs), "medium_findings": sum(f.severity == "Medium" for f in fs),
                      "reason_codes": ";".join(sorted({f.code for f in fs})),
                      "summary": " | ".join(f"{f.code}: {f.evidence}" for f in fs)[:500]})
        findings += [{"application_id": form["application_id"], "code": f.code, "severity": f.severity, "message": f.message,
                      "evidence": f.evidence, "document": f.document} for f in fs]
    elapsed = time.perf_counter() - t0
    q = pd.DataFrame(queue)
    order = {"ESCALATE": 0, "REVIEW": 1, "CLEAR": 2}
    q = q.sort_values(["decision", "high_findings", "medium_findings"], key=lambda c: c.map(order) if c.name == "decision" else -c).reset_index(drop=True)
    return q, pd.DataFrame(findings, columns=["application_id", "code", "severity", "message", "evidence", "document"]), elapsed


def accuracy(queue: pd.DataFrame, truth: pd.DataFrame, rules: dict) -> tuple[pd.DataFrame, dict]:
    t = truth.set_index("application_id").planted_defects.fillna("").map(lambda s: set(filter(None, s.split(";"))))
    p = queue.set_index("application_id").reason_codes.fillna("").map(lambda s: set(filter(None, s.split(";"))))
    rows = []
    for code in rules["severity"]:
        tp = sum(code in t[a] and code in p[a] for a in t.index)
        fp = sum(code not in t[a] and code in p[a] for a in t.index)
        fn = sum(code in t[a] and code not in p[a] for a in t.index)
        rows.append({"code": code, "severity": rules["severity"][code], "planted": tp + fn, "flagged": tp + fp, "true_positive": tp,
                     "false_positive": fp, "false_negative": fn,
                     "precision": round(tp / (tp + fp), 4) if tp + fp else None, "recall": round(tp / (tp + fn), 4) if tp + fn else None})
    per_code = pd.DataFrame(rows)

    def expected(codes: set) -> str:
        sev = {rules["severity"][c] for c in codes}
        return "ESCALATE" if "High" in sev else "REVIEW" if "Medium" in sev else "CLEAR"
    exp = t.map(expected)
    got = queue.set_index("application_id").decision.reindex(t.index)
    defective = t.map(bool)
    packet = {
        "packets": int(len(t)),
        "packets_with_planted_defects": int(defective.sum()),
        "routing_accuracy": round(float((exp == got).mean()), 4),
        "defective_packets_missed_(routed_CLEAR)": int((defective & (got == "CLEAR")).sum()),
        "clean_packets_routed_for_review": int((~defective & (got != "CLEAR")).sum()),
        "exact_reason_code_match": round(float((t == p.reindex(t.index)).mean()), 4),
        "confusion": pd.crosstab(exp.rename("expected"), got.rename("routed")).to_dict(),
    }
    return per_code, packet


def time_model(queue: pd.DataFrame, rules: dict) -> tuple[pd.DataFrame, dict]:
    tm = rules["time_model_minutes"]
    counts = queue.decision.value_counts()
    rows = [{"decision": d, "packets": int(counts.get(d, 0)), "minutes_per_packet_with_tool": m,
             "minutes_with_tool": round(counts.get(d, 0) * m, 1)} for d, m in tm["with_tool"].items()]
    df = pd.DataFrame(rows)
    manual = len(queue) * tm["manual_per_packet"]
    tool = float(df.minutes_with_tool.sum())
    return df, {"manual_minutes": manual, "tool_minutes": round(tool, 1), "reduction": round(1 - tool / manual, 4)}


def write_excel(path: Path, queue, findings, per_code, packet, tm_df, tm, perf) -> None:
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    fills = {"ESCALATE": "FFC7CE", "REVIEW": "FFEB9C", "CLEAR": "C6EFCE", "High": "FFC7CE", "Medium": "FFEB9C"}
    summary = pd.DataFrame([
        ("Packets screened", packet.get("packets", len(queue))),
        ("Routed ESCALATE", int((queue.decision == "ESCALATE").sum())),
        ("Routed REVIEW", int((queue.decision == "REVIEW").sum())),
        ("Routed CLEAR", int((queue.decision == "CLEAR").sum())),
        ("Findings raised", len(findings)),
        ("Routing accuracy vs ground truth", packet.get("routing_accuracy", "n/a")),
        ("Defective packets missed", packet.get("defective_packets_missed_(routed_CLEAR)", "n/a")),
        ("Processing time (seconds, all packets)", round(perf["seconds"], 1)),
        ("Processing time per packet (ms)", round(perf["seconds"] / max(len(queue), 1) * 1000, 1)),
        ("Estimated manual review minutes", tm["manual_minutes"]),
        ("Estimated review minutes with tool", tm["tool_minutes"]),
        ("Estimated review time reduction (time model)", f"{tm['reduction']:.0%}"),
    ], columns=["Measure", "Value"])
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in [("Summary", summary), ("Reviewer Queue", queue), ("Findings", findings),
                         ("Accuracy by Check", per_code), ("Time Model", tm_df)]:
            df.to_excel(xw, sheet_name=name, index=False)
            ws = xw.sheets[name]
            for c in ws[1]:
                c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3864")
            for j, col in enumerate(df.columns, 1):
                w = max([len(str(col))] + [len(str(v)) for v in df[col].head(300)]) + 2
                ws.column_dimensions[get_column_letter(j)].width = min(max(w, 10), 70)
            ws.freeze_panes = "A2"
            for col_name in ("decision", "severity"):
                if col_name in df.columns:
                    j = list(df.columns).index(col_name) + 1
                    for i in range(2, len(df) + 2):
                        cell = ws.cell(row=i, column=j)
                        if cell.value in fills:
                            cell.fill = PatternFill("solid", fgColor=fills[cell.value])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--packets", type=Path, help="folder of packet sub-folders; omit to generate synthetic packets")
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=ROOT / "reports")
    a = ap.parse_args()

    rules = json.loads((ROOT / "config" / "rules.json").read_text())
    with open(ROOT / "config" / "watchlist.csv", newline="", encoding="utf-8") as f:
        watchlist = list(csv.DictReader(f))
    truth = None
    if a.packets is None:
        data = generate(ROOT / "data", a.n, a.seed)
        a.packets = data / "packets"
        truth = pd.read_csv(data / "ground_truth.csv", keep_default_na=False)
        print(f"[generate] {a.n} synthetic packets, {sum(1 for _ in a.packets.rglob('*.pdf')):,} PDFs")

    queue, findings, secs = screen_folder(a.packets, rules, watchlist)
    a.out.mkdir(parents=True, exist_ok=True)
    per_code, packet = accuracy(queue, truth, rules) if truth is not None else (pd.DataFrame(), {})
    tm_df, tm = time_model(queue, rules)

    queue.to_csv(a.out / "reviewer_queue.csv", index=False)
    findings.to_csv(a.out / "findings.csv", index=False)
    if truth is not None:
        per_code.to_csv(a.out / "accuracy_by_check.csv", index=False)
    write_excel(a.out / "screening_report.xlsx", queue, findings, per_code, packet, tm_df, tm, {"seconds": secs})
    (a.out / "summary.json").write_text(json.dumps({"routing": queue.decision.value_counts().to_dict(), "packet_accuracy": packet,
                                                    "time_model": tm, "processing_seconds": round(secs, 2),
                                                    "ms_per_packet": round(secs / len(queue) * 1000, 1)}, indent=2, default=str))

    print(f"[screen] {len(queue)} packets in {secs:.1f}s ({secs / len(queue) * 1000:.0f} ms/packet) | "
          + " | ".join(f"{k}: {v}" for k, v in queue.decision.value_counts().items()))
    if truth is not None:
        print(per_code.to_string(index=False))
        print({k: v for k, v in packet.items() if k != "confusion"})
        print(pd.DataFrame(packet["confusion"]).fillna(0).astype(int))
    print(f"[time model] manual {tm['manual_minutes']:.0f} min -> with tool {tm['tool_minutes']:.0f} min ({tm['reduction']:.0%} less)")


if __name__ == "__main__":
    main()
