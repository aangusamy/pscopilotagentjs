import json
import re
import tempfile
import unittest
from pathlib import Path

from psagent import ci, runbooks, xlsx
from psagent.db import check_select

SHAPE_XML = (Path(__file__).parent / "fixtures" / "ci" / "user_profile_shape.xml").read_text(encoding="utf-8")

# PSBCITEM joined to PSRECFIELD for USER_PROFILE on HR92U052 (PeopleTools 8.61), trimmed
ITEMS = [
    ("UserID", "PS_ROOT", 1, "PSOPRDEFN_SRCH", "OPRID", " ", 2337),
    ("UserID", "PS_ROOT", 2, "PSOPRDEFN_SRCH", "OPRID", " ", 2337),
    ("UserID", "PS_ROOT", 4, "PSOPRDEFN", "OPRID", " ", 33),
    ("AccountLocked", "PS_ROOT", 4, "PSOPRDEFN", "ACCTLOCK", " ", 0),
    ("AccountLockDate", "PS_ROOT", 4, "PSOPRDEFN", "PTACCTLOCKDATE", " ", 8388608),
    ("CurrencyCode", "PS_ROOT", 4, "PSOPRDEFN", "CURRENCY_CD", "CURRENCY_CD_TBL", 16384),
    ("EffectiveDateFrom", "PS_ROOT", 4, "PSROLEXLATOPRVW", "EFFDT_FROM", " ", 0),
    ("EmailUser", "PS_ROOT", 4, "PSROLEXLATOPRVW", "EMAIL_USER_SW", " ", 8192),
    ("LanguageCode", "PS_ROOT", 4, "PSOPRDEFN", "LANGUAGE_CD", " ", 512),
    ("PrimaryPermissionList", "PS_ROOT", 4, "PSOPRDEFN", "OPRCLASS", "PSCLASSDEFN", 16384),
    ("SymbolicID", "PS_ROOT", 4, "PSOPRDEFN", "SYMBOLICID", "PSACCESSPROFILE", 16384),
    ("UserDescription", "PS_ROOT", 4, "PSOPRDEFN", "OPRDEFNDESC", " ", 32),
    ("EmailAddress", "EmailAddresses", 4, "PSUSEREMAIL", "EMAILID", " ", 8388608),
    ("EmailType", "EmailAddresses", 4, "PSUSEREMAIL", "EMAILTYPE", " ", 8389121),
    ("PrimaryEmail", "EmailAddresses", 4, "PSUSEREMAIL", "PRIMARY_EMAIL", " ", 8396800),
    ("IDType", "IDTypes", 4, "PSOPRALIAS", "OPRALIASTYPE", "PSUSERTYPEVW", 16641),
    ("RoleName", "Roles", 4, "PSROLEUSER_VW", "ROLENAME", "%EDITTABLE", 16641),
]
XLAT = [("EMAILTYPE", "BUS", "Business"), ("EMAILTYPE", "HOME", "Home"), ("EMAILTYPE", "WORK", "Work"),
        ("LANGUAGE_CD", "ENG", "English"), ("LANGUAGE_CD", "FRA", "French")]


class CIDB:
    """Answers the CI feature's queries by purpose, like HR92U052 would."""

    def __init__(self):
        self.users = {"PS", "VP1", "JSMITH"}
        self.classes = {"PTPT1000", "HCCPEMPLOYEE", "EMPLOYEE"}
        self.profiles = {"SYSADM1"}
        self.queries = []

    def query(self, sql, params=None, max_rows=200, who="", why="", **kw):
        check_select(sql)
        params = params or {}
        self.queries.append((why, sql, params))
        vals = list(params.values())
        if why == "ci catalog":
            rows = [("CI_JOB_DATA", "CI on JOB DATA", 0, 1, 1), ("USER_PROFILE", "User Profile Component", 1, 1, 1),
                    ("USERMAINT_SELF", "User Profile Maintenance", 0, 1, 0)]
            words = [v.strip("%") for k, v in params.items() if k.startswith("w")]
            rows = [r for r in rows if all(w in r[0] or w in r[1].upper() for w in words)]
            return ["BCNAME", "DESCR", "C", "G", "S"], rows, False
        if why == "ci description":
            return ["DESCR"], [("User Profile Component",)] if params["ci"] == "USER_PROFILE" else [], False
        if why == "ci items":
            return ["BCITEMNAME", "BCITEMPARENT", "BCTYPE", "RECNAME", "FIELDNAME", "EDITTABLE", "USEEDIT"], ITEMS, False
        if why == "ci uppercase fields":           # PSDBFIELD.FORMAT 0 on HR92U052; OPRID, ROLENAME, EMAILID are mixed case
            return ["FIELDNAME"], [(v,) for v in vals if v in ("CLASSID", "CURRENCY_CD")], False
        if why == "ci translate values":
            return ["FIELDNAME", "FIELDVALUE", "XLATLONGNAME"], [r for r in XLAT if r[0] in vals], False
        if why == "ci prompt tables":
            known = {"PSCLASSDEFN": ("PSCLASSDEFN", 0), "PSACCESSPROFILE": ("PSACCESSPRFL", 0), "CURRENCY_CD_TBL": (" ", 0),
                     "PSUSERTYPEVW": (" ", 1)}
            return ["RECNAME", "SQLTABLENAME", "RECTYPE"], [(r, *known[r]) for r in vals if r in known], False
        if why == "ci prompt fields":
            return ["RECNAME", "FIELDNAME", "FIELDNUM", "USEEDIT"], [
                ("PSCLASSDEFN", "CLASSID", 1, 1), ("PSCLASSDEFN", "VERSION", 2, 0), ("PSACCESSPROFILE", "SYMBOLICID", 1, 1),
                ("CURRENCY_CD_TBL", "CURRENCY_CD", 1, 1), ("CURRENCY_CD_TBL", "EFFDT", 2, 1)], False
        if why.startswith("ci prompt PSCLASSDEFN"):
            return ["CLASSID"], [(v,) for v in vals if v in self.classes], False
        if why.startswith("ci prompt PSACCESSPROFILE"):
            assert "FROM PSACCESSPRFL" in sql
            return ["SYMBOLICID"], [(v,) for v in vals if v in self.profiles], False
        if why.startswith("ci prompt CURRENCY_CD_TBL"):
            assert "FROM PS_CURRENCY_CD_TBL" in sql and "SELECT DISTINCT CURRENCY_CD" in sql
            return ["CURRENCY_CD"], [(v,) for v in vals if v in ("USD", "EUR")], False
        if why == "ci key record":
            return ["SQLTABLENAME", "RECTYPE"], [("PSOPRDEFN", 0)] if params["r"] == "PSOPRDEFN" else [(" ", 5)], False
        if why.startswith("ci key exists"):
            assert "FROM PSOPRDEFN WHERE OPRID IN" in sql
            return ["OPRID"], [(v,) for v in vals if v in self.users], False
        raise AssertionError(f"unexpected query: {why}")


def shape(db=None):
    return ci.enrich(ci.parse_shape(SHAPE_XML, "USER_PROFILE"), db or CIDB(), "VELU")


def filled(sh, rows, picked=None):
    """A filled template: rows as load records (the shape the agent writes back), through the real xlsx round trip."""
    return ci.template_xlsx(sh, picked, rows)


def rec(action, values, **children):
    return {"action": action, "raw": values, "values": values,
            "children": {k: [{"row_action": c.pop("_row", "change"), "raw": c, "values": c, "children": {}} for c in v]
                         for k, v in children.items()}}


PICK = {"UserDescription", "SymbolicID", "PrimaryPermissionList", "LanguageCode", "EffectiveDateFrom", "CurrencyCode",
        "EmailAddresses.EmailAddress", "EmailAddresses.PrimaryEmail", "Roles.RoleName"}


class ShapeTest(unittest.TestCase):
    def test_parse_get_ci_shape(self):
        sh = ci.parse_shape(SHAPE_XML, "USER_PROFILE")
        self.assertEqual((sh["descr"], sh["action"]), ("User Profile Component", "CREATE"))
        names = [p["name"] for p in sh["root"]["props"]]
        self.assertEqual(names[0], "UserID")
        self.assertEqual(names.count("UserID"), 1, "the key info and the property are one column")
        self.assertEqual([(c["path"], len(chain)) for c, chain in ci.collections(sh)],
                         [("EmailAddresses", 1), ("IDTypes", 1), ("IDTypes/Attributes", 2), ("Roles", 1), ("Roles/RouteControls", 2)])
        lock = next(p for p in sh["root"]["props"] if p["name"] == "AccountLockDate")
        self.assertEqual((lock["type"], lock["readonly"]), (4, True))

    def test_real_answers(self):
        # GetCIShape as HR92U052 answered it (PT 8.61): <Getcishape__CompIntfc__XResponse>, an empty description
        fx = Path(__file__).parent / "fixtures" / "ci"
        sh = ci.parse_shape((fx / "user_profile_shape_real.xml").read_text(encoding="utf-8"), "USER_PROFILE")
        self.assertEqual([p["name"] for p in ci.keys_of(sh["root"])], ["UserID"])
        self.assertEqual([c["path"] for c, _ in ci.collections(sh)],
                         ["EmailAddresses", "IDTypes", "IDTypes/Attributes", "Roles", "Roles/RouteControls"])
        self.assertEqual(sh["descr"], "")
        self.assertEqual({p["edit"] for p in sh["root"]["props"]}, {""}, "edit is a True/False flag, not the prompt table")
        self.assertEqual(ci.enrich(sh, CIDB(), "PS")["descr"], "User Profile Component", "filled from PSBCDEFN")
        job =ci.parse_shape((fx / "ci_job_data_shape_real.xml").read_text(encoding="utf-8"), "CI_JOB_DATA")
        self.assertEqual(job["action"], "UPDATE")
        self.assertEqual([p["name"] for p in ci.keys_of(job["root"])], ["KEYPROP_EMPLID", "KEYPROP_EMPL_RCD"])
        self.assertEqual(len(list(ci.collections(job))), 20)
        self.assertTrue(ci.template_xlsx(job, None, None, {"update": True}).startswith(b"PK"))

    def test_no_shape(self):
        fault = ('<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"><SOAP-ENV:Body><SOAP-ENV:Fault>'
                 "<faultcode>200</faultcode><faultstring>Component Interface API</faultstring><detail><messages><type>Error</type>"
                 "<messagesetnumber>91</messagesetnumber><messagenumber>2</messagenumber><messagetext>Cannot find Component "
                 "Interface NOPE</messagetext></messages></detail></SOAP-ENV:Fault></SOAP-ENV:Body></SOAP-ENV:Envelope>")
        with self.assertRaisesRegex(ci.CIError, "Cannot find Component Interface NOPE"):
            ci.parse_shape(fault, "NOPE")
        with self.assertRaisesRegex(ci.CIError, "no access to WEBLIB_SOAPTOCI"):
            ci.parse_shape("<html>Authorization Error</html>", "USER_PROFILE")
        with self.assertRaisesRegex(ci.CIError, "session has expired"):
            ci.parse_shape("<html><form><input id='userid'></form></html>", "USER_PROFILE")

    def test_enrich_from_peopletools_metadata(self):
        sh = shape()
        p = {x["name"]: x for x in sh["root"]["props"]}
        self.assertEqual((p["UserID"]["record"], p["UserID"]["search_record"]), ("PSOPRDEFN", "PSOPRDEFN_SRCH"))
        self.assertEqual(p["SymbolicID"]["edit"], "PSACCESSPROFILE")
        self.assertEqual(p["LanguageCode"]["values"][0], ("ENG", "English"))
        self.assertEqual(p["EmailUser"]["values"], [("Y", "Yes"), ("N", "No")])
        self.assertEqual(p["AccountLocked"]["values"], [])
        roles = next(c for c, _ in ci.collections(sh) if c["name"] == "Roles")
        self.assertEqual(roles["props"][0]["edit"], "", "a dynamic prompt (%EDITTABLE) is not checked ahead")
        emails = next(c for c, _ in ci.collections(sh) if c["name"] == "EmailAddresses")
        self.assertEqual([v for v, _ in emails["props"][0]["values"]], ["BUS", "HOME", "WORK"])

    def test_catalog(self):
        db = CIDB()
        cis, more = ci.catalog(db, "VELU", "user")
        self.assertEqual([c["name"] for c in cis], ["USER_PROFILE"], "USERMAINT_SELF has no Save: not loadable")
        self.assertEqual((cis[0]["create"], cis[0]["update"]), (True, True))
        self.assertEqual(db.queries[0][2]["oprid"], "VELU")
        self.assertIn("PS_USERCLASS_VW", db.queries[0][1])


class TemplateTest(unittest.TestCase):
    def test_default_template(self):
        sh = shape()
        text = runbooks.xlsx_text(ci.template_xlsx(sh))
        self.assertIn("Component Interface USER_PROFILE: User Profile Component", text)
        self.assertIn("Action | User ID * (UserID) | Symbolic ID * (SymbolicID)", text)
        self.assertIn("Row action | User ID (USER_PROFILE.UserID) | Email Type * (EmailType)", text)
        self.assertIn("Row action | User ID (USER_PROFILE.UserID) | ID Type (IDTypes.IDType)", text)
        self.assertIn("LanguageCode | Language Code |  |  | Text | 3 |  | ENG = English; FRA = French", text)
        self.assertNotIn("AccountLockDate)", text.split("Help")[0], "read-only properties are not columns")

    def test_picked_properties(self):
        sh = shape()
        names = [s for s, _ in runbooks.xlsx_sheets(ci.template_xlsx(sh, PICK))]
        self.assertEqual(names, ["USER_PROFILE", "EmailAddresses", "Roles", "Help"], "only collections with picked properties")


class LoadFileTest(unittest.TestCase):
    def setUp(self):
        self.db = CIDB()
        self.sh = shape(self.db)

    def upload(self, rows):
        return ci.read_upload(filled(self.sh, rows, PICK), self.sh)

    def test_round_trip_and_checks(self):
        recs, problems = self.upload([
            rec("create", {"UserID": "CITEST01", "UserDescription": "CI Test & Co", "SymbolicID": "SYSADM1", "PrimaryPermissionList": "PTPT1000",
                           "LanguageCode": "english", "EffectiveDateFrom": "2026-11-01", "CurrencyCode": "USD"},
                Roles=[{"RoleName": "EMPLOYEE"}, {"RoleName": "PeopleSoft User"}], EmailAddresses=[{"EmailType": "WORK", "EmailAddress": "ci@x.com"}]),
            rec("create", {"UserID": "JSMITH", "SymbolicID": "SYSADM1"}),                                # exists
            rec("create", {"UserID": "CITEST02", "SymbolicID": "NOPE", "PrimaryPermissionList": "BADLIST",
                           "EffectiveDateFrom": "31/31/2026"}, EmailAddresses=[{"EmailType": "XX", "EmailAddress": "a@b"}]),
            rec("update", {"UserID": "NOSUCH", "UserDescription": "x"}),                                  # missing
            rec("update", {"UserID": "VP1", "UserDescription": "New name"}, Roles=[{"RoleName": "EMPLOYEE", "_row": "add"}]),
            rec("create", {"UserID": "CITEST01", "SymbolicID": "SYSADM1"}),                              # duplicate in the file
            rec("", {"UserID": "CITEST03"}),
        ])
        self.assertEqual(problems, [])
        self.assertEqual(len(recs), 7)
        self.assertEqual([r["row"] for r in recs][:2], [4, 5])
        c = ci.check(recs, self.sh, self.db, "VELU")
        self.assertEqual(c, {"rows": 7, "ready": 2, "problems": 5})
        p = {r["values"]["UserID"] + r["action"]: r["problems"] for r in recs}
        self.assertEqual(recs[0]["problems"], [])
        self.assertEqual(recs[0]["values"]["LanguageCode"], "ENG", "a translate label becomes its code")
        self.assertEqual(recs[0]["values"]["EffectiveDateFrom"], "20261101", "dates go to the CI as YYYYMMDD")
        self.assertEqual(p["JSMITHcreate"], ["User ID JSMITH already exists: use Update to change it"])
        bad = "\n".join(p["CITEST02create"])
        for s in ["From Date 31/31/2026 is not a date", "Email Type XX is not one of: BUS (Business)", "Symbolic ID NOPE does not exist",
                  "Primary Permission List BADLIST does not exist (prompt table PSCLASSDEFN)"]:
            self.assertIn(s, bad)
        self.assertEqual(p["NOSUCHupdate"], ["User ID NOSUCH does not exist: use Create to add it"])
        self.assertEqual(p["VP1update"], [])
        self.assertEqual(recs[5]["problems"], ["the same key as row 4"])
        self.assertEqual(recs[6]["problems"], ["Action is empty (Create or Update)"])

    def test_orphan_rows_and_wrong_files(self):
        raw = filled(self.sh, [rec("create", {"UserID": "A1", "SymbolicID": "SYSADM1"})], PICK)
        sheets = runbooks.xlsx_sheets(raw)
        # add a Roles row for a user that is not in the file
        book = [{"name": n, "rows": [c for _, c in rows] + ([["", "GHOST", "EMPLOYEE"]] if n == "Roles" else []), "header": 2}
                for n, rows in sheets]
        recs, problems = ci.read_upload(xlsx.write(book), self.sh)
        self.assertEqual(problems, ["Roles row 4: no USER_PROFILE row with UserID GHOST"])
        other = ci.parse_shape(SHAPE_XML.replace("USER_PROFILE", "CI_JOB_DATA"), "CI_JOB_DATA")
        with self.assertRaisesRegex(ci.CIError, "template for USER_PROFILE, not CI_JOB_DATA"):
            ci.read_upload(raw, other)
        with self.assertRaisesRegex(ci.CIError, "not a USER_PROFILE template"):
            ci.read_upload(xlsx.write([{"name": "Sheet1", "rows": [["a", "b"]]}]), self.sh)


class KeysDB:
    """PSBCITEM of CI_HR_LBR_GRP_DEFN on HR92U052: GB_GROUP_ID creates it, GB_GROUP_ID and PNLGRPNAME find it (trimmed)."""
    ITEMS = [("GB_GROUP_ID", "PS_ROOT", 1, "GB_GROUP_SRCH", "GB_GROUP_ID", " ", 8390945),
             ("PNLGRPNAME", "PS_ROOT", 1, "GB_GROUP_SRCH", "PNLGRPNAME", " ", 8390657),
             ("GB_GROUP_ID", "PS_ROOT", 2, "GB_GROUP_TBL", "GB_GROUP_ID", " ", 8390945),
             ("GB_GROUP_ID", "PS_ROOT", 4, "HR_LBR_GRP_DEFN", "GB_GROUP_ID", " ", 8390945),
             ("DESCR", "PS_ROOT", 4, "HR_LBR_GRP_DEFN", "DESCR", " ", 41943296),
             ("JOBCODE", "HR_LBR_GRP_JOBS", 4, "HR_LBR_GRP_JOBS", "JOBCODE", " ", 8388609)]

    def query(self, sql, params=None, max_rows=200, who="", why="", **kw):
        check_select(sql)
        if why == "ci items":
            return ["BCITEMNAME", "BCITEMPARENT", "BCTYPE", "RECNAME", "FIELDNAME", "EDITTABLE", "USEEDIT"], self.ITEMS, False
        if why == "ci key fields":
            assert list(params.values()) == ["PNLGRPNAME"]
            return ["FIELDNAME", "FIELDTYPE", "LENGTH", "DECIMALPOS", "LONGNAME"], [("PNLGRPNAME", 0, 18, 0, "Component Name")], False
        if why == "ci description":
            return ["DESCR"], [("Labor Group Definition",)], False
        return [], [], False


class KeysTest(unittest.TestCase):
    """GetCIShape lists the create keys; Update (Updatedata) finds the record by the get keys (SOAPTOCI setKeys)."""

    def setUp(self):
        xml = (Path(__file__).parent / "fixtures" / "ci" / "ci_hr_lbr_grp_defn_shape_real.xml").read_text(encoding="utf-8")
        self.sh = ci.enrich(ci.parse_shape(xml, "CI_HR_LBR_GRP_DEFN"), KeysDB(), "PS")

    def test_get_keys_join_the_template(self):
        self.assertEqual((self.sh["get_keys"], self.sh["create_keys"]), (["GB_GROUP_ID", "PNLGRPNAME"], ["GB_GROUP_ID"]))
        self.assertEqual([p["name"] for p in ci.action_keys(self.sh, "update")], ["GB_GROUP_ID", "PNLGRPNAME"])
        self.assertEqual([p["name"] for p in ci.action_keys(self.sh, "create")], ["GB_GROUP_ID"])
        head = runbooks.xlsx_sheets(ci.template_xlsx(self.sh))[0][1][2][1]
        self.assertIn("Component Name (for Update) (PNLGRPNAME)", head)

    def test_each_action_its_keys(self):
        recs = [{"n": 1, "row": 4, "sheet": "x", "action": "update", "values": {"GB_GROUP_ID": "G1", "DESCR": "x"}, "children": {}, "problems": []},
                {"n": 2, "row": 5, "sheet": "x", "action": "update", "values": {"GB_GROUP_ID": "G2", "PNLGRPNAME": "JOB_DATA"}, "children": {},
                 "problems": []},
                {"n": 3, "row": 6, "sheet": "x", "action": "create", "values": {"GB_GROUP_ID": "G3", "DESCR": "New"}, "children": {},
                 "problems": []}]
        ci.check(recs, self.sh, None, "PS", {"create": True, "update": True})
        self.assertEqual(recs[0]["problems"], ["Component Name is required to find the record"])
        self.assertEqual(recs[1]["problems"], [])
        self.assertFalse([x for x in recs[2]["problems"] if "Component Name" in x or "Group" in x], "Create needs only the create keys")
        self.assertIn("<PNLGRPNAME>JOB_DATA</PNLGRPNAME>", ci.envelope(self.sh, recs[1]))
        recs[2]["values"]["PNLGRPNAME"] = "IGNORED"
        self.assertNotIn("PNLGRPNAME", ci.envelope(self.sh, recs[2]), "not a create key nor a property: SOAPTOCI would refuse it")

    def test_kept_shapes_from_before_are_read_again(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "shapes").mkdir()
            old = dict(self.sh)
            old.pop("v")
            (Path(d) / "shapes" / "CI_HR_LBR_GRP_DEFN.json").write_text(json.dumps(old), encoding="utf-8")
            (Path(d) / "shapes" / "NEW.json").write_text(json.dumps(dict(self.sh, ci="NEW")), encoding="utf-8")
            self.assertEqual(list(ci.Store(d).shapes), ["NEW"])


class ValueTest(unittest.TestCase):
    def test_datetime_numbers(self):
        dt = {"label": "Updated", "type": 6, "length": 26}
        for v in ("2026-11-01 09:30", "2026-11-01T09:30:00", "11/01/2026 09:30", "46327.395833"):
            self.assertEqual(ci.normalize(dt, v), ("2026-11-01-09.30.00.000000", None), v)
        self.assertIn("not a date and time", ci.normalize(dt, "soon")[1])
        num = {"label": "Rate", "type": 3, "length": 8, "decimals": 3}
        self.assertEqual(ci.normalize(num, "0.30000000000000004"), ("0.300", None))
        self.assertEqual(ci.normalize(num, "1,234.5"), ("1234.500", None))
        self.assertEqual(ci.normalize(num, "1E2"), ("100.000", None))
        self.assertIn("at most 3 decimals", ci.normalize(num, "1.2345")[1])
        self.assertEqual(ci.normalize({"label": "Empl ID", "type": 0, "length": 11, "upper": True}, "kw123"), ("KW123", None))
        self.assertEqual(ci.normalize({"label": "Role", "type": 0, "length": 30}, "PeopleSoft User"), ("PeopleSoft User", None))
        whole = {"label": "Count", "type": 2, "length": 3}
        self.assertEqual(ci.normalize(whole, "5.0000000000001"), ("5", None))
        self.assertIn("whole number", ci.normalize(whole, "5.5")[1])

    def test_drop_downs_excel_can_open(self):
        raw = xlsx.write([{"name": "S", "rows": [["h1", "h2", "h3"]], "header": 0,
                           "lists": [(0, ["A", "B"]), (1, ["x" * 30] * 9), (2, ["a,b", "c"])]}])
        import zipfile, io
        sheet = zipfile.ZipFile(io.BytesIO(raw)).read("xl/worksheets/sheet1.xml").decode()
        self.assertEqual(re.findall(r"<formula1>(.*?)</formula1>", sheet), ['"A,B"'])
        raw = xlsx.write([{"name": "S", "rows": [["h1"]], "header": 0, "lists": [(0, ["a,b"])]}])
        self.assertNotIn("dataValidations", zipfile.ZipFile(io.BytesIO(raw)).read("xl/worksheets/sheet1.xml").decode())


class SoapTest(unittest.TestCase):
    def setUp(self):
        self.sh = shape()

    def test_create_envelope(self):
        r = rec("create", {"UserID": "CITEST01", "UserDescription": "A & B <x>", "SymbolicID": "SYSADM1"},
                Roles=[{"RoleName": "EMPLOYEE"}, {"RoleName": "PeopleSoft User"}], EmailAddresses=[{"EmailType": "WORK", "EmailAddress": "a@b.c"}])
        x = ci.envelope(self.sh, r)
        body = re.search(r"<SOAP-ENV:Body>(.*)</SOAP-ENV:Body>", x).group(1)
        self.assertTrue(body.startswith("<Create__CompIntfc__USER_PROFILE><UserID>CITEST01</UserID>"), "the keys first")
        self.assertIn("<UserDescription>A &amp; B &lt;x&gt;</UserDescription>", body)
        self.assertIn("<Roles><RoleName>EMPLOYEE</RoleName></Roles><Roles><RoleName>PeopleSoft User</RoleName></Roles>", body)
        self.assertIn("<EmailAddresses><EmailType>WORK</EmailType><EmailAddress>a@b.c</EmailAddress></EmailAddresses>", body)
        import xml.etree.ElementTree as ET
        ET.fromstring(x)          # well-formed

    def test_update_envelope_row_actions(self):
        r = rec("update", {"UserID": "VP1", "UserDescription": "New"}, Roles=[{"RoleName": "EMPLOYEE", "_row": "add"},
                                                                             {"RoleName": "OLDROLE", "_row": "delete"}, {"RoleName": "SAME"}])
        x = ci.envelope(self.sh, r)
        self.assertIn("<Updatedata__CompIntfc__USER_PROFILE><UserID>VP1</UserID><UserDescription>New</UserDescription>", x)
        self.assertIn('<Roles CINodeAction="INSERT"><RoleName>EMPLOYEE</RoleName></Roles><Roles CINodeAction="DELETE">'
                      "<RoleName>OLDROLE</RoleName></Roles><Roles><RoleName>SAME</RoleName></Roles>", x)

    def test_passwords_are_filled_in_the_browser(self):
        sh = json.loads(json.dumps(self.sh))
        sh["root"]["props"].append({"name": "OperatorPassword", "label": "Password", "type": 0, "length": 32, "key": False, "required": True,
                                    "readonly": False, "edit": "", "secret": True, "values": []})
        self.assertNotIn("OperatorPassword", runbooks.xlsx_text(ci.template_xlsx(sh)).split("Help")[0])
        x = ci.envelope(sh, rec("create", {"UserID": "A1"}))
        self.assertIn("<OperatorPassword>{{secret:OperatorPassword}}</OperatorPassword>", x)
        self.assertEqual(ci.secrets_of(sh, [dict(rec("create", {"UserID": "A1"}), problems=[])]), [{"name": "OperatorPassword", "label": "Password"}])
        book = [{"name": "USER_PROFILE", "rows": [["Component Interface USER_PROFILE: x"], ["n"], ["Action", "User ID (UserID)", "Password (OperatorPassword)"],
                                                  ["Create", "A1", "secret!"]]}]
        with self.assertRaisesRegex(ci.CIError, "passwords are never in the file"):
            ci.read_upload(xlsx.write(book), sh)

    def test_results(self):
        ok = ('<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"><SOAP-ENV:Body><Create__CompIntfc__USER_PROFILE>'
              "<notification>1</notification><detail><keyinformation><UserID>CITEST01</UserID></keyinformation></detail>"
              "</Create__CompIntfc__USER_PROFILE></SOAP-ENV:Body></SOAP-ENV:Envelope>")
        self.assertEqual(ci.parse_result(ok)["status"], "ok")
        warn = ok.replace("</detail>", "<messages><type>Warning</type><messagesetnumber>48</messagesetnumber><messagenumber>2</messagenumber>"
                                       "<messagetext>Email address not verified</messagetext><explaintext/></messages></detail>")
        r = ci.parse_result(warn)
        self.assertEqual((r["status"], r["detail"]), ("warning", "Email address not verified"))
        err = ('<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"><SOAP-ENV:Body><SOAP-ENV:Fault><faultcode>200</faultcode>'
               "<faultstring>Component Interface API</faultstring><runcode>0</runcode><detail><keyinformation><UserID>CITEST01</UserID>"
               "</keyinformation><messages><type>Error</type><messagesetnumber>15</messagesetnumber><messagenumber>14</messagenumber>"
               "<messagetext>Invalid value -- press the prompt button or hyperlink for a list of valid values (15,11) {USER_PROFILE.Roles(1).RoleName}"
               "</messagetext><explaintext>The value entered in the field does not match one of the allowable values.</explaintext></messages>"
               "</detail></SOAP-ENV:Fault></SOAP-ENV:Body></SOAP-ENV:Envelope>")
        r = ci.parse_result(err)
        self.assertEqual(r["status"], "error")
        self.assertIn("Invalid value", r["detail"])
        self.assertEqual(r["messages"][0]["explain"], "The value entered in the field does not match one of the allowable values.")
        self.assertIn("session has expired", ci.parse_result("<html><input name='userid'></html>")["detail"])
        self.assertEqual(ci.parse_result("")["status"], "error")

    def test_a_failed_row_shows_the_reason_not_only_that_the_save_failed(self):
        def msg(kind, text):
            return (f"<messages><type>{kind}</type><messagesetnumber>1</messagesetnumber><messagenumber>1</messagenumber>"
                    f"<messagetext>{text}</messagetext><explaintext/></messages>")
        # as PeopleSoft answered for a Create without an ID type (HR92U052, PT 8.61)
        answer = ('<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"><SOAP-ENV:Body><SOAP-ENV:Fault><detail>'
                  + msg("Message", "At least one ID type required. (48,50)")
                  + msg("Error", "Error saving Component Interface. {USER_PROFILE} (91,37)")
                  + msg("Error", "Error saving Component Interface. {USER_PROFILE}")
                  + "</detail></SOAP-ENV:Fault></SOAP-ENV:Body></SOAP-ENV:Envelope>")
        r = ci.parse_result(answer)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["detail"], "At least one ID type required. (48,50); Error saving Component Interface. {USER_PROFILE} (91,37)")
        self.assertEqual(len(r["messages"]), 3)

    def test_an_update_may_add_rows_to_one_list_only(self):
        sh = ci.parse_shape(SHAPE_XML, "USER_PROFILE")

        def full(action, values, **children):
            r = rec(action, values, **children)
            r.update(row=4, problems=[])
            for kids in r["children"].values():
                for k in kids:
                    k.update(sheet="Sheet", row=4)
            return r
        email = {"EmailType": "WORK", "EmailAddress": "a@example.com", "_row": "add"}
        both = full("update", {"UserID": "U1"}, EmailAddresses=[dict(email)], Roles=[{"RoleName": "EMPLOYEE", "_row": "add"}])
        ci.check([both], sh, None, "VELU")
        self.assertEqual(len(both["problems"]), 1)
        self.assertIn("Add rows in EmailAddresses and Roles cannot go in one Update", both["problems"][0])
        one = full("update", {"UserID": "U1"}, Roles=[{"RoleName": "A", "_row": "add"}, {"RoleName": "B", "_row": "add"}, {"RoleName": "C", "_row": "delete"}])
        delete_too = full("update", {"UserID": "U2"}, EmailAddresses=[dict(email)], Roles=[{"RoleName": "C", "_row": "delete"}])
        created = full("create", {"UserID": "U3"}, EmailAddresses=[dict(email)], Roles=[{"RoleName": "A", "_row": "add"}])
        ci.check([one, delete_too, created], sh, None, "VELU")
        for r in (one, delete_too, created):
            self.assertFalse([p for p in r["problems"] if "Add rows" in p], r["values"])

    def test_rows_added_under_an_added_row_count_as_one_list(self):
        added = {"row_action": "add", "path": "Roles", "values": {}, "children": {
            "RouteControls": [{"row_action": "add", "path": "Roles/RouteControls", "values": {}, "children": {}}]}}
        self.assertEqual(ci._lists_with_adds({"children": {"Roles": [added]}}), ["Roles"])
        kept = {"row_action": "change", "path": "Roles", "values": {}, "children": added["children"]}
        email = {"row_action": "add", "path": "EmailAddresses", "values": {}, "children": {}}
        self.assertEqual(ci._lists_with_adds({"children": {"EmailAddresses": [email], "Roles": [kept]}}),
                         ["EmailAddresses", "Roles/RouteControls"], "a row added under an existing row can be overwritten too")


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = CIDB()
        self.sh = shape(self.db)
        self.store = ci.Store(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_rows_results_and_rows_to_fix(self):
        recs, problems = ci.read_upload(filled(self.sh, [
            rec("create", {"UserID": "CITEST01", "SymbolicID": "SYSADM1"}, Roles=[{"RoleName": "EMPLOYEE"}]),
            rec("create", {"UserID": "CITEST02", "SymbolicID": "SYSADM1"}, Roles=[{"RoleName": "BADROLE"}]),
            rec("create", {"UserID": "JSMITH", "SymbolicID": "SYSADM1"}),
        ], PICK), self.sh)
        ci.check(recs, self.sh, self.db, "VELU")
        ld = ci.new_load(self.store, self.sh, recs, problems, "VELU", "users.xlsx", {"create": True, "update": True})
        v = ci.view(ld, self.sh)
        self.assertEqual((v["total"], v["processed"], v["counts"]["problems"]), (2, 0, 1))
        self.assertEqual([r["status"] for r in v["rows"]], ["ready", "ready", "problem"])
        ok = ('<SOAP-ENV:Envelope xmlns:SOAP-ENV="x"><SOAP-ENV:Body><Create__CompIntfc__USER_PROFILE><notification>1</notification>'
              "</Create__CompIntfc__USER_PROFILE></SOAP-ENV:Body></SOAP-ENV:Envelope>")
        bad = ('<SOAP-ENV:Envelope xmlns:SOAP-ENV="x"><SOAP-ENV:Body><SOAP-ENV:Fault><detail><messages><type>Error</type>'
               "<messagetext>Invalid value -- press the prompt button {USER_PROFILE.Roles(1).RoleName}</messagetext></messages></detail>"
               "</SOAP-ENV:Fault></SOAP-ENV:Body></SOAP-ENV:Envelope>")
        first = ci.next_row(ld, self.sh)
        self.assertEqual((first["n"], first["label"]), (1, "CITEST01"))
        self.assertEqual(ci.record_result(self.store, ld, self.sh, 1, ok)["status"], "ok")
        self.assertEqual(ci.next_row(ld, self.sh)["n"], 2)
        # the service restarts mid-load: the load is paused, and resumes where it stopped
        ld["status"] = "running"
        self.store.save(ld)
        again = ci.Store(self.tmp.name)
        ld2 = again.loads[ld["load_id"]]
        self.assertEqual((ld2["status"], ci.next_row(ld2, self.sh)["n"]), ("paused", 2))
        self.assertEqual(ci.record_result(again, ld2, self.sh, 2, bad)["status"], "error")
        self.assertEqual(ld2["status"], "done")
        self.assertIsNone(ci.record_result(again, ld2, self.sh, 3, ok), "a row with problems is never sent")
        res = runbooks.xlsx_text(ci.results_xlsx(ld2, self.sh))
        self.assertIn("CITEST01 | 1 | Saved", res)
        self.assertIn("CITEST02 | 1 | Failed | Invalid value", res)
        self.assertIn("JSMITH |  | Not loaded | User ID JSMITH already exists", res)
        raw, n = ci.fix_xlsx(ld2, self.sh)
        self.assertEqual(n, 2)
        back, _ = ci.read_upload(raw, self.sh)
        self.assertEqual([(r["values"]["UserID"], [k["values"] for k in r["children"].get("Roles", [])]) for r in back],
                         [("CITEST02", [{"RoleName": "BADROLE"}]), ("JSMITH", [])])


if __name__ == "__main__":
    unittest.main()


class LooseDB(CIDB):
    """CIDB for the API: the welcome screen's other queries (security admin, recent failures) find nothing."""

    def query(self, sql, params=None, max_rows=200, who="", why="", **kw):
        try:
            return super().query(sql, params, max_rows, who, why, **kw)
        except AssertionError as e:
            if "unexpected query" in str(e):
                return ["X"], [], False
            raise


class ApiTest(unittest.TestCase):
    def setUp(self):
        import base64
        from starlette.testclient import TestClient
        from fakes import FakeLLM, response, text, tool
        from psagent import auth
        from psagent.agent import Agent
        from psagent.server import create_app
        from psagent.tasks import load_tasks
        self.b64 = base64
        self.tmp = tempfile.TemporaryDirectory()
        self.db = LooseDB()
        self.llm = FakeLLM()
        self.response, self.text, self.tool = response, text, tool
        tasks = load_tasks(Path(__file__).parent / "fixtures" / "tasks")
        self.c = TestClient(create_app(Agent(self.llm, tasks, self.db), "s3cret", ["http://ps"], ci_store=ci.Store(self.tmp.name)))
        self.h = lambda o: {"Authorization": "Bearer " + auth.make_token("s3cret", o, roles=["USER"])}

    def tearDown(self):
        self.tmp.cleanup()

    def shape(self, who="VELU"):
        r = self.c.get("/api/ci/shape?ci=USER_PROFILE", headers=self.h(who)).json()
        if "request" in r:
            self.assertIn("<GetCIShape__CompIntfc__USER_PROFILE/>", r["request"])
            self.assertEqual(r["soaptoci"], "WEBLIB_SOAPTOCI.SOAPTOCI.FieldFormula.IScript_SOAPToCI")
            r = self.c.post("/api/ci/shape", json={"ci": "USER_PROFILE", "xml": SHAPE_XML}, headers=self.h(who)).json()
        return r["shape"]

    def test_the_whole_load(self):
        h = self.h("VELU")
        self.assertTrue(self.c.get("/api/me", headers=h).json()["ci"])
        self.assertEqual([x["name"] for x in self.c.get("/api/ci?q=user", headers=h).json()["cis"]], ["USER_PROFILE"])
        s = self.shape()
        self.assertEqual((s["ci"], s["can"]), ("USER_PROFILE", {"create": True, "update": True}))
        self.assertEqual([p["name"] for p in s["root"]["props"] if p["default"]], ["UserID", "SymbolicID"])
        self.assertIn("shape", self.c.get("/api/ci/shape?ci=USER_PROFILE", headers=h).json(), "cached after the first time")
        t = self.c.post("/api/ci/template", json={"ci": "USER_PROFILE", "props": sorted(PICK)}, headers=h).json()
        self.assertEqual(t["name"], "user_profile_template.xlsx")
        sh = shape(self.db)
        raw = filled(sh, [rec("create", {"UserID": "CITEST01", "SymbolicID": "SYSADM1"}, Roles=[{"RoleName": "EMPLOYEE"}]),
                          rec("create", {"UserID": "CITEST02", "SymbolicID": "SYSADM1"}),
                          rec("create", {"UserID": "JSMITH", "SymbolicID": "SYSADM1"})], PICK)
        ld = self.c.post("/api/ci/check", json={"name": "users.xlsx", "data": self.b64.b64encode(raw).decode()}, headers=h).json()["load"]
        self.assertEqual((ld["ci"], ld["counts"], ld["status"]), ("USER_PROFILE", {"rows": 3, "ready": 2, "problems": 1}, "checked"))
        lid = ld["load_id"]
        self.assertEqual(self.c.post("/api/ci/next", json={"load_id": lid}, headers=h).json()["paused"], True, "nothing runs before Start")
        self.assertEqual(self.c.post("/api/ci/start", json={"load_id": lid}, headers=h).json()["load"]["status"], "running")
        other = self.h("JOE")
        self.assertEqual(self.c.post("/api/ci/next", json={"load_id": lid}, headers=other).status_code, 404, "only the owner")
        ok = ('<SOAP-ENV:Envelope xmlns:SOAP-ENV="x"><SOAP-ENV:Body><Create__CompIntfc__USER_PROFILE><notification>1</notification>'
              "</Create__CompIntfc__USER_PROFILE></SOAP-ENV:Body></SOAP-ENV:Envelope>")
        row = self.c.post("/api/ci/next", json={"load_id": lid}, headers=h).json()["row"]
        self.assertEqual((row["n"], row["label"]), (1, "CITEST01"))
        self.assertIn("<Roles><RoleName>EMPLOYEE</RoleName></Roles>", row["xml"])
        r = self.c.post("/api/ci/result", json={"load_id": lid, "n": 1, "response": ok}, headers=h).json()
        self.assertEqual((r["result"]["status"], r["load"]["processed"]), ("ok", 1))
        self.c.post("/api/ci/pause", json={"load_id": lid}, headers=h)
        self.assertTrue(self.c.post("/api/ci/next", json={"load_id": lid}, headers=h).json()["paused"])
        self.c.post("/api/ci/start", json={"load_id": lid}, headers=h)
        row = self.c.post("/api/ci/next", json={"load_id": lid}, headers=h).json()["row"]
        self.c.post("/api/ci/result", json={"load_id": lid, "n": row["n"], "response": "<html><input id='userid'></html>"}, headers=h)
        done = self.c.post("/api/ci/next", json={"load_id": lid}, headers=h).json()
        self.assertEqual((done["done"], done["load"]["status"], done["load"]["done"]), (True, "done", {"ok": 1, "warning": 0, "error": 1}))
        self.assertIn("session has expired", done["load"]["rows"][1]["detail"])
        loads = self.c.get("/api/ci/loads", headers=h).json()["loads"]
        self.assertEqual([x["load_id"] for x in loads], [lid])
        self.assertEqual(self.c.get("/api/ci/loads", headers=other).json()["loads"], [])
        res = self.c.get(f"/api/ci/export?load={lid}", headers=h).json()
        self.assertEqual(res["name"], "users-results.xlsx")
        fix = self.c.get(f"/api/ci/export?load={lid}&what=fix", headers=h).json()
        self.assertIn("CITEST02", runbooks.xlsx_text(self.b64.b64decode(fix["data"])))
        self.assertEqual(self.c.post("/api/ci/start", json={"load_id": lid}, headers=h).status_code, 409, "a finished load is not rerun")

    def test_permissions(self):
        h = self.h("VELU")
        self.assertEqual(self.c.get("/api/ci/shape?ci=USERMAINT_SELF", headers=h).status_code, 403)
        self.assertEqual(self.c.post("/api/ci/template", json={"ci": "USER_PROFILE"}, headers=h).status_code, 409, "pick it first")
        bad = self.c.post("/api/ci/shape", json={"ci": "USER_PROFILE", "xml": "<html>Authorization Error</html>"}, headers=h)
        self.assertEqual(bad.status_code, 502)
        self.assertIn("WEBLIB_SOAPTOCI", bad.json()["error"])

    def test_a_template_dropped_in_the_chat_is_checked_straight_away(self):
        h = self.h("VELU")
        self.shape()
        raw = filled(shape(self.db), [rec("create", {"UserID": "CITEST09", "SymbolicID": "SYSADM1"})], PICK)
        att = {"name": "new users.xlsx", "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
               "data": self.b64.b64encode(raw).decode()}
        r = self.c.post("/api/chat", json={"message": "load these", "attachment": att}, headers=h).json()
        self.assertEqual((r["ci_load"]["ci"], r["ci_load"]["load"]["counts"]["ready"]), ("USER_PROFILE", 1))
        self.assertEqual(self.llm.calls, [], "no Claude call for a template")

    def test_asking_in_the_chat_opens_the_picker(self):
        self.llm.responses += [self.response(self.tool("offer_ci_load", {"ci": "user_profile"})), self.response(self.text("Pick the columns."))]
        r = self.c.post("/api/chat", json={"message": "I need to create 200 users from excel"}, headers=self.h("VELU")).json()
        self.assertEqual(r["ci_load"], {"ci": "USER_PROFILE"})
