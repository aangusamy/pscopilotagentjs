"""A small .xlsx writer (no dependencies): text cells, a bold header row, column widths, drop-down lists.

Used for the watch plan template and for downloading a saved plan to edit and upload again.
runbooks.xlsx_text reads what this writes.
"""

import io
import zipfile
from xml.sax.saxutils import escape


def _col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref, value, style):
    text = escape(str(value)).replace("\n", "&#10;")
    space = ' xml:space="preserve"' if text != text.strip() or "&#10;" in text else ""
    return f'<c r="{ref}" t="inlineStr"{f" s={chr(34)}{style}{chr(34)}" if style else ""}><is><t{space}>{text}</t></is></c>'


def _sheet(sh):
    """sh: {name, rows: [[...]], widths: [...], header: row index (0-based) or None, title_rows: [indexes],
    lists: [(column index, [values])] (drop-downs below the header), freeze: True}"""
    header, titles = sh.get("header"), set(sh.get("title_rows") or [])
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(sh.get("widths") or []))
    rows = []
    for r, row in enumerate(sh["rows"]):
        style = 1 if r == header else 2 if r in titles else 3
        cells = "".join(_cell(f"{_col(c)}{r + 1}", v, style) for c, v in enumerate(row) if v not in (None, ""))
        rows.append(f'<row r="{r + 1}">{cells}</row>')
    pane = ""
    if header is not None and sh.get("freeze", True):
        pane = (f'<sheetViews><sheetView workbookViewId="0"><pane ySplit="{header + 1}" topLeftCell="A{header + 2}" '
                'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
    dv = ""
    lists = sh.get("lists") or []
    if lists and header is not None:
        items = []
        for c, values in lists:
            inline = '"' + ",".join(values) + '"'
            if len(inline) > 255 or any("," in v or '"' in v for v in values):
                continue                    # Excel refuses an inline list over 255 characters or with a comma in a value
            ref = f"{_col(c)}{header + 2}:{_col(c)}500"
            formula = escape(inline)
            items.append(f'<dataValidation type="list" allowBlank="1" showErrorMessage="1" sqref="{ref}">'
                         f"<formula1>{formula}</formula1></dataValidation>")
        dv = f'<dataValidations count="{len(items)}">{"".join(items)}</dataValidations>' if items else ""
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'{pane}{f"<cols>{cols}</cols>" if cols else ""}<sheetData>{"".join(rows)}</sheetData>{dv}</worksheet>')


_STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<fonts count="3"><font><sz val="11"/><name val="Calibri"/></font>'
           '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
           '<font><b/><sz val="12"/><name val="Calibri"/></font></fonts>'
           '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
           '<fill><patternFill patternType="solid"><fgColor rgb="FF0F766E"/><bgColor indexed="64"/></patternFill></fill></fills>'
           '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
           '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
           '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
           '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1">'
           '<alignment wrapText="1" vertical="center"/></xf>'
           '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
           '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf>'
           '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def write(sheets):
    """sheets: list of sheet dicts (see _sheet). -> .xlsx bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/'
                             f'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(len(sheets)))
                   + "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                   'Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(s["name"][:31])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, s in enumerate(sheets))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                             f'relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(sheets)))
                   + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                     'relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", _STYLES)
        for i, s in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet(s))
    return buf.getvalue()
