"""Fictional payslips whose continuation pages carry no identifiers.

Run with reportlab available:  uv run --with reportlab python samples/make_continuation_sample.py
Writes continuation-payslips.pdf and continuation-expected.json next to this script.
"""
import json
from pathlib import Path
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent
W, H = letter


def title(c, employer, heading):
    c.setFillColor(HexColor("#17365D")); c.rect(36, 682, 540, 70, fill=1, stroke=0)
    c.setFillColor(HexColor("#FFFFFF")); c.setFont("Helvetica-Bold", 18); c.drawString(52, 724, heading)
    c.setFont("Helvetica", 12); c.drawString(52, 700, employer); c.setFillColor(HexColor("#000000"))


def table(c, top, rows, header=True):
    """Ruled table so Docling detects it; returns y of table bottom."""
    row_h, x0, x1, mid = 24, 52, 560, 400
    y = top
    for i, (label, amount) in enumerate(rows):
        c.setFont("Helvetica-Bold" if header and i == 0 else "Helvetica", 11)
        c.rect(x0, y - row_h, x1 - x0, row_h)
        c.line(mid, y, mid, y - row_h)
        c.drawString(x0 + 6, y - 16, label); c.drawRightString(x1 - 6, y - 16, amount)
        y -= row_h
    return y


def identity(c, eid, name, period, ref, y=645):
    for line in (f"Employee ID: {eid}", f"Name: {name}", f"Pay period: {period}", f"Statement reference: {ref}", "Currency: USD"):
        c.setFont("Helvetica", 12); c.drawString(52, y, line); y -= 22


def main():
    c = canvas.Canvas(str(OUT / "continuation-payslips.pdf"), pagesize=letter)
    # Payslip 1 (pages 1-2): earnings table runs to the page bottom and continues
    # on page 2 as a header-less table, with NO identifiers or page numbering.
    title(c, "Lakeside Demo Corp", "PAY STATEMENT")
    identity(c, "E201", "Riley Sample", "2026-09", "CONT-1")
    earn = [("Earnings", "Amount")] + [(f"Shift pay entry {i}", "100.00") for i in range(1, 19)]
    table(c, 520, earn)  # ends near the page bottom
    c.showPage()
    table(c, 740, [("Overtime", "100.00"), ("Bonus", "100.00"), ("Gross earnings", "2000.00"),
                   ("Income tax", "300.00"), ("Total deductions", "400.00"), ("Net pay", "1600.00")], header=False)
    c.showPage()
    # Payslip 2 (page 3): single page
    title(c, "Lakeside Demo Corp", "PAY STATEMENT")
    identity(c, "E202", "Sam Example", "2026-09", "CONT-2")
    table(c, 520, [("Earnings", "Amount"), ("Gross earnings", "3000.00"), ("Total deductions", "600.00"), ("Net pay", "2400.00")])
    c.showPage()
    # Payslip 3 (pages 4-5): plain-text continuation, same running title, no table, no ids
    title(c, "Lakeside Demo Corp", "PAY STATEMENT")
    identity(c, "E203", "Dana Demo", "2026-09", "CONT-3")
    c.setFont("Helvetica", 12)
    y = 500
    for line in ("Basic salary: 3800.00", "Allowance: 200.00", "Gross earnings: 4000.00"):
        c.drawString(52, y, line); y -= 22
    c.showPage()
    c.setFont("Helvetica", 12)
    y = 740
    for line in ("Income tax: 700.00", "Insurance: 100.00", "Total deductions: 800.00", "Net pay: 3200.00"):
        c.drawString(52, y, line); y -= 22
    c.showPage()
    # Payslip 4 (pages 6-7): continuation repeats the title block but has no identifiers
    title(c, "Lakeside Demo Corp", "PAY STATEMENT")
    identity(c, "E204", "Quinn Test", "2026-09", "CONT-4")
    c.setFont("Helvetica", 12)
    y = 500
    for line in ("Basic salary: 2700.00", "Allowance: 300.00", "Gross earnings: 3000.00"):
        c.drawString(52, y, line); y -= 22
    c.showPage()
    title(c, "Lakeside Demo Corp", "PAY STATEMENT")
    c.setFont("Helvetica", 12)
    y = 600
    for line in ("Income tax: 450.00", "Insurance: 150.00", "Total deductions: 600.00", "Net pay: 2400.00"):
        c.drawString(52, y, line); y -= 22
    c.showPage()
    c.save()
    expected = [
        dict(source_pages=[1, 2], employee_id="E201", employee_name="Riley Sample", employer="Lakeside Demo Corp",
             pay_period="2026-09", currency="USD", gross_pay=2000, total_deductions=400, net_pay=1600),
        dict(source_pages=[3], employee_id="E202", employee_name="Sam Example", employer="Lakeside Demo Corp",
             pay_period="2026-09", currency="USD", gross_pay=3000, total_deductions=600, net_pay=2400),
        dict(source_pages=[4, 5], employee_id="E203", employee_name="Dana Demo", employer="Lakeside Demo Corp",
             pay_period="2026-09", currency="USD", gross_pay=4000, total_deductions=800, net_pay=3200),
        dict(source_pages=[6, 7], employee_id="E204", employee_name="Quinn Test", employer="Lakeside Demo Corp",
             pay_period="2026-09", currency="USD", gross_pay=3000, total_deductions=600, net_pay=2400),
    ]
    (OUT / "continuation-expected.json").write_text(json.dumps(expected, indent=2))


if __name__ == "__main__":
    main()
