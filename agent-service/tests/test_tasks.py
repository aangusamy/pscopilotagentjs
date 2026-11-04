import unittest
from pathlib import Path

from psagent.tasks import TaskError, load_tasks, parse_task

TASKS_DIR = Path(__file__).resolve().parent / "fixtures" / "tasks"


def minimal(**overrides):
    data = {
        "id": "t",
        "title": "T",
        "component": {"menu": "M", "component": "C", "market": "GBL"},
        "fields": {"a": {"label": "A", "required": True}, "pw": {"label": "PW", "sensitive": True}},
        "playbook": [{"do": "set", "field": {"id": "X"}, "value": "{{a}}"}],
    }
    data.update(overrides)
    return data


class AssignRoleTest(unittest.TestCase):
    def test_adds_a_row_and_never_writes_to_a_fixed_row(self):
        task = load_tasks(Path(__file__).resolve().parents[1] / "tasks")["assign_role_to_user"]
        steps = task.render({"user_id": "RT1", "role_name": "Employee"})
        grid = next(s for s in steps if s["do"] == "grid")
        self.assertEqual(grid["field"]["id"], "PSROLEUSER_VW_ROLENAME", "no row number: a fixed row replaces a role")
        self.assertEqual(grid["values"], ["Employee"])
        self.assertFalse([s for s in steps if s["do"] == "set"])


class HcmProcessTasksTest(unittest.TestCase):
    def test_each_submits_its_process_from_its_run_control_page(self):
        tasks = load_tasks(Path(__file__).resolve().parents[1] / "tasks")
        for task_id, values in (
            ("refresh_employees_table", {"as_of_date": "11/30/2026"}),
            ("build_direct_reports_tables", {}),
            ("run_personnel_actions_history", {"from_date": "11/01/2026", "thru_date": "11/30/2026"}),
            ("run_emergency_contacts_report", {}),
        ):
            task = tasks[task_id]
            self.assertEqual(task.missing(values), [], task_id)
            steps = task.render(values)
            self.assertEqual(steps[-1]["do"], "expect_process", task_id)
            self.assertIn("PRCS8622", [s.get("value") for s in steps], task_id)


class CreateUserProfileTest(unittest.TestCase):
    def setUp(self):
        self.task = load_tasks(TASKS_DIR)["create_user_profile"]

    def test_required_fields_exclude_password_and_defaults(self):
        self.assertEqual(self.task.missing({}), ["user_id", "description"])
        self.assertEqual(self.task.missing({"user_id": "VELU", "description": "Velu"}), [])

    def test_user_id_is_uppercased_and_validated(self):
        f = self.task.fields["user_id"]
        self.assertEqual(f.normalize(" velu "), ("VELU", None))
        self.assertIsNotNone(f.normalize("velu kumar")[1])
        self.assertIsNotNone(f.normalize("X" * 31)[1])

    def test_roles_keep_case(self):
        self.assertEqual(
            self.task.fields["roles"].normalize("EMPLOYEE, PeopleSoft User,"),
            (["EMPLOYEE", "PeopleSoft User"], None),
        )

    def test_render_minimal_user(self):
        steps = self.task.render({"user_id": "VELU", "description": "Velu Arumugam"})
        kinds = [s["do"] for s in steps]
        self.assertEqual(kinds[0], "navigate")
        self.assertEqual(kinds[-2:], ["click", "expect_saved"])
        # Description is only editable on the ID tab, so that tab always runs; Roles only with roles
        self.assertEqual([st["label"] for st in steps if st["do"] == "tab"], ["ID"])
        values = {st["field"].get("id"): st.get("value") for st in steps if st["do"] == "set"}
        self.assertEqual(values["PSOPRDEFN_SRCH_OPRID"], "VELU")
        self.assertEqual(values["PSOPRDEFN_SYMBOLICID"], "SYSADM1")  # default applied
        self.assertEqual(values["PSUSRPRFL_WRK_PTPSWDCLEARTXTINPT"], "{{secret:password}}")  # left for the browser
        self.assertEqual(values["PSOPRDEFN_DVW_OPRDEFNDESC$0"], "Velu Arumugam")
        self.assertNotIn("PSOPRALIAS_OPRALIASTYPE$0", values)        # no emplid -> ID Type untouched
        self.assertNotIn("PSOPRDEFN_OPRCLASS", values)
        self.assertEqual(steps[2]["say"], "Adding user VELU")

    def test_render_full_user(self):
        steps = self.task.render({
            "user_id": "VELU", "description": "Velu", "emplid": "KU0042",
            "roles": ["EMPLOYEE", "PeopleSoft User"], "primary_permission_list": "HCCPEMPLOYEE",
        })
        self.assertEqual([s["label"] for s in steps if s["do"] == "tab"], ["ID", "Roles"])
        grid = next(s for s in steps if s["do"] == "grid")
        self.assertEqual(grid["values"], ["EMPLOYEE", "PeopleSoft User"])
        self.assertTrue(all("when" not in s for s in steps))

    def test_summary_hides_secrets(self):
        rows = self.task.summary_rows({"user_id": "VELU", "description": "Velu", "roles": ["A", "B"]})
        self.assertIn({"label": "Roles", "value": "A, B"}, rows)
        self.assertNotIn("Password", [r["label"] for r in rows])
        self.assertEqual(self.task.secrets(), [{"name": "password", "label": "Password"}])


class ParseTaskTest(unittest.TestCase):
    def test_unknown_template_field(self):
        with self.assertRaisesRegex(TaskError, "unknown field"):
            parse_task(minimal(playbook=[{"do": "set", "value": "{{nope}}"}]))

    def test_secret_must_use_secret_prefix(self):
        with self.assertRaisesRegex(TaskError, "sensitive"):
            parse_task(minimal(playbook=[{"do": "set", "value": "{{pw}}"}]))
        with self.assertRaisesRegex(TaskError, "sensitive"):
            parse_task(minimal(playbook=[{"do": "set", "value": "{{secret:a}}"}]))

    def test_unknown_step(self):
        with self.assertRaisesRegex(TaskError, "unknown action"):
            parse_task(minimal(playbook=[{"do": "delete_everything"}]))

    def test_bad_when(self):
        with self.assertRaisesRegex(TaskError, "when"):
            parse_task(minimal(playbook=[{"do": "tab", "when": "nope", "label": "X"}]))


if __name__ == "__main__":
    unittest.main()
