"""Generate synthetic KYC application packets: an application form (JSON) plus real PDF documents.

Each packet is a folder containing application.json and doc_1.pdf ... doc_n.pdf. File names are shuffled,
so the screener must work out each document's type from its content. Defects are planted at known rates
and written to ground_truth.csv so that screening accuracy can be measured. All people, numbers and
watchlist names are fictional.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import random
import shutil
from datetime import date, timedelta
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

FIRST = ["Rahul", "Priya", "Amit", "Sneha", "Vikram", "Anjali", "Rohan", "Kavya", "Arjun", "Neha", "Siddharth", "Pooja",
         "Karan", "Meera", "Aditya", "Ishita", "Manish", "Divya", "Varun", "Shreya", "Nikhil", "Tanvi", "Harsh", "Riya"]
MIDDLE = ["Kumar", "Singh", "Prasad", "Lal", "Devi", "Rani", "Mohan", "Chandra", ""]
LAST = ["Sharma", "Verma", "Gupta", "Agarwal", "Iyer", "Reddy", "Nair", "Patel", "Mehta", "Joshi", "Bansal", "Malhotra",
        "Chopra", "Kapoor", "Saxena", "Mishra", "Pandey", "Rao", "Desai", "Kulkarni"]
CITIES = [("Delhi", "Delhi", "110"), ("Mumbai", "Maharashtra", "400"), ("Bengaluru", "Karnataka", "560"),
          ("Chennai", "Tamil Nadu", "600"), ("Pune", "Maharashtra", "411"), ("Jaipur", "Rajasthan", "302"),
          ("Lucknow", "Uttar Pradesh", "226"), ("Patiala", "Punjab", "147"), ("Meerut", "Uttar Pradesh", "250")]
STREETS = ["MG Road", "Nehru Nagar", "Civil Lines", "Model Town", "Sector 15", "Park Street", "Gandhi Marg", "Lake View Colony"]
EMPLOYERS = ["Orbit Tech Solutions Pvt Ltd", "Bluewave Analytics LLP", "Northstar Logistics Ltd", "Greenfield Pharma Ltd",
             "Apex Retail India Pvt Ltd", "Crescent Finserv Ltd"]
EDITORS = ["QuickPDF Editor 3.2", "PDFfiller Online", "Sejda PDF Editor"]

# Planted defects: code -> probability per packet
DEFECTS = {
    "MISSING_ID_PROOF": 0.03, "MISSING_ADDRESS_PROOF": 0.03, "MISSING_INCOME_PROOF": 0.04,
    "PAN_INVALID_FORMAT": 0.03, "PAN_MISMATCH": 0.03, "NAME_MISMATCH": 0.04, "DOB_MISMATCH": 0.03,
    "ADDRESS_PIN_MISMATCH": 0.04, "STALE_ADDRESS_PROOF": 0.05, "INCOME_MISMATCH": 0.05,
    "UNMASKED_AADHAAR": 0.03, "WATCHLIST_HIT": 0.02, "METADATA_TAMPER": 0.03, "UNREADABLE_DOC": 0.02,
}


def load_watchlist(path: Path) -> list[str]:
    with open(path, newline="", encoding="utf-8") as f:
        return [r["name"] for r in csv.DictReader(f)]


def make_pan(rng: random.Random, last: str) -> str:
    L = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return "".join(rng.choice(L) for _ in range(3)) + "P" + last[0].upper() + f"{rng.randint(1, 9999):04d}" + rng.choice(L)


def name_variant(rng: random.Random, first: str, middle: str, last: str) -> str:
    """Benign variations seen on real documents that must NOT be flagged as mismatches."""
    style = rng.choice(["full", "upper", "initial_middle", "no_middle", "initial_first"])
    if style == "upper":
        return " ".join(p for p in (first, middle, last) if p).upper()
    if style == "initial_middle" and middle:
        return f"{first} {middle[0]}. {last}"
    if style == "no_middle":
        return f"{first} {last}"
    if style == "initial_first":
        return f"{first[0]}. {middle + ' ' if middle else ''}{last}"
    return " ".join(p for p in (first, middle, last) if p)


def pdf(lines: list[str], title: str, created: date, producer: str = "ReportLab PDF Library", modified: date | None = None,
        blank: bool = False) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    if blank:  # simulate a scanned image with no text layer
        c.setFillGray(0.85)
        c.rect(60, h - 400, w - 120, 300, fill=1, stroke=0)
    else:
        c.setFont("Helvetica-Bold", 15)
        c.drawString(60, h - 70, title)
        c.setFont("Helvetica", 11)
        for i, line in enumerate(lines):
            c.drawString(60, h - 110 - i * 20, line)
    c.save()
    reader = PdfReader(io.BytesIO(buf.getvalue()))
    wr = PdfWriter()
    for p in reader.pages:
        wr.add_page(p)
    fmt = lambda d: d.strftime("D:%Y%m%d120000+05'30'")
    wr.add_metadata({"/Producer": producer, "/CreationDate": fmt(created), "/ModDate": fmt(modified or created)})
    out = io.BytesIO()
    wr.write(out)
    return out.getvalue()


def make_packet(rng: random.Random, app_id: str, watchlist: list[str]) -> tuple[dict, list[tuple[str, bytes]], list[str]]:
    defects = [d for d, p in DEFECTS.items() if rng.random() < p]
    # keep combinations coherent
    if "MISSING_ID_PROOF" in defects:
        # name / DOB mismatches are planted on the PAN card, so they cannot exist without one
        defects = [d for d in defects if d not in ("PAN_MISMATCH", "PAN_INVALID_FORMAT", "NAME_MISMATCH", "DOB_MISMATCH")]
    if "MISSING_ADDRESS_PROOF" in defects:
        defects = [d for d in defects if d not in ("ADDRESS_PIN_MISMATCH", "STALE_ADDRESS_PROOF", "UNMASKED_AADHAAR")]
    if "MISSING_INCOME_PROOF" in defects:
        defects = [d for d in defects if d != "INCOME_MISMATCH"]

    first, middle, last = rng.choice(FIRST), rng.choice(MIDDLE), rng.choice(LAST)
    if "WATCHLIST_HIT" in defects:  # applicant is a near-spelling of a watchlist entry
        parts = rng.choice(watchlist).split()
        first, middle, last = parts[0], " ".join(parts[1:-1]), parts[-1]
        if rng.random() < 0.5:
            middle = ""                                     # middle name dropped
        else:                                               # one-letter spelling change in the surname
            k = rng.randrange(1, len(last))
            last = last[:k] + ("i" if last[k] != "i" else "e") + last[k + 1:]
    full = " ".join(p for p in (first, middle, last) if p)
    dob = date(1965, 1, 1) + timedelta(days=rng.randint(0, 365 * 38))
    city, state, pin3 = rng.choice(CITIES)
    pin = f"{pin3}{rng.randint(1, 99):03d}"
    addr = f"{rng.randint(1, 450)}, {rng.choice(STREETS)}, {city}, {state}"
    pan = make_pan(rng, last)
    submitted = date(2026, 9, 1) + timedelta(days=rng.randint(0, 60))
    net_month = rng.randrange(28_000, 260_000, 500)
    declared = int(net_month * 12 * rng.uniform(1.0, 1.25))
    if "INCOME_MISMATCH" in defects:
        declared = int(net_month * 12 * rng.uniform(1.6, 2.6))

    form = {"application_id": app_id, "product": rng.choice(["Classic Card", "Gold Card", "Platinum Card"]),
            "full_name": full, "date_of_birth": dob.isoformat(), "pan": pan, "address": addr, "pin_code": pin,
            "declared_annual_income": declared, "employer": rng.choice(EMPLOYERS), "submitted_date": submitted.isoformat()}

    docs: list[tuple[str, bytes]] = []
    tamper_target = rng.choice(["pan", "salary", "utility"]) if "METADATA_TAMPER" in defects else None
    unreadable_target = rng.choice(["pan", "salary", "aadhaar", "utility"]) if "UNREADABLE_DOC" in defects else None

    def meta(kind: str, created: date) -> dict:
        if kind != tamper_target:
            return {"created": created}
        if rng.random() < 0.5:
            return {"created": created, "producer": rng.choice(EDITORS)}
        return {"created": created, "modified": submitted - timedelta(days=rng.randint(0, 3))}

    # ID proof: PAN card
    if "MISSING_ID_PROOF" not in defects:
        doc_pan = pan
        if "PAN_MISMATCH" in defects:
            doc_pan = make_pan(rng, last)
        if "PAN_INVALID_FORMAT" in defects:
            doc_pan = doc_pan[:3] + rng.choice("0123456789") + doc_pan[4:8] + rng.choice("ABCDE") + doc_pan[9]
            form["pan"] = doc_pan
        nm = name_variant(rng, first, middle, last).upper()
        doc_dob = dob
        if "NAME_MISMATCH" in defects:
            nm = f"{rng.choice([f for f in FIRST if f != first])} {rng.choice([l for l in LAST if l != last])}".upper()
        if "DOB_MISMATCH" in defects:
            doc_dob = dob + timedelta(days=rng.choice([-1, 1]) * rng.randint(200, 3000))
        issued = date(2015, 1, 1) + timedelta(days=rng.randint(0, 3000))
        docs.append(("pan", pdf(["Permanent Account Number Card", f"Name: {nm}", f"Father's Name: {rng.choice(FIRST).upper()} {last.upper()}",
                                 f"Date of Birth: {doc_dob:%d/%m/%Y}", f"Permanent Account Number: {doc_pan}"],
                                "INCOME TAX DEPARTMENT - GOVT. OF INDIA", **meta("pan", issued),
                                blank=unreadable_target == "pan")))

    # Address proof: masked Aadhaar and/or utility bill
    if "MISSING_ADDRESS_PROOF" not in defects:
        which = rng.choices(["both", "aadhaar", "utility"], [0.6, 0.25, 0.15])[0]
        if "STALE_ADDRESS_PROOF" in defects or "ADDRESS_PIN_MISMATCH" in defects:
            which = "both" if which == "aadhaar" else which
        if "UNMASKED_AADHAAR" in defects and which == "utility":
            which = "both"
        if which in ("both", "aadhaar"):
            num = f"{rng.randint(2000, 9999)} {rng.randint(1000, 9999)} {rng.randint(1000, 9999)}"
            shown = num if "UNMASKED_AADHAAR" in defects else "XXXX XXXX " + num[-4:]
            docs.append(("aadhaar", pdf(["Unique Identification Authority of India", f"Name: {name_variant(rng, first, middle, last)}",
                                         f"DOB: {dob:%d/%m/%Y}", f"Aadhaar No.: {shown}", f"Address: {addr}", f"PIN Code: {pin}"],
                                        "AADHAAR", created=date(2019, 1, 1) + timedelta(days=rng.randint(0, 2000)),
                                        blank=unreadable_target == "aadhaar")))
        if which in ("both", "utility"):
            bill_date = submitted - timedelta(days=rng.randint(5, 55))
            if "STALE_ADDRESS_PROOF" in defects:
                bill_date = submitted - timedelta(days=rng.randint(120, 260))
            bill_pin = pin
            if "ADDRESS_PIN_MISMATCH" in defects:
                other = rng.choice([c for c in CITIES if c[2] != pin3])
                bill_pin = f"{other[2]}{rng.randint(1, 99):03d}"
            docs.append(("utility", pdf(["Consumer Name: " + name_variant(rng, first, middle, last), f"Service Address: {addr}",
                                         f"PIN Code: {bill_pin}", f"Bill Date: {bill_date:%d-%b-%Y}",
                                         f"Consumer No.: {rng.randint(10**9, 10**10 - 1)}", f"Amount Due: Rs. {rng.randint(400, 6000)}.00"],
                                        "ELECTRICITY BILL - STATE POWER DISTRIBUTION CO.", **meta("utility", bill_date),
                                        blank=unreadable_target == "utility")))

    # Income proof: salary slip
    if "MISSING_INCOME_PROOF" not in defects:
        month = submitted.replace(day=1) - timedelta(days=1)
        gross = int(net_month * rng.uniform(1.12, 1.25))
        docs.append(("salary", pdf([f"Employer: {form['employer']}", f"Employee Name: {name_variant(rng, first, middle, last)}",
                                    f"Pay Period: {month:%B %Y}", f"Gross Earnings: Rs. {gross:,}",
                                    f"Total Deductions: Rs. {gross - net_month:,}", f"Net Pay: Rs. {net_month:,}"],
                                   "SALARY SLIP", **meta("salary", month), blank=unreadable_target == "salary")))

    if unreadable_target and unreadable_target not in [k for k, _ in docs]:
        defects.remove("UNREADABLE_DOC")
    if tamper_target and tamper_target not in [k for k, _ in docs]:
        defects.remove("METADATA_TAMPER")
    rng.shuffle(docs)
    return form, docs, sorted(defects)


def generate(out: Path, n: int = 500, seed: int = 42, watchlist_path: Path | None = None) -> Path:
    rng = random.Random(seed)
    root = Path(__file__).resolve().parent.parent
    watchlist = load_watchlist(watchlist_path or root / "config" / "watchlist.csv")
    if out.exists():
        shutil.rmtree(out)
    (out / "packets").mkdir(parents=True)
    rows = []
    for i in range(1, n + 1):
        app_id = f"APP{i:05d}"
        form, docs, defects = make_packet(rng, app_id, watchlist)
        d = out / "packets" / app_id
        d.mkdir()
        (d / "application.json").write_text(json.dumps(form, indent=2))
        for j, (_, b) in enumerate(docs, 1):
            (d / f"doc_{j}.pdf").write_bytes(b)
        rows.append({"application_id": app_id, "planted_defects": ";".join(defects)})
    with open(out / "ground_truth.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["application_id", "planted_defects"])
        w.writeheader()
        w.writerows(rows)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    generate(a.out, a.n, a.seed)
    print(f"Generated {a.n} packets in {a.out / 'packets'}")


if __name__ == "__main__":
    main()
