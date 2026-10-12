"""Create fictional payslips and independent expected records."""
import json
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor


def main():
    root = Path(__file__).resolve().parent
    specs = [
        ("E101", "Alex Sample", "2026-08", 2, 4200, 840, "Northstar Demo Ltd"),
        ("E102", "Jordan Example", "2026-09", 3, 6100, 1220, "Harbor Fictional LLC"),
        ("E103", "Casey Test", "2026-09", 1, 3500, 525, "Orchard Sample Inc"),
        ("E101", "Alex Sample", "2026-09", 2, 4400, 880, "Northstar Demo Ltd"),
        ("E104", "Morgan Demo", "2026-09", 2, 5000, 1000, "Harbor Fictional LLC"),
    ]
    c = canvas.Canvas(str(root / "synthetic-payslips.pdf"), pagesize=(612, 792))
    expected, page = [], 0
    for idx, (eid, name, period, count, gross, deductions, employer) in enumerate(specs):
        pages = []
        for part in range(1, count + 1):
            page += 1
            pages.append(page)
            color = ["#17365D", "#355C42", "#6D3656"][idx % 3]
            c.setFillColor(HexColor(color))
            c.rect(36, 682, 540, 70, fill=1, stroke=0)
            c.setFillColor(HexColor("#FFFFFF"))
            c.setFont("Helvetica-Bold", 18)
            c.drawString(52, 724, ["PAY STATEMENT", "Employee Earnings Advice", "Monthly Payroll"][idx % 3])
            c.setFont("Helvetica", 12)
            c.drawString(52, 700, employer)
            c.setFillColor(HexColor("#222222"))
            lines = [
                "SYNTHETIC DEMO - FICTIONAL DATA",
                f"{['Employee ID', 'Staff number', 'Worker reference'][idx % 3]}: {eid}",
                f"Name: {name}",
                f"{['Pay period', 'Payroll month', 'Period ending'][idx % 3]}: {period}",
                f"Statement reference: DEMO-{idx + 1}",
                "Currency: USD",
            ]
            if part == 1:
                lines += ["CURRENT PERIOD EARNINGS", f"Basic salary: {gross - 200:.2f}", "Allowance: 200.00", f"Gross earnings: {gross:.2f}"]
            if part == count:
                lines += ["CURRENT PERIOD DEDUCTIONS", f"Income tax: {deductions - 100:.2f}", "Insurance: 100.00", f"Total deductions: {deductions:.2f}", f"Net pay: {gross - deductions:.2f}", f"YEAR TO DATE gross earnings: {gross * 8:.2f}"]
            elif part > 1:
                lines += ["CONTINUATION: attendance details", "Regular hours: 160", "Deductions and net pay appear on the following page."]
            y = 645
            for line in lines:
                c.setFont("Helvetica-Bold" if line.isupper() else "Helvetica", 12)
                c.drawString(52 if idx % 3 != 1 else 85, y, line)
                y -= 28
            c.setFont("Helvetica", 10)
            c.drawString(52, 48, f"Statement page {part} of {count}")
            c.drawRightString(560, 48, f"Source page {page}")
            c.showPage()
        expected.append(dict(source_pages=pages, employee_id=eid, employee_name=name,
                             employer=employer, pay_period=period, currency="USD",
                             gross_pay=gross, total_deductions=deductions, net_pay=gross-deductions))
    c.save()
    (root / "expected.json").write_text(json.dumps(expected, indent=2))


if __name__ == "__main__":
    main()
