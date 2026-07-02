"""
bp_parser.py — Deterministic Blue Prism release XML → Intermediate Representation (IR) JSON

Extracts the structural skeleton of a .bprelease file:
  - Process name and object sequence
  - Per-object: URL, element map, subsheets, ordered stages
  - Stage types: Write, Navigate, Read, Decision, SubSheet, Action, Calculation, etc.

Output: a clean IR dict (optionally saved as <name>_ir.json)

Usage:
  python bp_parser.py <file.bprelease> [output_ir.json]
"""

import xml.etree.ElementTree as ET
import json
import re
import sys
from pathlib import Path

NS_R = "http://www.blueprism.co.uk/product/release"
NS_P = "http://www.blueprism.co.uk/product/process"


def tag(el):
    return el.tag.split("}")[-1] if "}" in el.tag else el.tag


def text(el):
    return (el.text or "").strip()


def child_text(parent, local_tag):
    el = parent.find(f"{{{NS_P}}}{local_tag}")
    return text(el) if el is not None else ""


# ---------------------------------------------------------------------------
# Element map (UI element id → human name)
# ---------------------------------------------------------------------------

def build_element_map(proc_el):
    elem_map = {}
    appdef = proc_el.find(f"{{{NS_P}}}appdef")
    if appdef is None:
        return elem_map

    def walk(el):
        eid_el = el.find(f"{{{NS_P}}}id")
        if eid_el is not None and eid_el.text:
            elem_map[eid_el.text.strip()] = el.attrib.get("name", "?")
        for child in el.findall(f"{{{NS_P}}}element"):
            walk(child)

    for el in appdef.findall(f"{{{NS_P}}}element"):
        walk(el)

    return elem_map


def get_launch_url(proc_el):
    appdef = proc_el.find(f"{{{NS_P}}}appdef")
    if appdef is None:
        return None
    apptypeinfo = appdef.find(f"{{{NS_P}}}apptypeinfo")
    if apptypeinfo is None:
        return None
    for param in apptypeinfo.findall(f".//{{{NS_P}}}parameter"):
        name_el = param.find(f"{{{NS_P}}}name")
        value_el = param.find(f"{{{NS_P}}}value")
        if name_el is not None and text(name_el) == "CommandLineParams":
            url = text(value_el) if value_el is not None else ""
            if url.startswith("http"):
                return url
    return None


# ---------------------------------------------------------------------------
# Stage parsing
# ---------------------------------------------------------------------------

def parse_navigate_steps(stage_el, elem_map):
    steps = []
    for step_el in stage_el.findall(f"{{{NS_P}}}step"):
        entry = {}
        elem_el = step_el.find(f"{{{NS_P}}}element")
        if elem_el is not None:
            eid = elem_el.attrib.get("id", "")
            entry["element"] = elem_map.get(eid, eid)

        action_el = step_el.find(f"{{{NS_P}}}action")
        if action_el is not None:
            aid_el = action_el.find(f"{{{NS_P}}}id")
            entry["action"] = text(aid_el) if aid_el is not None else ""

            args_el = action_el.find(f"{{{NS_P}}}arguments")
            if args_el is not None:
                args = {}
                for arg in args_el.findall(f"{{{NS_P}}}argument"):
                    aname = arg.attrib.get("name", "")
                    aval = text(arg)
                    if aname and aval:
                        args[aname] = aval
                if args:
                    entry["arguments"] = args

        steps.append(entry)
    return steps


def parse_write_step(stage_el, elem_map):
    step_el = stage_el.find(f"{{{NS_P}}}step")
    if step_el is None:
        return {}
    result = {"expr": step_el.attrib.get("expr", "")}
    elem_el = step_el.find(f"{{{NS_P}}}element")
    if elem_el is not None:
        eid = elem_el.attrib.get("id", "")
        result["element"] = elem_map.get(eid, eid)
    return result


def parse_read_step(stage_el, elem_map):
    step_el = stage_el.find(f"{{{NS_P}}}step")
    if step_el is None:
        return {}
    result = {}
    elem_el = step_el.find(f"{{{NS_P}}}element")
    if elem_el is not None:
        eid = elem_el.attrib.get("id", "")
        result["element"] = elem_map.get(eid, eid)
    action_el = step_el.find(f"{{{NS_P}}}action")
    if action_el is not None:
        aid_el = action_el.find(f"{{{NS_P}}}id")
        result["action"] = text(aid_el) if aid_el is not None else ""
    outputs = []
    for out in stage_el.findall(f"{{{NS_P}}}outputs/{{{NS_P}}}output"):
        outputs.append(out.attrib.get("stage", ""))
    if outputs:
        result["outputs"] = outputs
    return result


def parse_action_stage(stage_el):
    resource = stage_el.find(f"{{{NS_P}}}resource")
    result = {}
    if resource is not None:
        result["object"] = resource.attrib.get("object", "")
        result["action"] = resource.attrib.get("action", "")
    inputs = {}
    for inp in stage_el.findall(f"{{{NS_P}}}inputs/{{{NS_P}}}input"):
        iname = inp.attrib.get("name", "")
        iexpr = inp.attrib.get("expr", "")
        if iname:
            inputs[iname] = iexpr
    if inputs:
        result["inputs"] = inputs
    outputs = {}
    for out in stage_el.findall(f"{{{NS_P}}}outputs/{{{NS_P}}}output"):
        oname = out.attrib.get("name", "")
        ostage = out.attrib.get("stage", "")
        if oname:
            outputs[oname] = ostage
    if outputs:
        result["outputs"] = outputs
    return result


def parse_calculation(stage_el):
    expr_el = stage_el.find(f"{{{NS_P}}}calculation")
    result = {}
    if expr_el is not None:
        result["expression"] = expr_el.attrib.get("expression", "")
        result["result"] = expr_el.attrib.get("stage", "")
    return result


def parse_stage(stage_el, elem_map, subsheet_name_map):
    s = {
        "id":          stage_el.attrib.get("stageid", ""),
        "name":        stage_el.attrib.get("name", ""),
        "type":        stage_el.attrib.get("type", ""),
        "subsheet_id": child_text(stage_el, "subsheetid"),
    }

    onsuccess = child_text(stage_el, "onsuccess")
    if onsuccess:
        s["next"] = onsuccess
    ontrue  = child_text(stage_el, "ontrue")
    onfalse = child_text(stage_el, "onfalse")
    if ontrue:
        s["on_true"] = ontrue
    if onfalse:
        s["on_false"] = onfalse

    stype = s["type"]

    if stype == "Write":
        s["write"] = parse_write_step(stage_el, elem_map)
    elif stype == "Navigate":
        steps = parse_navigate_steps(stage_el, elem_map)
        if steps:
            s["navigate_steps"] = steps
    elif stype == "Read":
        s["read"] = parse_read_step(stage_el, elem_map)
    elif stype == "Decision":
        decision_el = stage_el.find(f"{{{NS_P}}}decision")
        if decision_el is not None:
            expr = decision_el.attrib.get("expression", "")
            if expr:
                s["condition"] = expr
    elif stype == "SubSheet":
        target_id = child_text(stage_el, "processid")
        s["calls_subsheet"] = subsheet_name_map.get(target_id, target_id)
    elif stype == "Action":
        s["action"] = parse_action_stage(stage_el)
    elif stype == "Calculation":
        s["calculation"] = parse_calculation(stage_el)
    elif stype == "MultipleCalculation":
        calcs = []
        for calc in stage_el.findall(f"{{{NS_P}}}calculation"):
            calcs.append({
                "expression": calc.attrib.get("expression", ""),
                "result":     calc.attrib.get("stage", ""),
            })
        if calcs:
            s["calculations"] = calcs
    elif stype == "Data":
        s["data"] = {
            "datatype": child_text(stage_el, "datatype"),
            "initial":  child_text(stage_el, "initialvalue"),
        }
    elif stype == "Collection":
        cols = []
        for field in stage_el.findall(f".//{{{NS_P}}}field"):
            cols.append({
                "name": field.attrib.get("name", ""),
                "type": field.attrib.get("type", ""),
            })
        if cols:
            s["schema"] = cols
    elif stype == "Exception":
        s["exception"] = {
            "type":   child_text(stage_el, "exception"),
            "detail": child_text(stage_el, "detail"),
        }
    elif stype == "ChoiceStart":
        choices = []
        for choice in stage_el.findall(f"{{{NS_P}}}choice"):
            choices.append({
                "name":      choice.attrib.get("name", ""),
                "decision":  choice.attrib.get("decision", ""),
                "onsuccess": child_text(choice, "onsuccess"),
            })
        s["choices"] = choices

    return s


# ---------------------------------------------------------------------------
# Subsheet parsing
# ---------------------------------------------------------------------------

def parse_subsheets(proc_el, elem_map):
    subsheet_name_map = {}
    for stage in proc_el.findall(f"{{{NS_P}}}stage"):
        if stage.attrib.get("type") == "SubSheetInfo":
            ssid_el = stage.find(f"{{{NS_P}}}subsheetid")
            if ssid_el is not None:
                subsheet_name_map[text(ssid_el)] = stage.attrib.get("name", "")

    for ss in proc_el.findall(f"{{{NS_P}}}subsheet"):
        ssid   = ss.attrib.get("subsheetid", "")
        ssname_el = ss.find(f"{{{NS_P}}}name")
        ssname = text(ssname_el) if ssname_el is not None else ""
        if ssid and ssname:
            subsheet_name_map[ssid] = ssname

    subsheet_stages = {}
    for stage in proc_el.findall(f"{{{NS_P}}}stage"):
        parsed = parse_stage(stage, elem_map, subsheet_name_map)
        ssid   = parsed.get("subsheet_id", "")
        subsheet_stages.setdefault(ssid, []).append(parsed)

    subsheets = []
    for ss in proc_el.findall(f"{{{NS_P}}}subsheet"):
        ssid   = ss.attrib.get("subsheetid", "")
        sstype = ss.attrib.get("type", "Normal")
        ssname = subsheet_name_map.get(ssid, ssid)
        stages = subsheet_stages.get(ssid, [])
        subsheets.append({
            "id":     ssid,
            "name":   ssname,
            "type":   sstype,
            "stages": stages,
        })

    return subsheets, subsheet_name_map


# ---------------------------------------------------------------------------
# Input schema extraction
# ---------------------------------------------------------------------------

def parse_input_schema(proc_el):
    inputs = []
    for stage in proc_el.findall(f"{{{NS_P}}}stage"):
        name  = stage.attrib.get("name", "")
        stype = stage.attrib.get("type", "")
        if name.startswith("I_") or name.startswith("Input"):
            if stype == "Data":
                inputs.append({
                    "name":     name.lstrip("I_"),
                    "kind":     "scalar",
                    "datatype": child_text(stage, "datatype"),
                })
            elif stype == "Collection":
                fields = []
                for field in stage.findall(f".//{{{NS_P}}}field"):
                    fields.append({
                        "name": field.attrib.get("name", ""),
                        "type": field.attrib.get("type", ""),
                    })
                inputs.append({
                    "name":   name.lstrip("I_"),
                    "kind":   "collection",
                    "fields": fields,
                })
    return inputs


# ---------------------------------------------------------------------------
# Top-level parse
# ---------------------------------------------------------------------------

def parse_bprelease(filepath: str) -> dict:
    tree = ET.parse(filepath)
    root = tree.getroot()
    contents = root.find(f"{{{NS_R}}}contents")

    release_name_el = root.find(f"{{{NS_R}}}name")
    release_name    = text(release_name_el) if release_name_el is not None else Path(filepath).stem

    proc_container  = contents.find(f"{{{NS_P}}}process")
    main_proc_name  = proc_container.attrib.get("name", "")
    main_proc_el    = proc_container.find(f"{{{NS_P}}}process")
    input_schema    = parse_input_schema(main_proc_el)

    raw_objects = contents.findall(f"{{{NS_P}}}object")

    def sort_key(obj):
        name = obj.attrib.get("name", "")
        m = re.match(r"^(\d+)", name)
        return int(m.group(1)) if m else 999

    objects = []
    for obj in sorted(raw_objects, key=sort_key):
        obj_name = obj.attrib.get("name", "")
        proc_el  = obj.find(f"{{{NS_P}}}process")
        if proc_el is None:
            continue

        elem_map          = build_element_map(proc_el)
        url               = get_launch_url(proc_el)
        subsheets, _      = parse_subsheets(proc_el, elem_map)

        objects.append({
            "name":          obj_name,
            "url":           url,
            "element_count": len(elem_map),
            "subsheets":     subsheets,
        })

    return {
        "release_name": release_name,
        "process_name": main_proc_name,
        "input_schema": input_schema,
        "objects":      objects,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python bp_parser.py <file.bprelease> [output_ir.json]")
        sys.exit(1)

    filepath = sys.argv[1]
    outpath  = sys.argv[2] if len(sys.argv) > 2 else filepath.replace(".bprelease", "_ir.json")

    print(f"Parsing: {filepath}")
    ir = parse_bprelease(filepath)

    with open(outpath, "w") as f:
        json.dump(ir, f, indent=2)

    print(f"IR written to: {outpath}")
    print(f"  Process: {ir['process_name']}")
    print(f"  Objects: {len(ir['objects'])}")
    for obj in ir["objects"]:
        stage_count = sum(len(ss["stages"]) for ss in obj["subsheets"])
        print(f"    {obj['name']}: {len(obj['subsheets'])} subsheets, {stage_count} stages")
