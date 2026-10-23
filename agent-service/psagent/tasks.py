"""Task catalog: field definitions + screen playbooks loaded from tasks/*.yaml.

A task is what a human does today on a PeopleSoft component: the values they
need (fields) and the clicks/keystrokes they make (playbook). The agent fills
the fields through conversation; the browser widget replays the playbook.
"""

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_TEMPLATE = re.compile(r"\{\{\s*([a-z0-9_:]+)\s*\}\}")
_STEP_TYPES = {"navigate", "set", "click", "wait", "tab", "grid", "expect_saved"}


class TaskError(ValueError):
    pass


@dataclass
class Field:
    name: str
    label: str
    required: bool = False
    type: str = "text"  # text | list
    sensitive: bool = False
    default: str | None = None
    upper: bool = False
    max_length: int | None = None
    pattern: str | None = None
    hint: str = ""
    choices: list[str] = field(default_factory=list)

    def normalize(self, raw):
        """Return (value, error). Empty input clears the field."""
        if self.type == "list":
            items = raw if isinstance(raw, list) else str(raw).split(",")
            items = [str(i).strip() for i in items if str(i).strip()]
            if self.upper:
                items = [i.upper() for i in items]
            for item in items:
                err = self._check(item)
                if err:
                    return None, f"{item!r}: {err}"
            return items, None
        value = str(raw).strip()
        if self.upper:
            value = value.upper()
        if not value:
            return "", None
        return value, self._check(value)

    def _check(self, value):
        if self.max_length and len(value) > self.max_length:
            return f"{self.label} is at most {self.max_length} characters"
        if self.pattern and not re.fullmatch(self.pattern, value):
            return f"{self.label} has invalid characters ({self.hint or self.pattern})"
        if self.choices and value not in self.choices:
            return f"{self.label} must be one of: {', '.join(self.choices)}"
        return None


@dataclass
class Task:
    id: str
    title: str
    summary: str
    component: dict
    fields: dict[str, Field]
    playbook: list[dict]
    examples: list[str] = field(default_factory=list)

    # ----- field state -------------------------------------------------
    def effective(self, values):
        out = {n: f.default for n, f in self.fields.items() if f.default and not f.sensitive}
        out.update({k: v for k, v in values.items() if v not in ("", [], None)})
        return out

    def missing(self, values):
        """Required fields the agent still has to collect (secrets excluded)."""
        eff = self.effective(values)
        return [n for n, f in self.fields.items() if f.required and not f.sensitive and not eff.get(n)]

    def secrets(self):
        return [{"name": n, "label": f.label} for n, f in self.fields.items() if f.sensitive]

    def summary_rows(self, values):
        eff = self.effective(values)
        rows = []
        for n, f in self.fields.items():
            if f.sensitive or not eff.get(n):
                continue
            v = eff[n]
            rows.append({"label": f.label, "value": ", ".join(v) if isinstance(v, list) else v})
        return rows

    def describe(self, values):
        """Field spec + current state, returned to the LLM by start_task/set_fields."""
        eff = self.effective(values)
        return {
            "task_id": self.id,
            "title": self.title,
            "fields": [
                {
                    "name": n,
                    "label": f.label,
                    "required": f.required,
                    "type": f.type,
                    **({"default": f.default} if f.default else {}),
                    **({"hint": f.hint} if f.hint else {}),
                    **({"collected_securely_by_widget": True} if f.sensitive else {"value": eff.get(n)}),
                }
                for n, f in self.fields.items()
            ],
            "missing_required": [self.fields[n].label for n in self.missing(values)],
        }

    # ----- playbook rendering -----------------------------------------
    def render(self, values):
        """Concrete steps for the widget. `{{secret:x}}` stays for the browser to fill."""
        eff = self.effective(values)
        steps = []
        for step in self.playbook:
            cond = step.get("when")
            if cond and not any(eff.get(c) for c in _when(cond)):
                continue
            s = copy.deepcopy(step)
            s.pop("when", None)
            steps.append(_fill(s, eff))
        return steps


def _when(cond):
    """`when: field` or `when: [a, b]` (step runs if any of them has a value)."""
    if not cond:
        return []
    return [cond] if isinstance(cond, str) else list(cond)


def _fill(obj, values):
    if isinstance(obj, dict):
        return {k: _fill(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill(v, values) for v in obj]
    if not isinstance(obj, str):
        return obj
    whole = _TEMPLATE.fullmatch(obj.strip())
    if whole and not whole.group(1).startswith("secret:"):
        return copy.deepcopy(values.get(whole.group(1), ""))  # keeps lists as lists
    def sub(m):
        name = m.group(1)
        if name.startswith("secret:"):
            return m.group(0)
        v = values.get(name, "")
        return ", ".join(v) if isinstance(v, list) else str(v)
    return _TEMPLATE.sub(sub, obj)


def _template_names(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _template_names(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _template_names(v)
    elif isinstance(obj, str):
        for m in _TEMPLATE.finditer(obj):
            yield m.group(1)


def parse_task(data, source="<task>"):
    try:
        fields = {
            name: Field(name=name, **spec) for name, spec in (data.get("fields") or {}).items()
        }
        task = Task(
            id=data["id"],
            title=data["title"],
            summary=data.get("summary", ""),
            component=data["component"],
            fields=fields,
            playbook=data["playbook"],
            examples=data.get("examples", []),
        )
    except (KeyError, TypeError) as e:
        raise TaskError(f"{source}: invalid task definition ({e})") from e

    for i, step in enumerate(task.playbook):
        if step.get("do") not in _STEP_TYPES:
            raise TaskError(f"{source}: step {i + 1} has unknown action {step.get('do')!r}")
        for cond in _when(step.get("when")):
            if cond not in fields:
                raise TaskError(f"{source}: step {i + 1} 'when' refers to unknown field {cond!r}")
        for name in _template_names(step):
            ref = name.split(":", 1)[1] if name.startswith("secret:") else name
            if ref not in fields:
                raise TaskError(f"{source}: step {i + 1} uses unknown field {{{{{name}}}}}")
            if name.startswith("secret:") != fields[ref].sensitive:
                raise TaskError(f"{source}: step {i + 1} must use {{{{secret:{ref}}}}} only for sensitive fields")
    return task


def load_tasks(directory):
    tasks = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        task = parse_task(yaml.safe_load(path.read_text()), source=path.name)
        if task.id in tasks:
            raise TaskError(f"{path.name}: duplicate task id {task.id!r}")
        tasks[task.id] = task
    return tasks
