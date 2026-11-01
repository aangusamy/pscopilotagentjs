"""Excel to CI from the agent panel: load spreadsheet rows into PeopleSoft through its Component Interfaces, the
way ExcelToCI does, without the macro workbook.

  catalog      the CIs the user may load (PSBCDEFN, PSAUTHBUSCOMP through the user's permission lists)
  shape        the CI's keys, properties and collections, from SOAPTOCI's GetCIShape (the call ExcelToCI's
               "New Template" makes), enriched from PeopleTools metadata (record/field, translate values)
  template     an .xlsx built from the shape: the CI's sheet (Action, keys, properties), one sheet per collection
               (linked by the parent keys), a Help sheet
  check        every row before anything is sent: keys, required, lengths, numbers and dates, translate values,
               prompt table values (read-only queries), whether the key exists (Create) or not (Update), duplicates
  envelope     one row as the SOAP request WEBLIB_SOAPTOCI expects (Create__CompIntfc__CI / Updatedata__...)
  result       SOAPTOCI's answer: saved (notification 1), the CI's messages (errors, warnings)

The browser posts each envelope to WEBLIB_SOAPTOCI.SOAPTOCI.FieldFormula.IScript_SOAPToCI?postDataBin=y (without
it the servlet drops the body and SOAPTOCI answers "No content provided.") in the user's own PeopleSoft session, so every row is saved as the user, with their CI permissions; the service never signs in.
Password properties are never in the file: the user types them on the load card and the browser fills them in.
Format verified against SOAPTOCI:SOAPToCI (PT 8.61): one action per request (it is read from the first
element), properties as child elements, collection rows as repeated child elements, CINodeAction on a collection
row (INSERT / DELETE) in an Updatedata request, dates as YYYYMMDD.
"""

import json
import logging
import re
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

from . import audit, xlsx
from .db import QueryError
from .runbooks import DocError, xlsx_sheets

log = logging.getLogger("psagent")

SOAPTOCI = "WEBLIB_SOAPTOCI.SOAPTOCI.FieldFormula.IScript_SOAPToCI"
MAX_ROWS = 5000                      # rows in one load
SHAPE_VERSION = 2                    # kept shapes older than this are read again from PeopleSoft (2: get and create keys)
SECRET = re.compile(r"PSWD|PASSWORD|PASSWD", re.I)
TYPES = {0: "Text", 1: "Long text", 2: "Number", 3: "Signed number", 4: "Date", 5: "Time", 6: "Date and time"}
ROW_ACTIONS = {"change": "", "add": "INSERT", "delete": "DELETE"}
HEADER_ROW = 2                       # 0-based: title, instructions, then the column headers


class CIError(Exception):
    pass


def _local(tag):
    return str(tag).rsplit("}", 1)[-1]


def _true(v):
    return str(v or "").strip().lower() in ("true", "1", "y", "yes")


def _table(rec, sqltable=""):
    name = (sqltable or "").strip() or ("PS_" + rec)
    return re.sub(r"[^A-Z0-9_$#]", "", name.upper())


# ----- catalog ------------------------------------------------------------------------------------

CATALOG_SQL = """SELECT D.BCNAME, D.DESCR,
  MAX(CASE WHEN A.BCMETHOD = 'Create' THEN 1 ELSE 0 END) AS CAN_CREATE,
  MAX(CASE WHEN A.BCMETHOD = 'Get' THEN 1 ELSE 0 END) AS CAN_GET,
  MAX(CASE WHEN A.BCMETHOD = 'Save' THEN 1 ELSE 0 END) AS CAN_SAVE
FROM PSBCDEFN D, PSAUTHBUSCOMP A
WHERE A.BCNAME = D.BCNAME AND A.CLASSID IN (SELECT CLASSID FROM PS_USERCLASS_VW WHERE OPRID = :oprid){filter}
GROUP BY D.BCNAME, D.DESCR
ORDER BY D.BCNAME"""


def catalog(db, oprid, text=""):
    """The CIs the user may save through (Save plus Create or Get), as SOAPTOCI decides it. -> (cis, more)"""
    params, filt = {"oprid": oprid}, ""
    words = [w for w in re.split(r"\s+", str(text or "").upper().strip()) if w][:4]
    for i, w in enumerate(words):
        filt += f" AND (UPPER(D.BCNAME) LIKE :w{i} OR UPPER(D.DESCR) LIKE :w{i})"
        params[f"w{i}"] = f"%{w}%"
    _, rows, more = db.query(CATALOG_SQL.format(filter=filt), params, max_rows=200, who=oprid, why="ci catalog")
    out = [{"name": r[0], "descr": (r[1] or "").strip(), "create": bool(r[2] and r[4]), "update": bool(r[3] and r[4])}
           for r in rows if r[4] and (r[2] or r[3])]
    return out, more


# ----- shape -------------------------------------------------------------------------------------

def not_xml_reason(text):
    t = str(text or "")
    if re.search(r"Authorization Error|not authorized", t, re.I):
        return "you have no access to WEBLIB_SOAPTOCI (the same Web Library ExcelToCI uses): ask your security administrator"
    if re.search(r"userid|signin|sign in|login", t, re.I):
        return "your PeopleSoft session has expired: sign in again, then resume"
    return "PeopleSoft did not answer with SOAP: " + re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)).strip()[:200]


def messages_of(root):
    out = []
    for m in root.iter():
        if _local(m.tag) == "messages":
            d = {_local(c.tag): (c.text or "").strip() for c in m}
            out.append({"type": d.get("type") or "Error", "set": d.get("messagesetnumber", ""), "num": d.get("messagenumber", ""),
                        "text": d.get("messagetext", ""), "explain": d.get("explaintext", "")})
    return out


def parse_shape(text, ci):
    """SOAPTOCI's GetCIShape answer -> the shape: {ci, descr, can: {create, update}, root: collection}
    collection: {name, path, level, props: [prop], collections: [collection]}"""
    try:
        root = ET.fromstring(str(text).encode("utf-8"))
    except ET.ParseError as e:
        raise CIError(not_xml_reason(text)) from e
    if _local(root.tag) != "Envelope":
        raise CIError(not_xml_reason(text))
    level0 = next((e for e in root.iter() if _local(e.tag) == "Level0-Collection"), None)
    if level0 is None:
        msgs = messages_of(root)
        fault = next((e.text for e in root.iter() if _local(e.tag) == "faultstring" and e.text), "")
        raise CIError((msgs[0]["text"] if msgs else fault) or f"PeopleSoft returned no shape for {ci}")
    descr = next(((e.text or "").strip() for e in root.iter() if _local(e.tag) == "description"), "")
    action = next(((e.text or "").strip().upper() for e in root.iter() if _local(e.tag) == "action"), "")

    def coll(el, name, path, level):
        props, colls, seen = [], [], {}
        for ch in el:
            t = _local(ch.tag)
            if re.fullmatch(r"Level\d+-Collection", t):
                n = ch.get("name") or t
                colls.append(coll(ch, n, (path + "/" + n) if path else n, level + 1))
                continue
            a = ch.attrib
            length = re.match(r"(\d+)(?:\.(\d+))?", a.get("fieldlength") or "")
            p = {"name": t, "label": (a.get("label") or t).strip() or t, "type": int(a.get("fieldtype") or 0) if str(a.get("fieldtype") or "0").isdigit() else 0,
                 "length": int(length.group(1)) if length else 0, "decimals": int(length.group(2) or 0) if length else 0,
                 "key": _true(a.get("key")), "required": _true(a.get("required")), "readonly": _true(a.get("readonly")),
                 # edit is a True/False flag on PT 8.61, not the prompt table: that comes from PSRECFIELD (enrich)
                 "edit": "" if _true(a.get("edit")) or (a.get("edit") or "").strip().lower() == "false" else (a.get("edit") or "").strip(),
                 "secret": bool(SECRET.search(t))}
            if t in seen:                       # the key info and the property: one column
                q = seen[t]
                q["key"] = q["key"] or p["key"]
                q["required"] = q["required"] or p["required"]
                continue
            seen[t] = p
            props.append(p)
        props.sort(key=lambda p: not p["key"])  # keys first, then the CI's order
        return {"name": name, "path": path, "level": level, "props": props, "collections": colls}

    return {"ci": ci, "descr": descr, "action": action, "root": coll(level0, ci, "", 0), "v": SHAPE_VERSION}


def collections(shape):
    """Every collection with its ancestors (root first), depth first."""
    out = []

    def walk(c, chain):
        for ch in c["collections"]:
            out.append((ch, chain + [c]))
            walk(ch, chain + [c])
    walk(shape["root"], [])
    return out


def keys_of(c):
    return [p for p in c["props"] if p["key"]]


def action_keys(shape, action):
    """The level 0 keys SOAPTOCI sets (setKeys): the CI's create keys for Create, its get keys for Update. GetCIShape
    lists only the create keys when there are any, and they can differ (CI_HR_LBR_GRP_DEFN: GB_GROUP_ID to create,
    GB_GROUP_ID and PNLGRPNAME to get)."""
    names = shape.get("create_keys" if action == "create" else "get_keys")
    if not names:                       # no PeopleTools metadata (no database): the keys in the shape
        return keys_of(shape["root"])
    return [p for p in shape["root"]["props"] if p["name"] in names]


def sub_collection(c, name):
    return next((ch for ch in c["collections"] if ch["name"] == name), None)


USE_REQUIRED, USE_XLAT, USE_YESNO, USE_PROMPT = 0x100, 0x200, 0x2000, 0x4000     # PSRECFIELD.USEEDIT bits


def enrich(shape, db, who):
    """Record and field of every property (PSBCITEM), its edits (PSRECFIELD: prompt table, translate, Yes/No,
    required) and translate values (PSXLATITEM)."""
    if db is None:
        return shape
    if not shape["descr"]:                         # GetCIShape answers <description/> on PT 8.61
        try:
            _, rows, _ = db.query("SELECT DESCR FROM PSBCDEFN WHERE BCNAME = :ci", {"ci": shape["ci"]}, max_rows=1, who=who,
                                  why="ci description")
            shape["descr"] = (rows[0][0] or "").strip() if rows else ""
        except QueryError as e:
            log.warning("CI %s description: %s", shape["ci"], e)
    try:
        _, rows, _ = db.query("""SELECT I.BCITEMNAME, I.BCITEMPARENT, I.BCTYPE, I.RECNAME, I.FIELDNAME, R.EDITTABLE, R.USEEDIT
            FROM PSBCITEM I, PSRECFIELD R WHERE I.BCNAME = :ci AND I.BCTYPE IN (1, 2, 4)
            AND R.RECNAME (+) = I.RECNAME AND R.FIELDNAME (+) = I.FIELDNAME""",
                              {"ci": shape["ci"]}, max_rows=3000, who=who, why="ci items")
    except QueryError as e:
        log.warning("CI %s items: %s", shape["ci"], e)
        return shape
    where, search, keyset = {}, {}, {1: [], 2: []}
    for item, parent, kind, rec, fld, edit, use in rows:
        k = (item.strip(), (parent or "").strip())
        if int(kind) == 4:
            where[k] = ((rec or "").strip(), (fld or "").strip(), (edit or "").strip(), int(use or 0))
        else:
            search[k] = ((rec or "").strip(), (fld or "").strip())
            if k[1] == "PS_ROOT" and k[0] not in keyset[int(kind)]:
                keyset[int(kind)].append(k[0])         # BCTYPE 1: get keys, 2: create keys
    shape["get_keys"], shape["create_keys"] = keyset[1], keyset[2]
    _add_key_props(shape, db, who, search)
    fields = set()

    def walk(c):
        parent = "PS_ROOT" if c["level"] == 0 else c["name"]
        for p in c["props"]:
            rec, fld, edit, use = where.get((p["name"], parent), ("", "", "", 0))
            srec, sfld = search.get((p["name"], parent), ("", ""))
            p["record"], p["field"], p["search_record"] = rec, fld or sfld, srec
            if c["level"] == 0:
                p["is_prop"] = (p["name"], parent) in where        # not only a key (KEYPROP_EMPLID): sent only with its action
                p["key_for"] = ("" if p["name"] in shape["get_keys"] and p["name"] in shape["create_keys"] or not shape["create_keys"]
                                else "update" if p["name"] in shape["get_keys"] else "create" if p["name"] in shape["create_keys"] else "")
            p["required"] = p["required"] or bool(use & USE_REQUIRED and p["key"])
            p["dynamic"] = bool(use & USE_PROMPT and edit.startswith("%"))      # %EDITTABLE: set by PeopleCode, not checked
            if use & USE_PROMPT and edit and not edit.startswith("%"):
                p["edit"] = edit
            elif p["edit"].startswith("%") or not (use & USE_PROMPT):
                p["edit"] = "" if not p["edit"] or p["edit"].startswith("%") else p["edit"]
            p["xlat"] = bool(use & USE_XLAT)
            p["yesno"] = bool(use & USE_YESNO)
            if fld and p["xlat"]:
                fields.add(fld)
            if p["field"]:
                every.add(p["field"])
        for ch in c["collections"]:
            walk(ch)
    every = set()
    walk(shape["root"])
    upper = set()                           # PSDBFIELD.FORMAT 0: Uppercase (EMPLID, SETID); 6: Mixed case (DESCR, OPRID)
    names = sorted(every)
    for i in range(0, len(names), 500):
        binds = {f"f{j}": n for j, n in enumerate(names[i:i + 500])}
        try:
            _, rows, _ = db.query("SELECT FIELDNAME FROM PSDBFIELD WHERE FIELDTYPE = 0 AND FORMAT = 0 AND FIELDNAME IN ("
                                  + ", ".join(":" + k for k in binds) + ")", binds, max_rows=1000, who=who, why="ci uppercase fields")
            upper |= {r[0].strip() for r in rows}
        except QueryError as e:
            log.warning("CI %s field formats: %s", shape["ci"], e)
            break

    def walk_upper(c):
        for p in c["props"]:
            p["upper"] = p.get("field") in upper
        for ch in c["collections"]:
            walk_upper(ch)
    walk_upper(shape["root"])
    xlat = {}
    names = sorted(fields)
    for i in range(0, len(names), 200):
        chunk = names[i:i + 200]
        binds = {f"f{j}": n for j, n in enumerate(chunk)}
        try:
            _, rows, _ = db.query("SELECT FIELDNAME, FIELDVALUE, XLATLONGNAME FROM PSXLATITEM WHERE EFF_STATUS = 'A' AND FIELDNAME IN ("
                                  + ", ".join(":" + k for k in binds) + ") ORDER BY FIELDNAME, FIELDVALUE", binds, max_rows=3000,
                                  who=who, why="ci translate values")
        except QueryError as e:
            log.warning("CI %s translate values: %s", shape["ci"], e)
            break
        for f, v, label in rows:
            if (v, (label or "").strip()) not in xlat.setdefault(f, []):
                xlat[f].append((v, (label or "").strip()))

    def walk2(c):
        for p in c["props"]:
            p["values"] = xlat.get(p.get("field"), []) if p.get("xlat") else [("Y", "Yes"), ("N", "No")] if p.get("yesno") else []
        for ch in c["collections"]:
            walk2(ch)
    walk2(shape["root"])
    return shape


def _add_key_props(shape, db, who, search):
    """Get keys GetCIShape leaves out (it lists the create keys): added as key columns, typed from PSDBFIELD."""
    root = shape["root"]
    have = {p["name"] for p in root["props"]}
    missing = [k for k in shape["get_keys"] + shape["create_keys"] if k not in have]
    if not missing:
        return
    fields = {k: search.get((k, "PS_ROOT"), ("", ""))[1] or k for k in missing}
    binds = {f"f{i}": f for i, f in enumerate(sorted(set(fields.values())))}
    meta = {}
    try:
        _, rows, _ = db.query("SELECT F.FIELDNAME, F.FIELDTYPE, F.LENGTH, F.DECIMALPOS, L.LONGNAME FROM PSDBFIELD F, PSDBFLDLABL L "
                              "WHERE L.FIELDNAME (+) = F.FIELDNAME AND L.DEFAULT_LABEL (+) = 1 AND F.FIELDNAME IN ("
                              + ", ".join(":" + k for k in binds) + ")", binds, max_rows=100, who=who, why="ci key fields")
        meta = {r[0].strip(): r for r in rows}
    except QueryError as e:
        log.warning("CI %s key fields: %s", shape["ci"], e)
    added = []
    for k in missing:
        m = meta.get(fields[k])
        t = int(m[1]) if m and str(m[1]).isdigit() and int(m[1]) <= 6 else 0
        added.append({"name": k, "label": ((m[4] or "").strip() if m else "") or k, "type": t, "length": int(m[2] or 0) if m else 0,
                      "decimals": int(m[3] or 0) if m else 0, "key": True, "required": True, "readonly": False, "edit": "",
                      "secret": False})
    nkeys = len(keys_of(root))
    root["props"][nkeys:nkeys] = added


def summary(shape, can=None):
    """What the widget shows to pick properties: per collection, its properties with flags."""
    def view(c):
        return {"name": c["name"], "path": c["path"], "level": c["level"],
                "props": [{k: p.get(k) for k in ("name", "label", "key", "required", "readonly", "secret", "type", "length")}
                          | {"values": len(p.get("values") or []), "type_label": TYPES.get(p["type"], "Text"),
                             "default": default_pick(p)} for p in c["props"]],
                "collections": [view(ch) for ch in c["collections"]]}
    return {"ci": shape["ci"], "descr": shape["descr"], "can": can or {}, "root": view(shape["root"])}


def default_pick(p):
    return bool(p["key"] or (p["required"] and not p["readonly"])) and not p["secret"]


# ----- template ----------------------------------------------------------------------------------

def _header(p, parent=None):
    if parent is not None:          # a parent's key: qualified, so the upload knows which record it points to
        return f"{p['label']} ({parent}.{p['name']})"
    if p.get("key_for") == "update":            # a get key that is not a create key: needed to find the record
        return f"{p['label']} (for Update) ({p['name']})"
    return f"{p['label']}{' *' if (p['required'] and not p['readonly']) or p.get('key_for') == 'create' else ''} ({p['name']})"


def _sheet_names(shape):
    used, names = set(), {}
    for c in [shape["root"]] + [c for c, _ in collections(shape)]:
        base = re.sub(r"[\[\]:*?/\\]", "_", c["name"])[:28] or "Sheet"
        n, i = base, 2
        while n.lower() in used or n.lower() == "help":
            n, i = f"{base[:26]}_{i}", i + 1
        used.add(n.lower())
        names[c["path"]] = n
    return names


def columns(shape, picked=None):
    """-> {collection path: [column spec]}, a spec: {kind: action | parent | prop, prop, parent}"""
    picked = set(picked) if picked is not None else None

    def chosen(c, p):
        if p["secret"] or (p["readonly"] and not p["key"]):
            return False
        if p["key"]:
            return True
        return default_pick(p) if picked is None else (f"{c['path']}.{p['name']}" if c["path"] else p["name"]) in picked

    out = {"": [{"kind": "action"}] + [{"kind": "prop", "prop": p} for p in shape["root"]["props"] if chosen(shape["root"], p)]}
    for c, chain in collections(shape):
        own = [p for p in c["props"] if chosen(c, p)]
        if picked is not None and not any(not p["key"] for p in own) and not any(
                k.startswith(c["path"] + ".") or k.startswith(c["path"] + "/") for k in picked):
            continue                 # nothing picked in this collection (or below): no sheet
        cols = [{"kind": "action", "row": True}]
        for anc in chain:
            cols += [{"kind": "parent", "prop": k, "parent": anc["path"] or shape["ci"]} for k in keys_of(anc)]
        out[c["path"]] = cols + [{"kind": "prop", "prop": p} for p in own]
    return out


def template_xlsx(shape, picked=None, data=None, can=None):
    """The template (data: load records to write back, e.g. the rows to fix)."""
    can = can or {"create": True, "update": True}
    names, cols = _sheet_names(shape), columns(shape, picked)
    by_path = {"": shape["root"], **{c["path"]: c for c, _ in collections(shape)}}
    actions = [a for a, ok in (("Create", can.get("create")), ("Update", can.get("update"))) if ok] or ["Create", "Update"]
    sheets = []
    for path, spec in cols.items():
        c = by_path[path]
        if path == "":
            title = f"Component Interface {shape['ci']}: {shape['descr']}"
            note = (f"Action: {' or '.join(actions)} ({'Create adds a new one; ' if 'Create' in actions else ''}"
                    f"{'Update changes an existing one' if 'Update' in actions else ''}). * = required for Create. "
                    "An empty cell keeps the current value. Lists inside each record are on the next sheets. Upload the file to the agent.")
        else:
            parent = path.rsplit("/", 1)[0] if "/" in path else shape["ci"]
            title = f"Collection {path} of {shape['ci']}"
            note = (f"One row per {c['name']} entry; the first columns say which {parent} row it belongs to. "
                    "Row action (Update only): Change an existing row (found by its keys), Add a new row, or Delete it.")
        head = []
        for s in spec:
            if s["kind"] == "action":
                head.append("Row action" if s.get("row") else "Action")
            elif s["kind"] == "parent":
                head.append(_header(s["prop"], s["parent"]))
            else:
                head.append(_header(s["prop"]))
        rows = [[title], [note], head]
        for rec in _records_for(data, path):
            rows.append([_cell_value(rec, s) for s in spec])
        lists = []
        for i, s in enumerate(spec):
            if s["kind"] == "action":
                lists.append((i, ["Change", "Add", "Delete"] if s.get("row") else actions))
            elif s["kind"] == "prop" and 0 < len(s["prop"].get("values") or []) <= 40:
                vals = [v for v, _ in s["prop"]["values"]]
                if len(",".join(vals)) < 240:
                    lists.append((i, vals))
        sheets.append({"name": names[path], "rows": rows, "header": HEADER_ROW, "title_rows": [0],
                       "widths": [12 if s["kind"] == "action" else max(14, min(40, len(head[i]) + 2)) for i, s in enumerate(spec)],
                       "lists": lists})
    help_rows = [["Help: every property of " + shape["ci"]], ["Collection", "Property", "Label", "Required", "Key", "Type", "Length",
                                                              "Values from (prompt table)", "Fixed values", "In the template"]]
    for c in [shape["root"]] + [c for c, _ in collections(shape)]:
        in_t = {s["prop"]["name"] for s in cols.get(c["path"], []) if s["kind"] == "prop"}
        for p in c["props"]:
            help_rows.append([c["path"] or shape["ci"], p["name"], p["label"], "Yes" if p["required"] else "", "Yes" if p["key"] else "",
                              TYPES.get(p["type"], "Text"), str(p["length"]) + (f".{p['decimals']}" if p.get("decimals") else ""),
                              p["edit"] or ("set at runtime: not checked" if p.get("dynamic") else ""), "; ".join(f"{v} = {lab}" for v, lab in (p.get("values") or [])[:30]),
                              "Typed on the load card" if p["secret"] else "Yes" if p["name"] in in_t else ""])
    sheets.append({"name": "Help", "rows": help_rows, "header": 1, "title_rows": [0], "widths": [18, 24, 28, 9, 6, 13, 8, 18, 50, 16]})
    return xlsx.write(sheets)


def _records_for(data, path):
    """Rows of the collection at `path` from load records, each with its ancestors (for the parent key columns)."""
    out = []
    for rec in data or []:
        if path == "":
            out.append({"rec": rec, "chain": []})
            continue
        parts = path.split("/")

        def walk(node, depth, chain):
            for child in node.get("children", {}).get(parts[depth], []):
                if depth == len(parts) - 1:
                    out.append({"rec": child, "chain": chain + [node]})
                else:
                    walk(child, depth + 1, chain + [node])
        walk(rec, 0, [])
    return out


def _cell_value(item, s):
    rec = item["rec"]
    if s["kind"] == "action":
        return {"create": "Create", "update": "Update"}.get(rec.get("action"), "") if not s.get("row") else \
            {"change": "", "add": "Add", "delete": "Delete"}.get(rec.get("row_action"), "")
    if s["kind"] == "parent":
        for anc in item["chain"]:
            src = anc.get("raw") or anc.get("values", {})
            if s["prop"]["name"] in src:
                return src[s["prop"]["name"]]
        return ""
    return rec.get("raw", {}).get(s["prop"]["name"], rec.get("values", {}).get(s["prop"]["name"], ""))


# ----- reading an upload -------------------------------------------------------------------------

def read_upload(raw, shape):
    """The filled template -> (records, file problems). A record: {n, row, action, values, raw, children: {coll: [rec]},
    problems}; a collection row: {row, sheet, row_action, values, raw, children, problems}."""
    try:
        sheets = xlsx_sheets(raw)
    except DocError as e:
        raise CIError(str(e)) from e
    by_path = {"": shape["root"], **{c["path"]: c for c, _ in collections(shape)}}
    found, problems = {}, []
    for title, rows in sheets:
        hi = next((i for i, (_, cells) in enumerate(rows) if cells and str(cells[0]).strip().lower() in ("action", "row action")), None)
        if hi is None or title.lower() == "help":
            continue
        first = " ".join(str(x) for x in rows[0][1]).strip()
        m = re.match(r"Component Interface (\w+)", first)
        path = ""
        if m:
            if m.group(1).upper() != shape["ci"].upper():
                raise CIError(f"this file is a template for {m.group(1)}, not {shape['ci']}")
        else:
            m = re.match(r"Collection (\S+) of (\w+)", first)
            if not m:
                continue
            path = m.group(1)
            if path not in by_path:
                problems.append(f"Sheet {title}: {shape['ci']} has no collection {path}")
                continue
        found[path] = (title, rows[hi][1], rows[hi + 1:])
    if "" not in found:
        raise CIError(f"this is not a {shape['ci']} template from the agent (download the template and fill it in)")

    def spec_of(head, c):
        props = {p["name"]: p for p in c["props"]}
        spec = []
        for h in head:
            h = str(h or "").strip()
            m = re.search(r"\(([\w/]+)\.(\w+)\)\s*$", h)
            if h.lower() in ("action", "row action"):
                spec.append(("action", None))
            elif m:
                spec.append(("parent", (m.group(1), m.group(2))))
            else:
                m = re.search(r"\((\w+)\)\s*$", h)
                p = props.get(m.group(1)) if m else None
                if m and p is None:
                    problems.append(f"Column {h}: {c['path'] or shape['ci']} has no property {m.group(1)}")
                if p is not None and p["secret"]:
                    raise CIError(f"remove the column {h}: passwords are never in the file; you type them on the load card")
                spec.append(("prop", p) if p else ("skip", None))
        return spec

    records, keys_index = [], {}
    title, head, rows = found[""]
    spec = spec_of(head, shape["root"])
    root_keys = keys_of(shape["root"])
    for rownum, cells in rows:
        vals = {}
        action = ""
        for i, (kind, p) in enumerate(spec):
            v = str(cells[i]).strip() if i < len(cells) else ""
            if kind == "action":
                action = v.lower()
            elif kind == "prop" and v != "":
                vals[p["name"]] = v
        if not vals and not action:
            continue
        if len(records) >= MAX_ROWS:
            problems.append(f"Only the first {MAX_ROWS} rows are loaded at a time; split the file")
            break
        rec = {"n": len(records) + 1, "row": int(rownum), "sheet": title, "action": action, "values": vals, "raw": dict(vals),
               "children": {}, "problems": []}
        records.append(rec)
        keys_index.setdefault(("", tuple(vals.get(k["name"], "") for k in root_keys)), []).append(rec)

    for c, chain in collections(shape):          # parents before children (depth first)
        if c["path"] not in found:
            continue
        title, head, rows = found[c["path"]]
        spec = spec_of(head, c)
        parent_path = chain[-1]["path"]
        for rownum, cells in rows:
            vals, row_action, pkeys = {}, "", {}
            for i, (kind, p) in enumerate(spec):
                v = str(cells[i]).strip() if i < len(cells) else ""
                if kind == "action":
                    row_action = v.lower()
                elif kind == "parent":
                    pkeys[p] = v
                elif kind == "prop" and v != "":
                    vals[p["name"]] = v
            if not vals and not any(pkeys.values()):
                continue
            # the parent: root keys, then each ancestor collection's keys
            key = ("", tuple(pkeys.get((shape["ci"], k["name"]), "") for k in root_keys))
            parents = keys_index.get(key, [])
            for anc in chain[1:]:
                sub = tuple(pkeys.get((anc["path"], k["name"]), "") for k in keys_of(anc))
                parents = [x for p_ in parents for x in keys_index.get((anc["path"], id(p_), sub), [])]
            where = ", ".join(f"{k[1]} {v}" for k, v in pkeys.items() if v) or "no parent keys"
            if not parents:
                problems.append(f"{title} row {rownum}: no {chain[-1]['name'] if chain[-1]['level'] else shape['ci']} row with {where}")
                continue
            if row_action and row_action not in ROW_ACTIONS:
                msg = f"{title} row {rownum}: Row action must be Change, Add or Delete"
                (parents[0]["problems"] if c["level"] == 1 else problems).append(msg)
            child = {"row": int(rownum), "sheet": title, "row_action": row_action if row_action in ROW_ACTIONS else "change",
                     "values": vals, "raw": dict(vals), "children": {}, "path": c["path"], "problems": []}
            owner = parents[0]
            owner.setdefault("children", {}).setdefault(c["name"], []).append(child)
            keys_index.setdefault((c["path"], id(owner), tuple(vals.get(k["name"], "") for k in keys_of(c))), []).append(child)
    return records, problems


# ----- checks ------------------------------------------------------------------------------------

def _excel_date(v):
    try:
        return date(1899, 12, 30) + timedelta(days=int(float(v)))
    except (ValueError, OverflowError):
        return None


def normalize(p, v):
    """-> (value for the CI, problem or None)"""
    v = str(v).strip()
    t = p["type"]
    if t in (2, 3):
        try:
            d = Decimal(v.replace(",", ""))
        except InvalidOperation:
            d = None
        if d is None or not d.is_finite():
            return v, f"{p['label']} must be a number, not {v}"
        if t == 2 and d < 0:
            return v, f"{p['label']} cannot be negative"
        places = p.get("decimals", 0)
        q = d.quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP)
        if abs(q - d) > Decimal("1e-9"):     # more than Excel's float noise (0.30000000000000004)
            return v, f"{p['label']} {v} " + (f"can have at most {places} decimals" if places else "must be a whole number")
        whole = len(str(abs(int(q))))
        if p["length"] and whole > max(1, p["length"] - places):
            return v, f"{p['label']} {v} is too large"
        return format(q, "f"), None          # ValueUser() in ExcelToCI.formatData: plain digits, a point for decimals
    if t == 4:
        d = None
        if re.fullmatch(r"\d{1,5}(\.0+)?", v):
            d = _excel_date(v)
        else:
            for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y", "%d-%b-%Y", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
                try:
                    d = datetime.strptime(v, fmt).date()
                    break
                except ValueError:
                    pass
        if d is None:
            return v, f"{p['label']} {v} is not a date (use YYYY-MM-DD)"
        if d.year < 1900:
            return v, f"{p['label']} cannot be before 1900"
        return d.strftime("%Y%m%d"), None
    if t == 5:
        if re.fullmatch(r"0?\.\d+", v):
            s = round(float(v) * 86400)
            return f"{s // 3600:02d}{s % 3600 // 60:02d}{s % 60:02d}", None
        m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", v)
        if not m:
            return v, f"{p['label']} {v} is not a time (use HH:MM)"
        return f"{int(m.group(1)):02d}{m.group(2)}{m.group(3) or '00'}", None
    if t == 6:                               # ConvertDateTimeToBase(): PeopleSoft's YYYY-MM-DD-HH.MM.SS.000000 (verified on PT 8.61)
        dt = None
        if re.fullmatch(r"\d{1,5}(\.\d+)?", v):
            day = _excel_date(v)
            if day is not None:
                s = round((float(v) % 1) * 86400)
                dt = datetime(day.year, day.month, day.day) + timedelta(seconds=s)
        else:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d-%H.%M.%S.%f",
                        "%Y-%m-%d-%H.%M.%S", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M %p", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(v, fmt)
                    break
                except ValueError:
                    pass
        if dt is None:
            return v, f"{p['label']} {v} is not a date and time (use YYYY-MM-DD HH:MM)"
        if dt.year < 1900:
            return v, f"{p['label']} cannot be before 1900"
        return dt.strftime("%Y-%m-%d-%H.%M.%S.000000"), None
    if t == 0 and p.get("upper"):
        v = v.upper()                        # an Uppercase field, as typing it on the page would
    if p.get("values"):
        for code, label in p["values"]:
            if v == code or v.upper() == code.upper() or (label and v.lower() == label.lower()):
                return code, None
        return v, f"{p['label']} {v} is not one of: " + ", ".join(f"{c} ({l})" if l else c for c, l in p["values"][:12])
    if p["length"] and t == 0 and len(v) > p["length"]:
        return v, f"{p['label']} is longer than {p['length']} characters"
    return v, None


def check(records, shape, db=None, who="", can=None):
    """Check every record; problems go on the record (a record with problems is not loaded). -> counts"""
    can = can or {"create": True, "update": True}
    root = shape["root"]
    by_name = {c["name"]: c for c, _ in collections(shape)}
    seen = {}

    def row_checks(rec, c, creating, where):
        for name in list(rec["values"]):
            p = next((q for q in c["props"] if q["name"] == name), None)
            if p is None:
                continue
            val, err = normalize(p, rec["values"][name])
            if err:
                rec_root["problems"].append(f"{where}{err}")
            else:
                rec["values"][name] = val
        if c is root:                    # level 0: the keys SOAPTOCI sets for this action, read-only or not
            for p in action_keys(shape, "create" if creating else "update"):
                if not rec["values"].get(p["name"]):
                    rec_root["problems"].append(f"{where}{p['label']} is required" + ("" if creating else " to find the record"))
        for p in c["props"]:
            if p["secret"] or p["readonly"]:
                continue
            if p["key"] and c is not root and not rec["values"].get(p["name"]):
                rec_root["problems"].append(f"{where}{p['label']} is required")
            elif creating and p["required"] and not p["key"] and not rec["values"].get(p["name"]):
                rec_root["problems"].append(f"{where}{p['label']} is required")
        for cname, kids in rec.get("children", {}).items():
            cc = sub_collection(c, cname) or by_name[cname]
            for k in kids:
                row_checks(k, cc, creating or k.get("row_action") == "add", f"{k['sheet']} row {k['row']}: ")

    for rec in records:
        rec_root = rec
        a = rec["action"]
        if a not in ("create", "update"):
            rec["problems"].append("Action must be Create or Update" if a else "Action is empty (Create or Update)")
        elif not can.get(a):
            rec["problems"].append(f"you may not {a} through {shape['ci']} (your CI permissions)")
        row_checks(rec, root, a == "create", "")
        key = (a, tuple(rec["values"].get(k["name"], "") for k in keys_of(root)))
        if key in seen and any(key[1]):
            rec["problems"].append(f"the same key as row {seen[key]}")
        seen.setdefault(key, rec["row"])
    if db is not None:
        _check_prompts(records, shape, db, who)
        _check_exists(records, shape, db, who)
    return counts(records)


def counts(records):
    bad = sum(1 for r in records if r["problems"])
    return {"rows": len(records), "ready": len(records) - bad, "problems": bad}


def _all_rows(records, shape):
    """(record, collection, row) for every row of every record."""
    def walk(rec, c, top):
        yield top, c, rec
        for cname, kids in rec.get("children", {}).items():
            for k in kids:
                yield from walk(k, sub_collection(c, cname), top)
    for r in records:
        yield from walk(r, shape["root"], r)


def prompt_values_found(db, wanted, who, tag):
    """wanted: {(prompt table, field): values} -> {(prompt table, field): the values found there}, without the ones that
    cannot be checked (dynamic prompts, derived records, keys in a subrecord, masked columns). Read-only queries."""
    recs = sorted({e for e, _ in wanted})
    if not recs:
        return {}
    try:
        binds = {f"r{i}": r for i, r in enumerate(recs)}
        _, rows, _ = db.query("SELECT RECNAME, SQLTABLENAME, RECTYPE FROM PSRECDEFN WHERE RECNAME IN ("
                              + ", ".join(":" + k for k in binds) + ")", binds, max_rows=500, who=who, why=f"{tag} prompt tables")
        tables = {r[0]: _table(r[0], r[1]) for r in rows if int(r[2] or 0) in (0, 1, 6)}
        _, rows, _ = db.query("SELECT RECNAME, FIELDNAME, FIELDNUM, USEEDIT FROM PSRECFIELD WHERE RECNAME IN ("
                              + ", ".join(":" + k for k in binds) + ") ORDER BY RECNAME, FIELDNUM",
                              binds, max_rows=5000, who=who, why=f"{tag} prompt fields")
        pkeys = {}
        for rec, f, _num, use in rows:
            if int(use or 0) & 1:
                pkeys.setdefault(rec, []).append(f)
    except QueryError as e:
        log.warning("%s prompt check skipped: %s", tag, e)
        return {}
    out = {}
    for (edit, fld), values in wanted.items():
        keys = pkeys.get(edit, [])
        # PeopleSoft matches the value to the prompt table's key of the same name, else its last key
        col = fld if fld in keys else next((k for k in reversed(keys) if k not in ("EFFDT", "EFFSEQ")), None)
        if edit not in tables or not col:
            continue
        values = sorted(values)
        ok = set()
        try:
            for i in range(0, len(values), 200):
                chunk = {f"v{j}": v for j, v in enumerate(values[i:i + 200])}
                _, rows, _ = db.query(f"SELECT DISTINCT {col} FROM {tables[edit]} WHERE {col} IN ("
                                      + ", ".join(":" + k for k in chunk) + ")", chunk, max_rows=1000, who=who,
                                      why=f"{tag} prompt {edit}.{col}")
                ok |= {str(r[0]) for r in rows}
        except QueryError as e:
            log.warning("%s prompt check of %s.%s skipped: %s", tag, edit, col, e)
            continue
        if "***" not in ok:
            out[(edit, fld)] = ok
    return out


def _check_prompts(records, shape, db, who):
    """Values of prompted properties must exist in their prompt table (one query per property)."""
    want = {}
    for top, c, row in _all_rows(records, shape):
        for p in c["props"]:
            v = row["values"].get(p["name"])
            if v and p["edit"] and p.get("field") and not p.get("values") and row.get("row_action") != "delete":
                want.setdefault((p["edit"], p["field"], p["label"]), []).append((top, v))
    if not want:
        return
    found = prompt_values_found(db, {(e, f): {v for _, v in items} for (e, f, _), items in want.items()}, who, "ci")
    for (edit, fld, label), items in want.items():
        ok = found.get((edit, fld))
        if ok is None:
            continue
        for top, v in items:
            if v not in ok and v.upper() not in ok:
                msg = (f"{label}: {v} is the name of the prompt table, not a value; type a {label} from it" if v.upper() == edit
                       else f"{label} {v} does not exist (prompt table {edit})")
                if msg not in top["problems"]:
                    top["problems"].append(msg)


def _check_exists(records, shape, db, who):
    """Create: the key must not exist yet; Update: it must (each with its own keys)."""
    for action in ("create", "update"):
        mine = [r for r in records if r["action"] == action]
        if mine:
            _check_exists_by(mine, action_keys(shape, action), db, who)


def _check_exists_by(records, keys, db, who):
    if not keys or any(not k.get("field") for k in keys):
        return
    table = None
    for attr in ("record", "search_record"):          # the CI's level 0 table, else its search record (if not dynamic)
        recs = {k.get(attr) for k in keys}
        if len(recs) != 1 or not next(iter(recs)):
            continue
        rec = next(iter(recs))
        try:
            _, rows, _ = db.query("SELECT SQLTABLENAME, RECTYPE FROM PSRECDEFN WHERE RECNAME = :r", {"r": rec}, max_rows=1, who=who,
                                  why="ci key record")
        except QueryError:
            return
        if rows and int(rows[0][1] or 0) in (0, 1):
            table = _table(rec, rows[0][0])
            break
    if table is None:
        return
    fields = [k["field"] for k in keys]
    todo = [r for r in records if r["action"] in ("create", "update") and all(r["values"].get(k["name"]) for k in keys)]
    tuples = sorted({tuple(r["values"][k["name"]] for k in keys) for r in todo})
    found = set()
    try:
        for i in range(0, len(tuples), 100):
            chunk = tuples[i:i + 100]
            binds = {}
            if len(fields) == 1:
                for j, t in enumerate(chunk):
                    binds[f"k{j}"] = t[0]
                cond = f"{fields[0]} IN (" + ", ".join(":" + k for k in binds) + ")"
            else:
                parts = []
                for j, t in enumerate(chunk):
                    parts.append("(" + ", ".join(f":k{j}_{x}" for x in range(len(fields))) + ")")
                    binds.update({f"k{j}_{x}": v for x, v in enumerate(t)})
                cond = "(" + ", ".join(fields) + ") IN (" + ", ".join(parts) + ")"
            _, rows, _ = db.query(f"SELECT DISTINCT {', '.join(fields)} FROM {table} WHERE {cond}", binds, max_rows=1000, who=who,
                                  why=f"ci key exists {rec}")
            found |= {tuple(str(x) for x in r) for r in rows}
    except QueryError as e:
        log.warning("CI key check skipped: %s", e)
        return
    if any("***" in t for t in found):
        return
    names = ", ".join(k["label"] for k in keys)
    for r in todo:
        t = tuple(r["values"][k["name"]] for k in keys)
        there = t in found or tuple(x.upper() for x in t) in found
        if r["action"] == "create" and there:
            r["problems"].append(f"{names} {' / '.join(t)} already exists: use Update to change it")
        elif r["action"] == "update" and not there:
            r["problems"].append(f"{names} {' / '.join(t)} does not exist: use Create to add it")


def label_of(rec, shape):
    return " / ".join(v for v in (rec["values"].get(k["name"], "") for k in keys_of(shape["root"])) if v) or f"row {rec['row']}"


# ----- the SOAP request and answer ---------------------------------------------------------------

def envelope(shape, rec):
    """One record as SOAPTOCI's request. Password properties become {{secret:NAME}} for the browser to fill."""
    creating = rec["action"] == "create"
    mine = {p["name"] for p in action_keys(shape, rec["action"])}

    def props_xml(c, row, secrets):
        out = []
        for p in c["props"]:
            if c is shape["root"] and p["key"] and not p.get("is_prop", True) and p["name"] not in mine:
                continue        # a key of the other action only: SOAPTOCI would answer "Property ... was not found"
            if p["secret"] and secrets:
                out.append(f"<{p['name']}>{{{{secret:{p['name']}}}}}</{p['name']}>")
                continue
            v = row["values"].get(p["name"])
            if v not in (None, ""):
                out.append(f"<{p['name']}>{escape(str(v))}</{p['name']}>")
        return "".join(out)

    def kids_xml(row, parent):
        out = []
        for cname, kids in row.get("children", {}).items():     # a collection's rows together: SOAPTOCI fills its first row
            c = sub_collection(parent, cname)                    # (the dummy row) and inserts the rest
            for k in kids:
                attr = "" if creating else (f" CINodeAction={quoteattr(ROW_ACTIONS[k['row_action']])}" if ROW_ACTIONS.get(k["row_action"]) else "")
                out.append(f"<{cname}{attr}>{props_xml(c, k, False)}{kids_xml(k, c)}</{cname}>")
        return "".join(out)

    method = ("Create" if creating else "Updatedata") + "__CompIntfc__" + shape["ci"]
    body = props_xml(shape["root"], rec, creating) + kids_xml(rec, shape["root"])
    return ('<?xml version="1.0"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
            f"<SOAP-ENV:Body><{method}>{body}</{method}></SOAP-ENV:Body></SOAP-ENV:Envelope>")


def shape_request(ci):
    return ('<?xml version="1.0"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">'
            f"<SOAP-ENV:Body><GetCIShape__CompIntfc__{ci}/></SOAP-ENV:Body></SOAP-ENV:Envelope>")


def secrets_of(shape, records):
    if not any(r["action"] == "create" for r in records if not r["problems"]):
        return []
    return [{"name": p["name"], "label": p["label"]} for p in shape["root"]["props"] if p["secret"]]


def parse_result(text):
    """SOAPTOCI's answer for one row -> {status: ok | warning | error, messages: [...], detail}"""
    t = str(text or "")
    if "<" not in t[:200] or "Envelope" not in t:
        return {"status": "error", "messages": [], "detail": not_xml_reason(t)}
    try:
        root = ET.fromstring(t.encode("utf-8"))
    except ET.ParseError:
        return {"status": "error", "messages": [], "detail": not_xml_reason(t)}
    saved = any(_local(e.tag) == "notification" and (e.text or "").strip() == "1" for e in root.iter())
    msgs = messages_of(root)
    errors = [m for m in msgs if m["type"].lower() in ("error", "cancel")]
    warnings = [m for m in msgs if m["type"].lower() == "warning"]
    if saved and not errors:
        return {"status": "warning" if warnings else "ok", "messages": msgs,
                "detail": "; ".join(m["text"] for m in warnings) or "Saved"}
    fault = next((e.text for e in root.iter() if _local(e.tag) == "faultstring" and e.text), "")
    detail = "; ".join(m["text"] for m in errors or msgs) or fault or "PeopleSoft did not save the row and gave no message"
    return {"status": "error", "messages": msgs, "detail": detail}


# ----- loads -------------------------------------------------------------------------------------

class Store:
    """Shapes and loads, kept on disk so a load survives a restart (PSAGENT_CI_DIR, default agent-service/ci_loads)."""

    def __init__(self, directory):
        self.dir = Path(directory)
        self.shapes, self.loads = {}, {}
        self._lock = threading.Lock()
        for f in sorted((self.dir / "loads").glob("*.json")) if (self.dir / "loads").is_dir() else []:
            try:
                ld = json.loads(f.read_text(encoding="utf-8"))
                if ld.get("status") == "running":
                    ld["status"] = "paused"          # the browser that ran it is gone: resume from the panel
                self.loads[ld["load_id"]] = ld
            except (OSError, ValueError, KeyError) as e:
                log.warning("CI load %s not read: %s", f.name, e)
        for f in sorted((self.dir / "shapes").glob("*.json")) if (self.dir / "shapes").is_dir() else []:
            try:
                sh = json.loads(f.read_text(encoding="utf-8"))
                if sh.get("v", 1) < SHAPE_VERSION:
                    continue                         # read again from PeopleSoft the next time the CI is picked
                self.shapes[sh["ci"]] = sh
            except (OSError, ValueError, KeyError):
                pass

    def put_shape(self, shape):
        with self._lock:
            self.shapes[shape["ci"]] = shape
            self._write("shapes", shape["ci"], shape)

    def save(self, ld):
        with self._lock:
            self.loads[ld["load_id"]] = ld
            self._write("loads", ld["load_id"], ld)

    def _write(self, kind, name, obj):
        try:
            d = self.dir / kind
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / f"{name}.json.tmp"
            tmp.write_text(json.dumps(obj, default=str), encoding="utf-8")
            tmp.replace(d / f"{name}.json")
        except OSError as e:
            log.warning("CI %s %s not saved: %s", kind, name, e)

    def mine(self, oprid, limit=10):
        return sorted((x for x in self.loads.values() if x["owner"] == oprid), key=lambda x: x["created"], reverse=True)[:limit]


def new_load(store, shape, records, problems, owner, name, can):
    c = counts(records)
    ld = {"load_id": "ci-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4], "ci": shape["ci"],
          "descr": shape["descr"], "owner": owner, "file": name, "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
          "status": "checked", "records": records, "file_problems": problems, "results": {}, "counts": c, "can": can,
          "started": None, "ended": None}
    store.save(ld)
    audit.record("ci.checked", f"{shape['ci']}: {name}: {c['rows']} rows, {c['ready']} ready, {c['problems']} with problems",
                 ref=ld["load_id"], feature="ci")
    return ld


def view(ld, shape, limit=200):
    """What the review and progress cards show."""
    res = ld["results"]
    done = {"ok": 0, "warning": 0, "error": 0}
    for r in res.values():
        done[r["status"]] = done.get(r["status"], 0) + 1
    rows = []
    for rec in ld["records"][:limit]:
        r = res.get(str(rec["n"]))
        rows.append({"n": rec["n"], "row": rec["row"], "action": rec["action"].capitalize(), "label": label_of(rec, shape),
                     "children": sum(len(v) for v in rec.get("children", {}).values()), "problems": rec["problems"],
                     "status": r["status"] if r else ("problem" if rec["problems"] else "ready"), "detail": r["detail"] if r else ""})
    ready = [r for r in ld["records"] if not r["problems"]]
    return {"load_id": ld["load_id"], "ci": ld["ci"], "descr": ld["descr"], "file": ld["file"], "status": ld["status"],
            "counts": ld["counts"], "file_problems": ld["file_problems"], "rows": rows, "more_rows": max(0, len(ld["records"]) - limit),
            "done": done, "total": len(ready), "processed": sum(done.values()), "created": ld["created"],
            "started": ld["started"], "ended": ld["ended"], "secrets": secrets_of(shape, ld["records"])}


def next_row(ld, shape):
    """The next ready record not yet sent -> {n, label, action, xml} or None."""
    for rec in ld["records"]:
        if not rec["problems"] and str(rec["n"]) not in ld["results"]:
            return {"n": rec["n"], "label": label_of(rec, shape), "action": rec["action"], "xml": envelope(shape, rec)}
    return None


def record_result(store, ld, shape, n, text):
    rec = next((r for r in ld["records"] if r["n"] == n), None)
    if rec is None or rec["problems"]:
        return None
    res = parse_result(text)
    res["at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ld["results"][str(n)] = res
    audit.record("ci.row", f"{shape['ci']} {rec['action'].capitalize()} {label_of(rec, shape)}: {res['detail']}"[:500],
                 {"ok": "ok", "warning": "ok"}.get(res["status"], "failed"), ld["load_id"],
                 {"row": rec["row"], "values": [{"label": k, "value": v} for k, v in list(rec["values"].items())[:12]],
                  "messages": res["messages"][:5]}, feature="ci")
    if next_row(ld, shape) is None:
        finish(store, ld, shape, "done")
    else:
        store.save(ld)
    return res


def finish(store, ld, shape, status):
    ld["status"], ld["ended"] = status, datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    v = view(ld, shape, 0)
    d = v["done"]
    audit.record("ci.load", f"{shape['ci']}: {ld['file']}: {d['ok']} saved, {d['warning']} with warnings, {d['error']} failed"
                 + ("" if status == "done" else f" ({status} after {v['processed']} of {v['total']})"),
                 "failed" if d["error"] else "ok", ld["load_id"], feature="ci")
    store.save(ld)


def results_xlsx(ld, shape):
    rows = [[f"Results of {ld['file']}: {ld['ci']}, loaded by {ld['owner']} on {ld['started'] or ld['created']}"],
            ["Row", "Action", "Key", "Lines", "Status", "Message"]]
    for rec in ld["records"]:
        r = ld["results"].get(str(rec["n"]))
        status = {"ok": "Saved", "warning": "Saved with warnings", "error": "Failed"}.get(r["status"]) if r else (
            "Not loaded: " + "; ".join(rec["problems"]) if rec["problems"] else "Not loaded (stopped)")
        rows.append([str(rec["row"]), rec["action"].capitalize(), label_of(rec, shape),
                     str(sum(len(v) for v in rec.get("children", {}).values()) or ""), status.split(":")[0],
                     (r["detail"] if r else "; ".join(rec["problems"]))])
    return xlsx.write([{"name": "Results", "rows": rows, "header": 1, "title_rows": [0], "widths": [7, 9, 24, 7, 20, 90]}])


def fix_xlsx(ld, shape):
    """The template again, with only the rows that were not saved (problems or errors), to fix and upload."""
    todo = [r for r in ld["records"] if r["problems"] or (ld["results"].get(str(r["n"])) or {}).get("status") == "error"]
    picked = set()
    for top, c, row in _all_rows(todo, shape):
        for name in row.get("raw", {}):
            picked.add(f"{c['path']}.{name}" if c["path"] else name)
    return template_xlsx(shape, picked or None, todo, ld.get("can")), len(todo)
