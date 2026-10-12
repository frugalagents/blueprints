"""Local layout signals from the Docling document for page-boundary decisions.

Used when the anchor rules (employee id, period, statement reference,
"page X of Y") cannot decide, for example a page that only continues a table.
Each signal adds to / subtracts from a continuation score; a strong score
decides locally, otherwise the pair is escalated to the model.
"""
import re

HEADINGS = {"section_header", "title"}
CONTINUED = re.compile(r"\b(?:continued|cont['’]?d\.?)\b", re.I)
CONTINUES = re.compile(r"continue[sd]?\s+(?:on|to|overleaf)|see\s+next\s+page|overleaf", re.I)
SAME_AT, NEW_AT = 3, -3


def norm(text):
    return re.sub(r"\s+", " ", text).strip().lower()


def page_layouts(doc):
    """Body items per page (furniture and page headers/footers excluded), top to bottom."""
    layouts = {int(n): dict(height=p["size"]["height"], items=[]) for n, p in doc["pages"].items()}

    def add(kind, item, text, rows=0, header=()):
        if item.get("content_layer", "body") != "body" or item.get("label") in ("page_header", "page_footer"):
            return
        if not item.get("prov"):
            return
        prov = item["prov"][0]
        page = layouts[prov["page_no"]]
        box, height = prov["bbox"], page["height"]
        a, b = (height - box["t"], height - box["b"]) if box.get("coord_origin") == "BOTTOMLEFT" else (box["t"], box["b"])
        page["items"].append(dict(kind=kind, label=item.get("label"), text=text, rows=rows,
                                  header=header, top=min(a, b), bottom=max(a, b)))

    for item in doc.get("texts", []):
        add("text", item, item.get("text", ""))
    for item in doc.get("tables", []):
        cells = item["data"]["table_cells"]
        add("table", item, " ".join(c["text"] for c in cells), item["data"]["num_rows"],
            tuple(norm(c["text"]) for c in cells if c.get("column_header")))
    for page in layouts.values():
        page["items"].sort(key=lambda i: i["top"])
    return layouts


def opens_with_title(page):
    first = page["items"][0] if page["items"] else None
    # A one/two-row table at the top is a banner, not a data table
    return bool(first) and (first["label"] in HEADINGS or (first["kind"] == "table" and first["rows"] <= 2))


def head(page):
    return [norm(i["text"]) for i in page["items"][:2]]


def boundary_from_signals(left, right, left_anchors, right_anchors):
    """Return (decision, evidence, score) for adjacent pages (layout dicts + anchor dicts)."""
    score, reasons = 0, []

    def add(points, reason):
        nonlocal score
        score += points
        reasons.append(f"{points:+d} {reason}")

    a, b = left_anchors.get("numbering"), right_anchors.get("numbering")
    if b and b[0] == 1:
        add(-4, "right page is statement page 1")
    elif a and b:
        if a[1] == b[1] and b[0] == a[0] + 1:
            add(3, "sequential statement page numbers")
        else:
            add(-3, "statement page numbers are not sequential")
    elif a and a[0] == a[1]:
        add(-2, "previous statement had ended and right page has no continuation numbering")

    right_start = " ".join(head(right))
    if CONTINUED.search(right_start):
        add(3, "right page is marked continued")
    if CONTINUES.search(" ".join(norm(i["text"]) for i in left["items"][-3:])):
        add(2, "left page says it continues on the next page")

    last = left["items"][-1] if left["items"] else None
    tables = [i for i in right["items"] if i["kind"] == "table"]
    first_table = tables[0] if tables else None
    if (last and last["kind"] == "table" and last["bottom"] > 0.7 * left["height"]
            and first_table and first_table["top"] < 0.25 * right["height"] and first_table["rows"] > 2):
        if not first_table["header"]:
            add(3, "table runs to the bottom of the left page and continues on the right without a header row")
        elif first_table["header"] == last["header"]:
            add(1, "table continues with a repeated header row")

    left_title, right_title = opens_with_title(left), opens_with_title(right)
    if left_title and not right_title:
        add(2, "right page has no title block but the left page does")
    elif left_title and right_title:
        if head(left) != head(right):
            add(-2, "right page opens with a different title block")
    elif right_title:
        add(-1, "right page opens with a title block")

    if left_anchors.keys() & {"employee", "statement"} and not right_anchors.keys() & {"employee", "statement"}:
        add(1, "right page has no employee or statement identifier while the left page does")

    decision = "same" if score >= SAME_AT else "new" if score <= NEW_AT else "uncertain"
    return decision, f"Layout signals (score {score:+d}): " + ("; ".join(reasons) or "no signals"), score
