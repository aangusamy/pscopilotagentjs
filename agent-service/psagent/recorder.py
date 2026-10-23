"""Turn a recorded transaction (a human doing the task once in the workspace)
into a reusable task template.

  recording ──candidates()──▶ values the human typed
            ──draft()───────▶ suggested name/mode per value (Claude, or rules)
            ──build()───────▶ task dict (same shape as tasks/*.yaml), validated

Modes for each recorded value:
  required  ask every time
  optional  ask once; steps that only exist for it are skipped when empty
  default   ask, but offer the recorded value as the default
  fixed     part of the procedure; always the recorded value
  secret    password-type field, typed on the confirm card
"""

import json
import logging
import re
from datetime import date

import yaml

from .llm import LLMError
from .tasks import parse_task

log = logging.getLogger("psagent")

MODES = ("required", "optional", "default", "fixed", "secret")
_GRID_ID = re.compile(r"^(.+)\$(\d+)$")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
TASK_ID = re.compile(r"^[a-z][a-z0-9_]{2,59}$")
MAX_STEPS = 300


class RecordingError(ValueError):
    pass


def snake(text, fallback="field"):
    s = re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")
    if not s or not s[0].isalpha():
        s = f"{fallback}_{s}" if s else fallback
    return s[:40].rstrip("_")


def _str(v, limit=500):
    return str(v if v is not None else "")[:limit]


def validate(rec):
    """Check the shape of a recording sent by the widget."""
    if not isinstance(rec, dict):
        raise RecordingError("recording must be an object")
    comp = rec.get("component") or {}
    if not all(re.fullmatch(r"[A-Z0-9_]{1,30}", _str(comp.get(k)).upper()) for k in ("menu", "component")):
        raise RecordingError("recording.component needs menu and component")
    steps = rec.get("steps")
    if not isinstance(steps, list) or not steps or len(steps) > MAX_STEPS:
        raise RecordingError(f"recording needs 1-{MAX_STEPS} steps")
    for st in steps:
        if not isinstance(st, dict) or st.get("do") not in ("set", "click", "tab"):
            raise RecordingError("recorded steps must be set, click or tab")
        if st["do"] == "set" and not (st.get("field") or {}).get("id"):
            raise RecordingError("recorded set steps need a field id")
    if not any(st["do"] == "set" for st in steps):
        raise RecordingError("nothing was typed during the recording")


def _secret_label(label):
    return re.sub(r"^(confirm|re-?enter|repeat|verify|new)\s+", "", label.strip(), flags=re.I) or "Password"


def candidates(rec):
    """Values the human entered, in order. Grids with 2+ recorded rows become one list value."""
    steps = rec["steps"]
    rows = {}
    for st in steps:
        if st["do"] == "set" and st.get("kind") != "password":
            m = _GRID_ID.match(st["field"]["id"])
            if m:
                rows.setdefault(m.group(1), set()).add(int(m.group(2)))
    grids = {base for base, r in rows.items() if len(r) >= 2}

    out, by_key = [], {}
    for st in steps:
        if st["do"] != "set":
            continue
        f = st["field"]
        kind = st.get("kind") or "text"
        label = _str(f.get("label") or f.get("column") or f["id"], 80).strip()
        if kind == "password":
            label = _secret_label(label)
            key = "secret:" + snake(label, "secret")
        else:
            m = _GRID_ID.match(f["id"])
            key = m.group(1) if m and m.group(1) in grids else f["id"]
        c = by_key.get(key)
        if c is None:
            c = {"key": key, "label": label, "kind": "secret" if kind == "password" else kind, "value": ""}
            if key in grids:
                c.update(kind="list", label=_str(f.get("column") or f.get("label") or key, 80), value=[], _rows={})
            by_key[key] = c
            out.append(c)
        if c["kind"] == "list":
            c["_rows"][int(_GRID_ID.match(f["id"]).group(2))] = _str(st.get("value"))
            c["value"] = [v for _, v in sorted(c["_rows"].items()) if v]
        elif c["kind"] != "secret":
            c["value"] = _str(st.get("value"))
    for c in out:
        c.pop("_rows", None)
    return out


# ----- draft: suggest names and modes -----------------------------------------

DRAFT_SYSTEM = """\
You turn a recorded PeopleSoft transaction into a reusable task template. A \
person did the task once by hand; you get the component and every value they \
entered, in order. Decide, for each value, how the agent should treat it next time:

- required: identifies or describes the specific thing being done (IDs, names, \
descriptions, amounts, dates, quantities, people, vendors, items). Ask every time.
- optional: often blank or situational; only filled sometimes.
- default: usually the same, but someone may need to change it (business unit, \
currency, symbolic ID, location). The recorded value becomes the default.
- fixed: part of the procedure itself, always the same (a type code that makes \
the following fields appear, a checkbox the process always ticks).

Give each value a short snake_case name (e.g. user_id, vendor_id, invoice_date), \
keep the label readable, and add a one-line hint when the format matters. Also \
name the task (snake_case id, short title starting with a verb), write a \
one-sentence summary, and two example requests a user might type.\
"""


def _draft_schema(keys):
    return {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "examples": {"type": "array", "items": {"type": "string"}},
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "enum": keys},
                        "name": {"type": "string"},
                        "label": {"type": "string"},
                        "mode": {"type": "string", "enum": ["required", "optional", "default", "fixed"]},
                        "hint": {"type": "string"},
                    },
                    "required": ["key", "name", "label", "mode", "hint"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["task_id", "title", "summary", "examples", "fields"],
        "additionalProperties": False,
    }


def _rule_mode(c):
    if c["kind"] == "secret":
        return "secret"
    if c["kind"] == "list":
        return "optional"
    if c["kind"] in ("select", "checkbox"):
        return "default"
    return "required"


def draft(rec, llm=None):
    """Suggested template: names, labels and modes for each recorded value."""
    validate(rec)
    cands = candidates(rec)
    comp = rec["component"]
    title = _str(rec.get("title"), 80).strip() or f"{comp['component']} task"
    d = {
        "task_id": snake(title, "task"),
        "title": title,
        "summary": f"Recorded on {comp['menu']}.{comp['component']}.",
        "examples": [title.lower()],
        "fields": [
            {"key": c["key"], "name": snake(c["label"]) if c["kind"] != "secret" else c["key"].split(":", 1)[1],
             "label": c["label"], "kind": c["kind"], "value": c["value"], "mode": _rule_mode(c), "hint": ""}
            for c in cands
        ],
        "suggested_by": "rules",
    }
    plain = [c for c in cands if c["kind"] != "secret"]
    if llm is None or not plain:
        return d
    prompt = json.dumps({
        "title_given_by_user": rec.get("title") or "",
        "component": f"{comp['menu']}.{comp['component']}.{comp.get('market') or 'GBL'}",
        "mode": "add" if (rec.get("query") or {}).get("Action") == "A" else "update/other",
        "steps": [_step_text(st) for st in rec["steps"]],
        "values": [{"key": c["key"], "label": c["label"], "kind": c["kind"], "recorded_value": c["value"]} for c in plain],
    }, indent=1)
    try:
        out = llm.structured(DRAFT_SYSTEM, prompt, _draft_schema([c["key"] for c in plain]))
    except LLMError as e:
        log.warning("draft suggestion failed, using rules: %s", e)
        return d
    by_key = {f["key"]: f for f in d["fields"]}
    for s in out.get("fields") or []:
        f = by_key.get(s.get("key"))
        if f and f["kind"] != "secret" and s.get("mode") in MODES:
            f.update(name=snake(s.get("name") or f["name"]), label=_str(s.get("label"), 80).strip() or f["label"],
                     mode=s["mode"], hint=_str(s.get("hint"), 200))
    if TASK_ID.fullmatch(snake(out.get("task_id") or "", "task")):
        d["task_id"] = snake(out["task_id"], "task")
    d["title"] = _str(out.get("title"), 80).strip() or d["title"]
    d["summary"] = _str(out.get("summary"), 300).strip() or d["summary"]
    d["examples"] = [_str(e, 200) for e in (out.get("examples") or [])[:3] if str(e).strip()] or d["examples"]
    d["suggested_by"] = "claude"
    return d


def _step_text(st):
    if st["do"] == "set":
        f = st["field"]
        return f"set {f.get('label') or f.get('column') or f['id']}"
    if st["do"] == "tab":
        return f"open tab {st.get('label')}"
    b = st.get("button") or {}
    return f"click {b.get('text') or b.get('id')}"


# ----- build: reviewed draft -> task -------------------------------------------

def _upper(values):
    vals = [v for v in (values if isinstance(values, list) else [values]) if v]
    return bool(vals) and all(v == v.upper() and re.search(r"[A-Z]", v) for v in vals)


def build(rec, reviewed, author):
    """Final task dict from the recording plus the user's reviewed draft."""
    validate(rec)
    cands = {c["key"]: c for c in candidates(rec)}
    choice = {f.get("key"): f for f in reviewed.get("fields") or [] if isinstance(f, dict)}
    task_id = _str(reviewed.get("task_id"), 80)
    if not TASK_ID.fullmatch(task_id):
        raise RecordingError("task id must be 3-60 lowercase letters, digits or underscores")
    title = _str(reviewed.get("title"), 80).strip()
    if not title:
        raise RecordingError("the task needs a title")

    fields, names = {}, {}   # name -> spec ; candidate key -> name (None when fixed)
    for key, c in cands.items():
        ch = choice.get(key) or {}
        mode = "secret" if c["kind"] == "secret" else ch.get("mode", _rule_mode(c))
        if mode not in MODES or mode == "secret" and c["kind"] != "secret":
            raise RecordingError(f"invalid mode {mode!r} for {c['label']}")
        if mode == "fixed":
            names[key] = None
            continue
        name = snake(ch.get("name") or (key.split(":", 1)[1] if mode == "secret" else c["label"]))
        if not _NAME.fullmatch(name):
            raise RecordingError(f"invalid field name {name!r}")
        base, n = name, 2
        while name in fields:
            name, n = f"{base}_{n}", n + 1
        spec = {"label": _str(ch.get("label"), 80).strip() or c["label"]}
        if mode == "secret":
            spec.update(required=True, sensitive=True)
        else:
            if mode in ("required", "default"):
                spec["required"] = True
            if c["kind"] == "list":
                spec["type"] = "list"
            if mode == "default" and c["value"]:
                spec["default"] = ", ".join(c["value"]) if c["kind"] == "list" else c["value"]
            if c["kind"] in ("text", "list") and _upper(c["value"]):
                spec["upper"] = True
            hint = _str(ch.get("hint"), 200).strip()
            if hint:
                spec["hint"] = hint
        fields[name] = spec
        names[key] = name

    comp = {k: _str(rec["component"].get(k)).upper() for k in ("menu", "component")}
    comp["market"] = _str(rec["component"].get("market") or "GBL").upper()
    query = {str(k)[:30]: _str(v, 100) for k, v in (rec.get("query") or {}).items()}
    playbook = [{"do": "navigate", "query": query, "say": f"Opening {title}"}]
    seen_grids = set()
    entered = {}  # field id -> value already entered earlier in the recording
    for st in rec["steps"]:
        if st["do"] == "tab":
            playbook.append({"do": "tab", "label": _str(st.get("label"), 80)})
        elif st["do"] == "click":
            b = st.get("button") or {}
            btn = {k: _str(b[k], 80) for k in ("id", "text") if b.get(k)}
            if btn:
                playbook.append({"do": "click", "button": btn})
        else:
            f = st["field"]
            raw = _str(st.get("value"))
            if entered.get(f["id"]) == raw:
                # The same value again is not a new action (PeopleSoft re-sets e.g. the search
                # key after Add, where the field no longer exists); replaying it would fail.
                continue
            entered[f["id"]] = raw
            c = cands[_key_of(st, cands)]
            if c["kind"] == "list":
                if c["key"] in seen_grids:
                    continue
                seen_grids.add(c["key"])
                spec = {"id": c["key"]}
                if f.get("column") or f.get("label"):
                    spec["column"] = _str(f.get("column") or f.get("label"), 80)
                name = names[c["key"]]
                playbook.append({"do": "grid", "field": spec, "values": f"{{{{{name}}}}}" if name else list(c["value"]),
                                 "_var": name})
                continue
            spec = {"id": _str(f["id"], 100)}
            if f.get("label"):
                spec["label"] = _str(f["label"], 80)
            if f.get("column"):
                spec["column"] = _str(f["column"], 80)
            name = names[c["key"]]
            if c["kind"] == "secret":
                value = f"{{{{secret:{name}}}}}"
            elif name:
                value = f"{{{{{name}}}}}"
            else:
                value = c["value"]
            playbook.append({"do": "set", "field": spec, "value": value,
                             "_var": None if c["kind"] == "secret" else name, "_fixed": not name})
    last_click = next((s for s in reversed(playbook) if s["do"] == "click"), None)
    if last_click and (last_click["button"].get("id") == "#ICSave" or norm(last_click["button"].get("text")) == "save"):
        playbook.append({"do": "expect_saved"})
    _add_conditions(playbook, fields)

    task = {
        "id": task_id,
        "title": title,
        "summary": _str(reviewed.get("summary"), 300).strip() or f"Recorded on {comp['menu']}.{comp['component']}.",
        "examples": [_str(e, 200) for e in (reviewed.get("examples") or [])[:3] if str(e).strip()],
        "component": comp,
        "recorded": {"by": author, "on": date.today().isoformat()},
        "fields": fields,
        "playbook": playbook,
    }
    parse_task(task, source=f"recording {task_id}")
    return task


def norm(t):
    return re.sub(r"\s+", " ", str(t or "")).strip().lower()


def _key_of(st, cands):
    f = st["field"]
    if st.get("kind") == "password":
        return "secret:" + snake(_secret_label(_str(f.get("label") or f.get("column") or f["id"], 80)), "secret")
    m = _GRID_ID.match(f["id"])
    return m.group(1) if m and m.group(1) in cands and cands[m.group(1)]["kind"] == "list" else f["id"]


def _add_conditions(playbook, fields):
    """Skip steps that only exist for optional values the user left empty.

    A tab section whose asked values are all optional runs only if any is filled
    (so a recorded "ID tab -> ID Type = Employee -> EMPLID" disappears without an
    EMPLID). Elsewhere each optional value's own step gets `when: <name>`.
    """
    optional = {n for n, f in fields.items() if not f.get("required")}
    sections, cur = [], []
    for st in playbook:
        if st["do"] == "tab" or st["do"] == "expect_saved" or (st["do"] == "click" and st["button"].get("id") == "#ICSave"):
            if cur:
                sections.append(cur)
            cur = []
        cur.append(st)
    sections.append(cur)
    for sec in sections:
        names = [st.get("_var") for st in sec if st.get("_var")]
        if sec[0]["do"] == "tab" and names and all(n in optional for n in names):
            cond = sorted(set(names))
            for st in sec:
                st["when"] = cond[0] if len(cond) == 1 else cond
        else:
            for i, st in enumerate(sec):
                if st.get("_var") in optional:
                    st["when"] = st["_var"]
                elif st.get("_fixed"):
                    # A fixed value that sets up the next asked value (ID Type = Employee
                    # before the EMPLID) is only needed when that value is given.
                    nxt = next((x["_var"] for x in sec[i + 1:] if x.get("_var")), None)
                    if nxt in optional:
                        st["when"] = nxt
    for st in playbook:
        st.pop("_var", None)
        st.pop("_fixed", None)


def to_yaml(task):
    rec = task.get("recorded", {})
    head = (f"# Recorded with PSAgent teach mode by {rec.get('by')} on {rec.get('on')}.\n"
            "# Edit freely: same format as the hand-written tasks (see README).\n")
    return head + yaml.safe_dump(task, sort_keys=False, allow_unicode=True, width=120)
