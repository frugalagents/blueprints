"""Docling local parsing + explicit grouping rules + Bedrock text extraction."""
import argparse
import json
import os


def _load_env():
    """Load KEY=VALUE pairs from .env beside this script; real env vars win."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_env()
import re
import sys
import time
from decimal import Decimal
from pathlib import Path

from prompts import BOUNDARY, EXTRACT, decode
from signals import boundary_from_signals, page_layouts


def anchors(text):
    patterns = {
        "employee": r"(?:employee\s*id|staff\s*number|worker\s*reference)\s*:\s*([A-Za-z0-9_-]+)",
        "period": r"(?:pay\s*period|payroll\s*month|period\s*ending)\s*:\s*(\d{4}-\d{2})",
        "statement": r"statement\s*reference\s*:\s*([A-Za-z0-9_-]+)",
    }
    result = {}
    for key, pattern in patterns.items():
        matches = set(re.findall(pattern, text, re.I))
        if len(matches) == 1:
            result[key] = next(iter(matches)).upper()
    numbering = re.search(r"statement\s*page\s+(\d+)\s+of\s+(\d+)", text, re.I)
    if numbering:
        result["numbering"] = [int(x) for x in numbering.groups()]
    return result


def group_anchors(pages, extracted):
    """Identity of the group so far (earliest page wins); numbering from its last page."""
    merged = {}
    for number in pages:
        for key in ("employee", "period", "statement"):
            if key in extracted[number]:
                merged.setdefault(key, extracted[number][key])
    if "numbering" in extracted[pages[-1]]:
        merged["numbering"] = extracted[pages[-1]]["numbering"]
    return merged


def rule_boundary(left, right):
    # Missing anchors are unknown. Conflicting anchors always prevent a merge.
    for key in ("employee", "period", "statement"):
        if key in left and key in right and left[key] != right[key]:
            return "new", f"Different {key}: {left[key]} vs {right[key]}"
    same_statement = left.get("statement") and left.get("statement") == right.get("statement")
    same_identity = all(k in left and left[k] == right.get(k) for k in ("employee", "period"))
    a, b = left.get("numbering"), right.get("numbering")
    continuation = a and b and a[1] == b[1] and b[0] == a[0]+1
    if continuation and (same_statement or same_identity):
        return "same", "Matching statement or employee/period with sequential statement pages"
    if a and b and a[0] == a[1] and b[0] == 1:
        return "new", "Previous statement finished; next page starts a statement"
    return "uncertain", "Rules have insufficient evidence; request model boundary comparison"


def render_png(pdf_path, number):
    import io
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        buffer = io.BytesIO()
        pdf[number-1].render(scale=1.5).to_pil().save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        pdf.close()


def atomic(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)


def run(args):
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat, ConversionStatus
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling_core.types.doc import ContentLayer
    import boto3
    from botocore.config import Config

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    events, calls, usage = [], [], []
    def emit(stage, title, pages=None, **details):
        events.append(dict(sequence=len(events)+1, timestamp=time.time(), stage=stage,
                           title=title, source_pages=pages or [], **details))
        atomic(out / "events.json", events)

    emit("render", "Docling parsing PDF locally: layout, text and tables")
    options = PdfPipelineOptions()
    options.do_ocr = not args.no_ocr
    options.do_table_structure = True
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    parsed = converter.convert(Path(args.pdf))
    if parsed.status != ConversionStatus.SUCCESS:
        raise ValueError(f"Docling conversion did not fully succeed: {parsed.status}")
    document = parsed.document
    document_dict = document.export_to_dict()
    atomic(out / "docling-document.json", document_dict)
    layouts = page_layouts(document_dict)
    page_numbers = sorted(document.pages)
    if page_numbers != list(range(1, len(page_numbers)+1)):
        raise ValueError("Unexpected original page numbering")
    texts = {}
    for number in page_numbers:
        text = document.export_to_markdown(
            page_no=number, included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE})
        if not text.strip():
            raise ValueError(f"No usable parsed content on page {number}; review required")
        texts[number] = text
        (out / f"page-{number}.md").write_text(text)
        emit("render", f"Docling page {number}: text and tables ready locally", [number])
    emit("render_done", "Local parsing complete; only grouped text will be sent to Qwen")
    extracted_anchors = {n: anchors(text) for n, text in texts.items()}
    atomic(out / "anchors.json", extracted_anchors)
    client = boto3.Session(profile_name=args.aws_profile, region_name=args.region).client(
        "bedrock-runtime", config=Config(retries={"mode": "adaptive", "total_max_attempts": 3},
                                        connect_timeout=10, read_timeout=180))
    def infer(pages, prompt, task, images=False):
        atomic(out / "progress.json", dict(stage=task, source_pages=pages))
        content = [{"text": prompt}]
        for number in pages:
            if images:
                content.append({"text": f"Original PDF source page: {number}"})
                content.append({"image": {"format": "png", "source": {"bytes": render_png(args.pdf, number)}}})
            else:
                content.append({"text": f"Original PDF source page: {number}\n{texts[number]}"})
        if sum(len(c.get("text", "")) for c in content) > 100000:
            raise ValueError("Group exceeds the POC's text size limit; review required")
        emit("request", "Sending page images to Bedrock Qwen" if images else
             "Sending parsed page text and tables to Bedrock Qwen", pages, task=task)
        started = time.monotonic()
        response = client.converse(modelId=args.inference_profile,
                                  messages=[dict(role="user", content=content)],
                                  inferenceConfig=dict(maxTokens=2500, temperature=0))
        atomic(out / f"{task}-response.json", response)
        calls.append(dict(task=task, source_pages=pages, input_type="image" if images else "text"))
        usage.append(response.get("usage", {}))
        emit("response", "Qwen response received", pages, task=task,
             elapsed_seconds=round(time.monotonic()-started, 2), usage=response.get("usage", {}))
        if response.get("stopReason") == "max_tokens":
            raise ValueError("Truncated model output")
        return decode("".join(c.get("text", "") for c in response["output"]["message"]["content"]))

    boundaries, groups, current = [], [], [1]
    if args.groups:
        groups = json.loads(Path(args.groups).read_text())
        if not all(isinstance(g, list) and g for g in groups) or [p for g in groups for p in g] != page_numbers:
            raise ValueError("Reviewed groups must cover all pages in order exactly once")
    else:
        for n in page_numbers[1:]:
            left_anchors = group_anchors(current, extracted_anchors)
            decision, evidence = rule_boundary(left_anchors, extracted_anchors[n])
            method = "local_rules"
            if decision == "uncertain":
                decision, signal_evidence, _ = boundary_from_signals(
                    layouts[n-1], layouts[n], left_anchors, extracted_anchors[n])
                evidence = f"{evidence}. {signal_evidence}"
                method = "layout_signals" if decision != "uncertain" else method
            if decision == "uncertain" and not args.parse_only:
                response = infer([n-1, n], BOUNDARY.replace("two images", "two parsed pages"), f"boundary-{n-1}-{n}")
                decision, evidence = response.get("decision"), response.get("evidence", "")
                method = "qwen_text"
                if decision == "uncertain" and not args.no_image_fallback:
                    response = infer([n-1, n], BOUNDARY, f"boundary-image-{n-1}-{n}", images=True)
                    decision, evidence = response.get("decision"), response.get("evidence", "")
                    method = "qwen_image"
            if decision not in ("same", "new", "uncertain"):
                raise ValueError("Invalid model boundary decision")
            boundaries.append(dict(pages=[n-1, n], decision=decision, evidence=evidence, method=method))
            emit("boundary", f"Boundary: {decision} ({method})", [n-1, n], evidence=evidence)
            if decision == "same":
                current.append(n)
            else:
                groups.append(current)
                current = [n]
        groups.append(current)
    atomic(out / "boundaries.json", boundaries)
    atomic(out / "groups.json", groups)
    emit("groups", f"{len(groups)} payslip groups assembled", groups=groups)
    if any(b["decision"] == "uncertain" for b in boundaries):
        atomic(out / "provisional-groups.json", groups)
        raise ValueError("Uncertain boundaries require review")
    if args.parse_only:
        emit("complete", "Local parsing and grouping complete; no Bedrock requests")
        return
    records = []
    for i, pages in enumerate(groups):
        prompt = EXTRACT.replace("These images", "These parsed pages") + """
Copy the complete employee name exactly as shown after the name label, up to
the next field label. Preserve every surname token; do not shorten names or
treat part of a name as a document annotation."""
        record = infer(pages, prompt, f"extract-{i+1}")
        record["source_pages"] = pages
        issues = []
        name_pattern = r"\bName:\s*(.+?)(?=\s+(?:Pay\s*period|Payroll\s*month|Period\s*ending|Statement\s*reference|Currency)\s*:|\n|$)"
        source_names = {m.strip() for n in pages for m in re.findall(name_pattern, texts[n], re.I)}
        if len(source_names) == 1 and record.get("employee_name") != next(iter(source_names)):
            issues.append("Extracted full name differs from explicitly labeled source name")
        for field in ("employee_name", "employee_id", "pay_period", "gross_pay", "total_deductions", "net_pay"):
            e = record.get("evidence", {}).get(field, {})
            number, quote = e.get("source_page"), e.get("text")
            if record.get(field) is None or number not in pages or not quote:
                issues.append(f"Missing field or evidence: {field}")
            elif re.sub(r"\s+", " ", str(quote)).strip() not in re.sub(r"\s+", " ", texts[number]):
                issues.append(f"Evidence text does not match parsed source: {field}")
        values = [record.get(k) for k in ("gross_pay", "total_deductions", "net_pay")]
        if all(type(v) in (int, float) for v in values):
            if abs(Decimal(str(values[0]))-Decimal(str(values[1]))-Decimal(str(values[2]))) > Decimal(".01"):
                issues.append("Payroll arithmetic mismatch")
        else:
            issues.append("Missing or nonnumeric totals")
        record["review_reasons"] = issues
        records.append(record)
        emit("validated", f"Payslip {i+1} extracted and checked", pages, review_reasons=issues)
    atomic(out / "records.json", records)
    report = dict(page_count=len(texts), payslip_count=len(records), calls=calls, usage=usage,
                  boundary_model_calls=sum(c["task"].startswith("boundary") for c in calls),
                  boundary_image_calls=sum(c["input_type"] == "image" for c in calls),
                  boundary_methods={m: sum(b["method"] == m for b in boundaries) for m in sorted({b["method"] for b in boundaries})},
                  input_type="text_with_image_fallback" if any(c["input_type"] == "image" for c in calls) else "text_only",
                  parser="docling")
    if args.expected:
        expected = json.loads(Path(args.expected).read_text())
        checks = [dict(record=i+1, field=k, expected=v, actual=(records[i] if i<len(records) else {}).get(k),
                       passed=(records[i] if i<len(records) else {}).get(k)==v)
                  for i, truth in enumerate(expected) for k, v in truth.items()]
        report.update(checks=checks, exact_grouping=[r["source_pages"] for r in records]==[r["source_pages"] for r in expected],
                      all_fields_match=len(records)==len(expected) and all(c["passed"] for c in checks))
    atomic(out / "report.json", report)
    emit("complete", f"Complete: {len(records)} payslips; {len(calls)} Bedrock requests")
    print(json.dumps({k:v for k,v in report.items() if k not in ("checks","calls","usage")}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf")
    parser.add_argument("--output", default="results")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--inference-profile", default=os.environ.get("BEDROCK_INFERENCE_PROFILE"),
                        help="Bedrock inference profile ARN or model ID (default: $BEDROCK_INFERENCE_PROFILE)")
    parser.add_argument("--aws-profile")
    parser.add_argument("--expected")
    parser.add_argument("--groups")
    parser.add_argument("--no-ocr", action="store_true", help="Only for PDFs with an embedded text layer")
    parser.add_argument("--no-image-fallback", action="store_true",
                        help="Do not send page images to Qwen for boundaries that text could not settle")
    parser.add_argument("--parse-only", action="store_true")
    args = parser.parse_args()
    if not args.inference_profile:
        parser.error("set --inference-profile or BEDROCK_INFERENCE_PROFILE")
    run(args)


if __name__ == "__main__":
    main()
