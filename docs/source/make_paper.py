"""PeopleSoft Agent technical paper (PDF), reportlab platypus."""
from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame, KeepTogether, NextPageTemplate, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

OUT = r"C:\Velu\2026\5AgenticWorkflow\pscopilotagentjs\docs\PSAgent_Technical_Paper.pdf"
FD = r"C:\Windows\Fonts"
pdfmetrics.registerFont(TTFont("Arial", FD + r"\arial.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Bold", FD + r"\arialbd.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Italic", FD + r"\ariali.ttf"))
pdfmetrics.registerFont(TTFont("Arial-BoldItalic", FD + r"\arialbi.ttf"))
pdfmetrics.registerFont(TTFont("Consolas", FD + r"\consola.ttf"))
from reportlab.pdfbase.pdfmetrics import registerFontFamily  # noqa: E402

registerFontFamily("Arial", normal="Arial", bold="Arial-Bold", italic="Arial-Italic", boldItalic="Arial-BoldItalic")

INK = colors.HexColor("#1A1A1A")
SUB = colors.HexColor("#555555")
RED = colors.HexColor("#B3261E")
SLATE = colors.HexColor("#22313F")
PALE = colors.HexColor("#F3F5F7")
LINE = colors.HexColor("#C9CED6")
BLUE = colors.HexColor("#2F5D8A")
BLUEPALE = colors.HexColor("#E6EEF6")
GREENPALE = colors.HexColor("#E8F3EA")
AMBERPALE = colors.HexColor("#FFF4DC")
REDPALE = colors.HexColor("#FBE9E7")

W, H = letter
LM = RM = 0.9 * inch
TW = W - LM - RM

ss = {}
ss["body"] = ParagraphStyle("body", fontName="Arial", fontSize=9.6, leading=13.6, textColor=INK, spaceAfter=6)
ss["small"] = ParagraphStyle("small", parent=ss["body"], fontSize=8.2, leading=10.8, spaceAfter=0)
ss["cell"] = ParagraphStyle("cell", parent=ss["body"], fontSize=8.2, leading=10.6, spaceAfter=0)
ss["cellb"] = ParagraphStyle("cellb", parent=ss["cell"], fontName="Arial-Bold")
ss["cellh"] = ParagraphStyle("cellh", parent=ss["cell"], fontName="Arial-Bold", textColor=colors.white)
ss["bullet"] = ParagraphStyle("bullet", parent=ss["body"], leftIndent=14, bulletIndent=3, spaceAfter=3)
ss["bullet2"] = ParagraphStyle("bullet2", parent=ss["bullet"], leftIndent=28, bulletIndent=17)
ss["h1"] = ParagraphStyle("h1", fontName="Arial-Bold", fontSize=20, leading=24, textColor=SLATE, spaceBefore=0, spaceAfter=14)
ss["h1x"] = ParagraphStyle("h1x", parent=ss["h1"])
ss["h2"] = ParagraphStyle("h2", fontName="Arial-Bold", fontSize=13, leading=16, textColor=SLATE, spaceBefore=12, spaceAfter=6)
ss["h3"] = ParagraphStyle("h3", fontName="Arial-Bold", fontSize=10.5, leading=13, textColor=RED, spaceBefore=8, spaceAfter=4)
ss["code"] = ParagraphStyle("code", fontName="Consolas", fontSize=8, leading=10.5, textColor=INK, backColor=PALE,
                            borderPadding=6, leftIndent=6, rightIndent=6, spaceBefore=4, spaceAfter=10)
ss["caption"] = ParagraphStyle("caption", parent=ss["small"], fontName="Arial-Italic", textColor=SUB, alignment=TA_CENTER,
                               spaceBefore=4, spaceAfter=12)
ss["toc1"] = ParagraphStyle("toc1", fontName="Arial-Bold", fontSize=10.5, leading=16, leftIndent=0)
ss["toc2"] = ParagraphStyle("toc2", fontName="Arial", fontSize=9.5, leading=13, leftIndent=18)


class Doc(BaseDocTemplate):
    def __init__(self, fn, **kw):
        super().__init__(fn, pagesize=letter, leftMargin=LM, rightMargin=RM, topMargin=0.95 * inch, bottomMargin=0.85 * inch,
                         title="PeopleSoft Agent: Architecture, Features and Security", author="Velu Angusamy",
                         subject="Technical paper", **kw)
        fr = Frame(LM, self.bottomMargin, TW, H - self.topMargin - self.bottomMargin, id="f")
        self.addPageTemplates([PageTemplate("cover", [Frame(0, 0, W, H, id="c", leftPadding=0, rightPadding=0,
                                                            topPadding=0, bottomPadding=0)], onPage=cover_page),
                               PageTemplate("body", [fr], onPageEnd=body_page)])
        self.chapter = ""

    def afterFlowable(self, f):
        if isinstance(f, Paragraph):
            st = f.style.name
            if st == "h1x":
                self.chapter = f.getPlainText()
            elif st == "h1":
                self.chapter = f.getPlainText()
                self.notify("TOCEntry", (0, f.getPlainText(), self.page))
            elif st == "h2":
                self.notify("TOCEntry", (1, f.getPlainText(), self.page))


def cover_page(c, doc):
    c.saveState()
    c.setFillColor(SLATE)
    c.rect(0, H - 4.9 * inch, W, 4.9 * inch, fill=1, stroke=0)
    c.setFillColor(RED)
    c.rect(0, H - 4.9 * inch - 8, W, 8, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Arial-Bold", 11)
    c.drawString(LM, H - 1.0 * inch, "TECHNICAL PAPER")
    c.setFont("Arial-Bold", 34)
    c.drawString(LM, H - 1.9 * inch, "PeopleSoft Agent")
    c.setFont("Arial", 16)
    c.drawString(LM, H - 2.4 * inch, "An AI assistant that works inside PeopleSoft HCM")
    c.setFont("Arial", 12)
    c.setFillColor(colors.HexColor("#C9D3DD"))
    c.drawString(LM, H - 2.85 * inch, "Architecture  ·  Features  ·  End-to-end flows  ·  Security  ·  Operations")
    c.setFillColor(INK)
    y = H - 5.7 * inch
    rows = [("Product version", "Widget 2026-11-02.5  ·  Agent service, November 2026"),
            ("Tested on", "PeopleSoft HCM 9.2 (HR92U052), PeopleTools 8.61, Oracle"),
            ("AI model", "Claude Opus 5.5 (claude-opus-5-5), Anthropic API"),
            ("Author", "Velu Angusamy"),
            ("Date", "November 2026"),
            ("Status", "Working system; pre-production (see Chapter 7 for the open items)")]
    for k, v in rows:
        c.setFont("Arial-Bold", 10)
        c.setFillColor(SUB)
        c.drawString(LM, y, k)
        c.setFont("Arial", 10)
        c.setFillColor(INK)
        c.drawString(LM + 1.6 * inch, y, v)
        y -= 18
    c.setStrokeColor(LINE)
    c.line(LM, 1.3 * inch, W - RM, 1.3 * inch)
    c.setFont("Arial", 8)
    c.setFillColor(SUB)
    c.drawString(LM, 1.1 * inch, "PeopleSoft is a trademark of Oracle Corporation. Claude is a trademark of Anthropic. This paper describes an")
    c.drawString(LM, 0.95 * inch, "independent project and is not a product of either company.")
    c.restoreState()


def body_page(c, doc):
    c.saveState()
    c.setStrokeColor(RED)
    c.setLineWidth(1.2)
    c.line(LM, H - 0.62 * inch, W - RM, H - 0.62 * inch)
    c.setFont("Arial", 7.8)
    c.setFillColor(SUB)
    c.drawString(LM, H - 0.55 * inch, "PeopleSoft Agent: Architecture, Features and Security")
    if doc.chapter:
        c.drawRightString(W - RM, H - 0.55 * inch, doc.chapter)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.5)
    c.line(LM, 0.6 * inch, W - RM, 0.6 * inch)
    c.drawString(LM, 0.45 * inch, "Technical paper · November 2026")
    c.drawRightString(W - RM, 0.45 * inch, str(doc.page))
    c.restoreState()


# ---------------------------------------------------------------------------------------------------------------
story = []


def P(t, st="body"):
    story.append(Paragraph(t, ss[st]))


def H1(t):
    story.append(NextPageTemplate("body"))
    story.append(PageBreak())
    story.append(Paragraph(t, ss["h1"]))


def H2(t):
    story.append(CondPageBreak(1.4 * inch))
    story.append(Paragraph(t, ss["h2"]))


def H3(t):
    story.append(CondPageBreak(0.9 * inch))
    story.append(Paragraph(t, ss["h3"]))


def B(items, lvl=1):
    for it in items:
        if isinstance(it, (list, tuple)):
            B(it, 2)
        else:
            story.append(Paragraph(it, ss["bullet" if lvl == 1 else "bullet2"], bulletText="•"))


def CODE(t):
    story.append(Paragraph(t.replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br/>").replace("  ", "&nbsp;&nbsp;"), ss["code"]))


def T(rows, widths, head=True, zebra=True, fills=None, fs="cell", extra=()):
    data = []
    for i, r in enumerate(rows):
        data.append([c if not isinstance(c, str) else Paragraph(c, ss["cellh" if head and i == 0 else fs]) for c in r])
    t = Table(data, colWidths=[w * TW for w in widths], repeatRows=1 if head else 0)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.4, LINE),
             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
             ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]
    if head:
        style += [("BACKGROUND", (0, 0), (-1, 0), SLATE)]
    if zebra:
        for i in range(1 if head else 0, len(rows)):
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, i), (-1, i), PALE))
    for f in fills or []:
        style.append(("BACKGROUND",) + f)
    t.setStyle(TableStyle(style + list(extra)))
    story.append(t)
    story.append(Spacer(1, 8))


def NOTE(title, text, fill=BLUEPALE, edge=BLUE):
    t = Table([[Paragraph(f"<b>{title}</b><br/>{text}", ss["small"])]], colWidths=[TW])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), fill), ("LINEBEFORE", (0, 0), (0, -1), 3, edge),
                           ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.append(t)
    story.append(Spacer(1, 10))


def FIG(drawing, caption, heading=None):
    head = [Paragraph(heading, ss["h2"])] if heading else []
    story.append(KeepTogether(head + [drawing, Paragraph(caption, ss["caption"])]))


# ---------------------------------------------------------------------------------------------------------------
# Diagrams
# ---------------------------------------------------------------------------------------------------------------
def arrow(d, x1, y1, x2, y2, color=SLATE, width=1, dash=None, head=5):
    ln = Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=width)
    if dash:
        ln.strokeDashArray = dash
    d.add(ln)
    import math
    a = math.atan2(y2 - y1, x2 - x1)
    p1 = (x2 - head * math.cos(a) + head * 0.55 * math.sin(a), y2 - head * math.sin(a) - head * 0.55 * math.cos(a))
    p2 = (x2 - head * math.cos(a) - head * 0.55 * math.sin(a), y2 - head * math.sin(a) + head * 0.55 * math.cos(a))
    d.add(Polygon([x2, y2, p1[0], p1[1], p2[0], p2[1]], fillColor=color, strokeColor=color, strokeWidth=0.5))


def box(d, x, y, w, h, title, lines=(), fill=colors.white, edge=SLATE, tsize=8.2, lsize=6.8, tcolor=SLATE):
    d.add(Rect(x, y, w, h, rx=4, ry=4, fillColor=fill, strokeColor=edge, strokeWidth=0.8))
    d.add(String(x + 6, y + h - 12, title, fontName="Arial-Bold", fontSize=tsize, fillColor=tcolor))
    for i, ln in enumerate(lines):
        d.add(String(x + 6, y + h - 24 - i * (lsize + 2.6), ln, fontName="Arial", fontSize=lsize, fillColor=INK))


def label(d, x, y, text, size=6.6, color=SUB, anchor="middle", bold=False):
    d.add(String(x, y, text, fontName="Arial-Bold" if bold else "Arial", fontSize=size, fillColor=color, textAnchor=anchor))


def architecture():
    """Three columns (browser, PeopleSoft, service) fitted to the text width TW (about 482 pt)."""
    d = Drawing(TW, 410)
    c1, c2, c3 = 0, 150, 300
    w1, w2, w3 = 132, 132, TW - 300
    box(d, c1, 215, w1, 185, "User's browser", (), fill=PALE, edge=LINE)
    box(d, c1 + 7, 318, w1 - 14, 62, "PeopleSoft pages", ("the user's own signed-in", "session (cookies)"), fill=colors.white)
    box(d, c1 + 7, 222, w1 - 14, 86, "psagent.js widget", ("chat panel and cards,", "runner, recorder,",
                                                          "page snapshots, SOAPTOCI", "and query calls"), fill=BLUEPALE, edge=BLUE)
    box(d, c2, 245, w2, 155, "PeopleSoft (web / app)", (), fill=PALE, edge=LINE)
    items = [("Branding", "loads psagent.js"), ("WEBLIB_PSAGENT", "sign-on token iScript"), ("WEBLIB_SOAPTOCI", "Component Interfaces"),
             ("Query Viewer", "Run to Excel link"), ("Process Monitor", "log files, Report Manager")]
    for i, (a, b) in enumerate(items):
        y = 356 - i * 26
        d.add(Rect(c2 + 7, y, w2 - 14, 22, rx=3, ry=3, fillColor=colors.white, strokeColor=LINE, strokeWidth=0.6))
        d.add(String(c2 + 12, y + 12.5, a, fontName="Arial-Bold", fontSize=6.9, fillColor=SLATE))
        d.add(String(c2 + 12, y + 4, b, fontName="Arial", fontSize=6.2, fillColor=INK))
    box(d, c2, 120, w2, 62, "Oracle database", ("HR92U052, schema SYSADM", "PeopleTools and HCM data",
                                                 "service reads: SELECT only"), fill=colors.white)
    box(d, c3, 120, w3, 280, "Agent service (Python)", (), fill=PALE, edge=LINE)
    mods = ["server.py      HTTP API, roles", "agent.py       chat, tools, cards", "access, auth   roles, tokens", "live, recorder",
            "troubleshoot, security", "approvals, runbooks", "watcher, monitors", "regress, regress_runner",
            "ci, xlsx       Excel to CI", "queries        Ask the data", "db             read-only SQL", "audit          audit log"]
    for i, m in enumerate(mods):
        d.add(String(c3 + 8, 376 - i * 13, m, fontName="Consolas", fontSize=6.4, fillColor=INK))
    box(d, c3 + 7, 127, w3 - 14, 52, "Local files", ("tasks, runbooks, monitors, regression,",
                                                    "ci_loads (YAML, JSON); audit SQLite"), fill=colors.white)
    box(d, c1, 15, w1, 62, "Users", ("USER, MONITOR, ADMIN,", "AUDITOR (PSAGENT_* roles)", "email recipients"), fill=colors.white)
    box(d, c3, 15, 86, 62, "SMTP server", ("alerts, summaries,", "budget, weekly"), fill=colors.white)
    box(d, c3 + 94, 15, w3 - 94, 62, "Claude API", ("Anthropic, HTTPS", "claude-opus-5-5", "tool use, caching"),
        fill=AMBERPALE, edge=colors.HexColor("#B7791F"))
    arrow(d, c1 + w1, 352, c2, 352, color=BLUE)
    arrow(d, c2, 344, c1 + w1, 344, color=BLUE)
    arrow(d, c1 + w1 - 7, 236, c3, 236, color=BLUE, width=1.2)
    arrow(d, c3, 228, c1 + w1 - 7, 228, color=BLUE, width=1.2)
    label(d, c2 + w2 / 2, 216, "HTTPS + signed token (CORS-limited)", 6.2, BLUE)
    arrow(d, c3, 150, c2 + w2, 150, color=RED)
    arrow(d, c3, 262, c2 + w2, 262, color=SLATE, dash=[2, 2])
    cx = c3 + 94 + (w3 - 94) / 2
    arrow(d, cx, 120, cx, 77, color=colors.HexColor("#B7791F"))
    label(d, cx - 4, 98, "prompts, tool results", 5.6, anchor="end")
    arrow(d, c3 + 43, 120, c3 + 43, 77)
    arrow(d, c3, 46, c1 + w1, 46, dash=[2, 2])
    label(d, (c1 + w1 + c3) / 2, 51, "email", 6)
    arrow(d, c1 + 50, 77, c1 + 50, 215)
    label(d, c1 + 56, 150, "work in PeopleSoft", 6, anchor="start")
    return d


def sequence(parts, msgs, step=19, top_pad=34):
    """parts: names; msgs: (a, b, text) with a == b a note at that lifeline; ('--', text) a divider."""
    n = len(parts)
    h = top_pad + 14 + step * len(msgs) + 10
    d = Drawing(TW, h)
    gap = TW / n
    xs = [gap * (i + 0.5) for i in range(n)]
    for i, p in enumerate(parts):
        bw = min(gap - 8, 108)
        d.add(Rect(xs[i] - bw / 2, h - 26, bw, 22, rx=3, ry=3, fillColor=SLATE, strokeColor=SLATE))
        d.add(String(xs[i], h - 18, p, fontName="Arial-Bold", fontSize=7.2, fillColor=colors.white, textAnchor="middle"))
        ln = Line(xs[i], h - 26, xs[i], 6, strokeColor=LINE, strokeWidth=0.8)
        ln.strokeDashArray = [3, 2]
        d.add(ln)
    y = h - top_pad - 10
    for k, m in enumerate(msgs):
        if m[0] == "--":
            d.add(Rect(4, y - 4, TW - 8, 12, fillColor=PALE, strokeColor=None))
            d.add(String(TW / 2, y - 0.5, m[1], fontName="Arial-Italic", fontSize=6.8, fillColor=SUB, textAnchor="middle"))
        else:
            a, b, text = m
            num = f"{sum(1 for x in msgs[:k + 1] if x[0] != '--')}. "
            if a == b:
                tw = pdfmetrics.stringWidth(num + text, "Arial", 6.6) + 10
                x0 = min(max(xs[a] - tw / 2, 2), TW - tw - 2)
                d.add(Rect(x0, y - 5, tw, 13, rx=2, ry=2, fillColor=AMBERPALE, strokeColor=colors.HexColor("#E2C27A"), strokeWidth=0.5))
                d.add(String(x0 + 5, y - 1, num + text, fontName="Arial", fontSize=6.6, fillColor=INK))
            else:
                col = RED if "DB" in parts[b] or "database" in parts[b].lower() else (BLUE if "Claude" not in (parts[a] + parts[b]) else colors.HexColor("#9A6512"))
                arrow(d, xs[a], y - 3, xs[b] + (-3 if b > a else 3), y - 3, color=col, width=0.9, head=4.5)
                mid = (xs[a] + xs[b]) / 2
                tw = pdfmetrics.stringWidth(num + text, "Arial", 6.6)
                d.add(Rect(mid - tw / 2 - 2, y, tw + 4, 8.5, fillColor=colors.white, strokeColor=None))
                d.add(String(mid, y + 1.5, num + text, fontName="Arial", fontSize=6.6, fillColor=INK, textAnchor="middle"))
        y -= step
    return d


# ---------------------------------------------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------------------------------------------
story.append(Spacer(1, 1))          # the cover page is drawn by cover_page
story.append(NextPageTemplate("body"))
story.append(PageBreak())

story.append(Paragraph("About this paper", ss["h1x"]))
P("This paper describes the <b>PeopleSoft Agent</b>: a chat assistant that lives inside PeopleSoft HCM, does work on screen "
  "the way a person does, learns new tasks by watching them done once, investigates failed processes and access problems, "
  "watches the batch schedule overnight, loads spreadsheets through Component Interfaces and answers data questions from "
  "curated PS Queries. It explains how the system is built, what each feature does from end to end, how security is "
  "enforced, and how it is operated, tested and what remains to be done.")
H3("Who should read it")
B(["<b>Architects and PeopleSoft administrators</b> who will install, run or extend the agent;",
   "<b>Security and audit reviewers</b> who need to know what the agent can see and do, as whom, and what is logged;",
   "<b>IT and HR leaders</b> who want to understand the capabilities, the cost and the risks before a rollout."])
H3("How to read the status labels")
T([["Label", "Meaning"],
   ["Proven", "Run on the real PeopleSoft system (HR92U052, PeopleTools 8.61) with real Claude."],
   ["Partly proven", "The main path was run on the real system; some parts only in tests."],
   ["Tested", "Covered by automated tests against mock PeopleSoft pages and a scripted Claude only."]], [0.18, 0.82])
H3("Conventions")
P("Code, settings and PeopleSoft object names are in <font name='Consolas'>monospace</font>. "
  "<i>USER, MONITOR, ADMIN</i> and <i>AUDITOR</i> are short for the PeopleSoft roles PSAGENT_USER, PSAGENT_MONITOR, "
  "PSAGENT_ADMIN and PSAGENT_AUDITOR. \"The service\" is the agent service; \"the widget\" is the browser part.")

story.append(PageBreak())
story.append(Paragraph("Contents", ss["h1x"]))
toc = TableOfContents()
toc.levelStyles = [ss["toc1"], ss["toc2"]]
story.append(toc)

# ===== 1 =====================================================================================================
H1("1  Overview")
H2("1.1  What the agent is")
P("PeopleSoft is powerful but demanding: work is spread over hundreds of components, batch failures need an expert to "
  "read logs and code, and security questions (\"why can't she open this page?\") mean walking a chain of PeopleTools "
  "tables by hand. The PeopleSoft Agent puts an assistant on every PeopleSoft page that can do this work with the user.")
P("It is made of two parts. A JavaScript <b>widget</b> (<font name='Consolas'>psagent.js</font>), loaded through "
  "PeopleSoft Branding, shows a chat panel and acts on the page in the user's own browser session. A Python <b>agent "
  "service</b> holds the conversation with Claude, the catalog of tasks, the rules and the audit log, and reads the "
  "PeopleSoft database read-only. The key idea is that <b>everything that changes PeopleSoft happens in the user's "
  "browser, as the user</b>, so PeopleSoft's own security applies, and nothing is saved without the user's click.")
H2("1.2  Design principles")
P("These six rules apply to every feature. Where it matters, the service enforces them in code rather than relying on "
  "the model's behaviour.")
T([["#", "Principle", "How it is enforced"],
   ["1", "Nothing is saved, submitted or granted without the user's click.", "Confirm cards and approval steps; the service refuses a save action that was not approved."],
   ["2", "Passwords are never seen by the service or Claude.", "Typed on a card in the browser and filled in there; never sent."],
   ["3", "Troubleshooting only reads.", "Read-only database access on three levels; investigations have no write tools."],
   ["4", "Values are never invented.", "Missing or unclear values are asked for or flagged; the service checks fields against the task."],
   ["5", "Page, document, log and database text is data, never instructions.", "Such text reaches Claude only as tool results, never as instructions; tools are narrow and checked."],
   ["6", "Sensitive data stays masked, and every database query is logged.", "Masking by column name; the audit log records every query with who and why."]],
  [0.05, 0.40, 0.55])
H2("1.3  Features at a glance")
T([["#", "Feature", "What the user gets", "Who", "Status"],
   ["1", "Chat widget", "A panel on every page that knows the page and its error", "All roles", "Proven"],
   ["2", "Tasks", "\"Create a user profile for JSMITH\": done on screen after a confirm card", "USER", "Proven"],
   ["3", "Recorder", "Teach a new task by doing it once", "ADMIN", "Proven"],
   ["4", "Live mode", "Work on pages that were never taught", "USER", "Proven"],
   ["5", "Live recovery", "A recorded task finishes even if the page changed", "USER", "Tested"],
   ["6", "Documents", "Attach a form; several people set up from one review table", "USER", "Tested"],
   ["7", "Process troubleshooter", "\"Why did process 557060 fail?\" with root cause and fix", "USER (own), MONITOR", "Proven"],
   ["8", "Security troubleshooter", "\"Why can't ABSMITH open Create Job Opening?\" and the smallest fix", "USER (own), ADMIN", "Proven"],
   ["9", "Absence reviewer", "A recommendation and confidence score for a pending absence", "USER (approver)", "Proven"],
   ["10", "Runbooks", "A checklist turned into a guided, logged run", "ADMIN builds, USER runs", "Proven"],
   ["11", "Watcher", "Watchdogs overnight; every failure investigated; email with the analysis", "MONITOR", "Proven"],
   ["12", "Roles from PeopleSoft", "Four agent roles on the normal user profile", "All", "Proven"],
   ["13", "Regression tests", "Recorded tasks replayed after a patch or PUM image", "ADMIN (test systems)", "Partly proven"],
   ["14", "Feature bar", "+ Features: guided flows by role", "All", "Tested"],
   ["15", "Admin dashboard", "Usage, cost, audit log, failures, refused attempts", "AUDITOR", "Tested"],
   ["16", "Excel to CI", "ExcelToCI without the macro workbook", "USER", "Partly proven"],
   ["17", "Ask the data", "Questions answered from curated AI_ PS Queries", "USER", "Partly proven"]],
  [0.05, 0.19, 0.46, 0.17, 0.13])
H2("1.4  What has been proven on the real system")
B(["<b>Tasks and recorder:</b> Create User Profile, Sync Portal Security and running an Application Engine program; a user profile and a process run recorded and replayed.",
   "<b>Troubleshooting:</b> AEMINITEST 557060 (division by zero located to statement 3 in 21 seconds) and PORTAL_CSS 557046 (three runs at once with Max Concurrent 0).",
   "<b>Security:</b> ABSMITH (smallest role Hiring Manager, 62 components) and VELU (empty row-security table, fixed by a refresh).",
   "<b>Watcher:</b> AEMINITEST 557089 found and analysed in 43 seconds; 60 stuck requests found; a watch plan template read by Claude in 33 seconds.",
   "<b>Excel to CI:</b> all 457 CIs on HR92U052 through template, checks and request building; for 250 of them PeopleSoft found an existing record from exactly the keys the agent sends.",
   "<b>Ask the data:</b> the per-user query catalog, the run link, and a real 11,646-row answer (AI_USERS_WITH_ROLE) asked from the chat."])

# ===== 2 =====================================================================================================
H1("2  Architecture")
H2("2.1  Components")
P("Figure 2-1 shows the parts and how they talk. Three channels matter: the <b>user's browser session</b> with "
  "PeopleSoft (blue, left), the <b>widget-to-service</b> API protected by a signed token (blue, middle), and the "
  "<b>service's own read-only access</b> to the database (red). The service talks to Claude over HTTPS and to an SMTP "
  "server for email.")
FIG(architecture(), "Figure 2-1  Components and channels. Blue: the user's own PeopleSoft session (left) and the token-protected API (middle). Red: the service's SELECT-only database reads. Dashed: log downloads as PSAGENT_SVC, and email.")
H2("2.2  The widget (psagent.js)")
P("A single JavaScript file (about 4,100 lines, no external libraries) loaded on every PeopleSoft page through "
  "Branding. It runs in the page with the user's session, so every PeopleSoft call it makes is made as the user.")
B(["<b>Panel:</b> chat, welcome tiles by role, the context bar (\"You are on ...\" and any error on the page), the activity feed, cards (confirm, diagnosis, review, results), the + Features bar and the watchdog indicator.",
   "<b>Runner:</b> performs a task's steps on classic pages: search pages (Add / Find), fields, grids, tabs, the Process Scheduler pop-up, and checks for Saved or a process instance.",
   "<b>Recorder:</b> records what a person does on a component (fields, clicks, grids, pop-ups, outcome) and sends it to the service to become a task.",
   "<b>Live mode:</b> sends a compact snapshot of the page (fields, buttons, tabs, messages) and performs the next action the service returns.",
   "<b>PeopleSoft calls in the user's session:</b> the sign-on iScript, View Log/Trace files, <font name='Consolas'>WEBLIB_SOAPTOCI</font> for Component Interfaces, and Query Viewer's Run to Excel link.",
   "<b>Secrets stay local:</b> passwords typed on cards are filled into the page directly and never sent to the service."])
H2("2.3  The sign-on iScript (WEBLIB_PSAGENT)")
P("<font name='Consolas'>WEBLIB_PSAGENT.ISCRIPT1.FieldFormula.IScript_PSAgentConfig</font> returns the user's ID, name, "
  "agent roles, the service URL and a signed token. A user with no agent role gets no token and the widget stays hidden. "
  "The token and its checks are described in Chapter 3.")
H2("2.4  The agent service")
P("A Python 3 application (Starlette on uvicorn) with one HTTP API used by the widget. It keeps conversations in "
  "memory, calls Claude with a fixed set of tools, checks every tool call, and writes an audit log. Table 2-1 lists its "
  "modules.")
T([["Module", "Responsibility"],
   ["server.py", "HTTP API, token check, rights per request, refused attempts, start-up settings"],
   ["access.py, auth.py", "The four roles and what each may use; the token format and signature check"],
   ["agent.py", "The conversation with Claude: system prompt, tools, confirm cards, session state"],
   ["llm.py", "The Claude client (Anthropic SDK): effort, prompt caching, error handling, usage to the audit log"],
   ["tasks.py, tasks/*.yaml", "The task catalog: fields, validation, and the screen playbook for each task"],
   ["recorder.py", "Turns a recording into a task, with Claude's suggestions for the field names and questions"],
   ["live.py", "Live mode: the next action from a page snapshot; save actions need approval"],
   ["troubleshoot.py", "Process investigations: request, message log, logs, AE code, PeopleCode, overlapping runs"],
   ["security.py", "Access investigations: the security chain and the least-privilege fix"],
   ["approvals.py", "Absence request reviews: balance, overlaps, team coverage, history"],
   ["runbooks.py", "Drafting runbooks from checklists, checking them, running them step by step"],
   ["monitors.py, watcher.py", "Watchdogs and watch plans; the watcher loop, investigations and emails"],
   ["regress.py, regress_runner.py", "Regression suites and runs; the headless unattended runner"],
   ["ci.py, xlsx.py", "Excel to CI: CI shapes, templates, row checks, SOAP requests, loads and results"],
   ["queries.py", "Ask the data: the user's AI_ query catalog, prompt checks, reading answers, summaries"],
   ["db.py", "Read-only database access: one SELECT, read-only transaction, rollback, limits, masking"],
   ["audit.py", "The audit log in SQLite: events, Claude usage and cost, dashboards, budget and weekly emails"]],
  [0.27, 0.73])
P("Table 2-1  Agent service modules", "caption")
H2("2.5  Claude in the service")
B(["<b>Model:</b> Claude Opus 5.5 (<font name='Consolas'>claude-opus-5-5</font>) through the Anthropic API, with a configurable effort (low for chat, medium for investigations).",
   "<b>Tools, not free text:</b> Claude acts only through tools the service defines (26 in the chat, separate smaller sets for investigations and live mode). Each call is checked by the service before anything happens.",
   "<b>Strict tool schemas</b> make the API guarantee the shape of most tool inputs. The API allows at most 20 strict tools and limits their combined size; the service uses 17, and an automated test plus a live API test keep it within the limits.",
   "<b>Prompt caching:</b> the system prompt and tools are fixed per session, so repeated calls reuse the cache and cost less.",
   "<b>What Claude never receives:</b> passwords; masked sensitive columns; the full rows of a query answer (it gets totals, and at most 25 matching rows without sensitive columns when asked who has a value)."])
H2("2.6  PeopleSoft integration points")
T([["Integration point", "Used for", "As whom"],
   ["Branding (custom JavaScript)", "Loading psagent.js on every page", "Every user"],
   ["WEBLIB_PSAGENT iScript", "Sign-on token with the user's agent roles", "The user"],
   ["Classic pages", "Tasks, live mode, recorder, security Apply", "The user"],
   ["View Log/Trace (Process Monitor)", "Log files for the troubleshooter", "The user (or PSAGENT_SVC overnight)"],
   ["WEBLIB_SOAPTOCI iScript", "Component Interfaces: GetCIShape, Create, Update", "The user"],
   ["Query Viewer Run to Excel link", "Running the user's AI_ queries", "The user"],
   ["Fluid Approvals", "Link from the absence review card", "The user"],
   ["Database (Oracle)", "Read-only lookups: PeopleTools metadata, process requests, security tables, HCM data", "The service's database account"],
   ["PS_MAINTENANCE_LOG", "Detecting a patch to start regression runs", "The service's database account"]],
  [0.30, 0.45, 0.25])
H2("2.7  What the service stores")
T([["Data", "Where", "Kept"],
   ["Tasks, runbooks, watch plans, regression suites", "YAML files: tasks/, runbooks/, monitors/, regression/", "Until changed"],
   ["Excel to CI loads (rows, results)", "ci_loads/ (JSON)", "Until removed"],
   ["Audit log: events, queries, Claude usage", "audit/psagent.db (SQLite)", "90 days (PSAGENT_AUDIT_DAYS)"],
   ["Conversations, query results, CI shapes in use", "Memory only", "Until the service restarts"],
   ["Chat text, Claude's answers, documents, passwords", "Not stored", "Never"]], [0.42, 0.38, 0.20])
H2("2.8  Deployment")
P("In the tested setup the service runs on a Linux host (PeopleSoft's bundled Python works; "
  "<font name='Consolas'>pysqlite3-binary</font> supplies SQLite where that Python lacks it) and is reached by the "
  "browsers over HTTPS. PeopleSoft itself is published through ngrok for testing. For production the recommended setup "
  "is a reverse proxy under the PeopleSoft host name, so the widget and the service share one origin.")
CODE("uvicorn --factory psagent.server:app_from_env --host 0.0.0.0 --port 8000")
P("Settings come from <font name='Consolas'>agent-service/.env</font> (Section 6.2). The service refuses to start "
  "without the shared secret, and refuses to start if the development user is also set (Section 3.4).")

# ===== 3 =====================================================================================================
H1("3  Security")
H2("3.1  Trust model: who acts as whom")
T([["Actor", "Identity", "Can do"],
   ["The widget", "The signed-in PeopleSoft user, in their browser session", "Everything the user can do in PeopleSoft, after the user's click for any save"],
   ["The agent service", "Its database account (read-only use) and its own secret", "Read PeopleSoft data; never write; decide which agent features a user may use"],
   ["Claude", "None in PeopleSoft", "Propose tool calls; every call is checked by the service; no direct access to anything"],
   ["PSAGENT_SVC", "A PeopleSoft service user", "Download posted log files for the overnight watcher (Process Monitor and Report Manager only)"],
   ["PSAGENT_TEST_USER (optional)", "A PeopleSoft test user", "Unattended regression runs on test systems"]],
  [0.25, 0.33, 0.42])
NOTE("Why the browser does the work",
     "Saving in PeopleSoft from the browser, in the user's session, means the user's page permissions, CI permissions, "
     "query security and row security all apply exactly as when they work by hand, and PeopleSoft's own audit fields show "
     "the real user. The service never holds a PeopleSoft password for the user and has no way to save on their behalf.")
H2("3.2  Sign-on and the token")
FIG(sequence(["User", "Browser / widget", "PeopleSoft iScript", "Agent service"],
             [(0, 1, "signs in to PeopleSoft as usual"),
              (1, 2, "IScript_PSAgentConfig (session cookie)"),
              (2, 2, "IsUserInRole(PSAGENT_*); sign"),
              (2, 1, "{oprid, roles, agentUrl, token}"),
              (1, 3, "every call: Authorization: Bearer token"),
              (3, 3, "check signature, date, roles"),
              (3, 1, "answer, or 401 when expired"),
              (1, 2, "on 401: fetch a new token")]),
    "Figure 3-1  Sign-on: the token carries the user's identity and agent roles")
P("The token is <font name='Consolas'>OPRID.YYYYMMDD.ROLES.signature</font>, where the signature is SHA-256 of "
  "<font name='Consolas'>secret|OPRID|YYYYMMDD|ROLES</font> computed by PeopleCode "
  "(<font name='Consolas'>HashSHA256</font>). The secret exists only on the PeopleSoft application server and on the "
  "agent service. The service recomputes the signature, accepts the day of issue (plus or minus one day for time zones), "
  "and restricts the user ID to printable characters without the separator, which also blocks length-extension forgeries.")
NOTE("Known limitation: token lifetime",
     "A token is valid for the calendar day. Until the browser fetches a new one (on page load or after a 401), it carries "
     "the roles the user had when it was issued, and a copied token works for the rest of the day. A shorter expiry (for "
     "example one hour, with the widget's silent refresh) is a small iScript change and is recommended before production.",
     fill=AMBERPALE, edge=colors.HexColor("#B7791F"))
H2("3.3  Roles")
P("Access to the agent is managed on the normal PeopleSoft user profile with four roles. USER, MONITOR and ADMIN build "
  "on each other; AUDITOR stands apart so that administrators do not audit themselves (a person may hold both).")
Y = "<para alignment=\"center\">●</para>"
T([["Feature", "USER", "MONITOR", "ADMIN", "AUDITOR"],
   ["Chat, explain a page or an error", Y, Y, Y, Y],
   ["Run recorded tasks, live mode, documents", Y, Y, Y, ""],
   ["Run published runbooks", Y, Y, Y, ""],
   ["Ask the data (AI_ queries)", Y, Y, Y, ""],
   ["Excel to CI", Y, Y, Y, ""],
   ["Absence reviewer, own approvals", Y, Y, Y, ""],
   ["Troubleshoot own processes", Y, Y, Y, ""],
   ["Troubleshoot anyone's processes, with Claude's own read-only queries", "", Y, Y, ""],
   ["Watcher: watchdogs, alerts, manage", "", Y, Y, ""],
   ["Check my own access", Y, Y, Y, ""],
   ["Check anyone's access, and Apply a fix", "", "", Y, ""],
   ["Record tasks, publish runbooks", "", "", Y, ""],
   ["Regression tests (test systems)", "", "", Y, ""],
   ["Everyone's absence requests", "", "", Y, ""],
   ["Admin dashboard and audit log", "", "", "", Y]],
  [0.56, 0.11, 0.11, 0.11, 0.11], extra=[("ALIGN", (1, 1), (-1, -1), "CENTER")])
H2("3.4  How access is enforced")
B(["<b>On every request, by the service.</b> Each request's rights are computed from its signed token "
   "(<font name='Consolas'>access.py</font>), not remembered from the first message. A refused feature answers 403; a "
   "refused tool tells Claude it is not allowed, and Claude tells the user which role is needed. The widget only hides "
   "what a user cannot use; hiding is not the control.",
   "<b>Ownership checks inside features.</b> A USER may troubleshoot only their own processes and check only their own "
   "access; other users' runs of the same process are shown without the user and run control. Investigations for a "
   "USER use fixed lookups only; Claude's own read-only queries are for MONITOR and ADMIN.",
   "<b>No role, no agent.</b> A token without any agent role is refused, and the iScript gives such a user no token.",
   "<b>Development mode cannot reach production.</b> With <font name='Consolas'>PSAGENT_DEV_OPRID</font> set, requests "
   "without a token run as that user. The service refuses to start if it is set together with the shared secret, and "
   "the development lists of teachers, monitors and auditors are ignored outside development."])
H2("3.5  PeopleSoft's own security still applies")
P("The agent role decides which features a user sees. Inside each feature PeopleSoft decides what the user can do: "
  "tasks and live mode use the user's page permissions; Excel to CI uses their Component Interface permissions; Ask the "
  "data offers only queries whose every record is in the user's query trees, and PeopleSoft applies their row security "
  "when it runs the query. The agent cannot give a user more access than PeopleSoft does.")
H2("3.6  Protecting data")
B(["<b>Passwords</b> are typed on a card and filled into the page by the browser; they are not sent to the service, Claude or the audit log.",
   "<b>Sensitive columns</b> (national IDs, birth dates, pay rates and amounts, bank details, passwords) are masked in every database result by column name before Claude sees it.",
   "<b>Ask the data</b> sends Claude the row count and per-column counts and totals; to name who has a value it sends at most 25 matching rows, never sensitive columns, and refuses to filter or total on them.",
   "<b>Chat text and answers are not stored</b>; the audit log records that a message was sent, not what it said. Documents are read and discarded.",
   "<b>Prompt injection:</b> page text, documents, logs and data reach Claude as tool results, never as instructions; tools are narrow; a save needs the user's click enforced by the service; the database is read-only."])
H2("3.7  Read-only database access")
P("Even with a privileged database account, the service cannot write. Every statement is checked to be a single "
  "SELECT or WITH; every session runs <font name='Consolas'>SET TRANSACTION READ ONLY</font>; every query is rolled "
  "back. Results are capped at 200 rows and 20 seconds, and every query is logged with the user and the reason. A "
  "dedicated SELECT-only account (<font name='Consolas'>PSAGENT_RO</font>) is planned to replace SYSADM.")
H2("3.8  Audit and refused attempts")
P("Every Claude call (tokens, estimated cost), database query (SQL, row count), task run (confirmed values), save, "
  "publish, watchdog change, investigation, alert and regression run is recorded with the user and the feature. "
  "Refused attempts are recorded as <font name='Consolas'>access.denied</font>: a feature or tool used without the "
  "role, another user's process, access, query or absence request, a token without an agent role, and a forged or "
  "malformed token. The dashboard counts them and the audit log filters them. Expired tokens are normal and not counted.")
H2("3.9  Threats and mitigations")
T([["Threat", "Mitigation", "Residual risk"],
   ["Someone calls the service as another user", "Signed token; secret only on the app server and service; 401 and an audit entry", "A leaked token works until the end of the day"],
   ["A user reaches a feature their role does not allow", "Rights computed per request from the token; tools refused by the service", "Role changes apply when a new token is fetched"],
   ["Claude is tricked by page, log or document text", "Text is data; narrow, checked tools; saves need the user's click; read-only database", "Claude may give a wrong answer; nothing is changed without the user"],
   ["The service writes to the database", "Single SELECT, read-only transaction, rollback", "SYSADM account until PSAGENT_RO exists"],
   ["Sensitive data reaches Claude", "Masking by column name; totals instead of rows; documents discarded", "A sensitive column with an unusual name is not recognised"],
   ["A user runs a query outside their query security", "Catalog from query trees; links built only for catalog queries; PeopleSoft row security", "Query Viewer's link itself does not check query trees (to be retested)"],
   ["Development mode left on in production", "Start-up refused when the dev user and the secret are both set", "None known"],
   ["Another website calls the service", "CORS limited to the PeopleSoft origin; token required", "None known"],
   ["Runaway Claude cost", "Cost per call in the audit log; monthly budget emails; cap on overnight investigations; prompt caching", "Budget emails need SMTP"],
   ["Service host compromised", "Secrets only in .env; read-only use of the database", "Keys in a file, not a vault; SYSADM account"]],
  [0.27, 0.43, 0.30])

# ===== 4 =====================================================================================================
H1("4  Features")
P("Each section gives the purpose, how the feature works, who can use it, its safeguards and what has been proven. "
  "Chapter 5 shows the main features as end-to-end sequence diagrams.")


def feature(num, name, who, status, claude, purpose, how, safeguards, proven):
    H2(f"4.{num}  {name}")
    T([["Who", who, "Status", status], ["Claude used", claude, "", ""]], [0.13, 0.47, 0.12, 0.28], head=False, zebra=False,
      fills=[((0, 0), (0, -1), PALE), ((2, 0), (2, 0), PALE)], fs="cell", extra=[("SPAN", (1, 1), (3, 1))])
    P(purpose)
    if how:
        H3("How it works")
        B(how)
    if safeguards:
        H3("Safeguards")
        B(safeguards)
    if proven:
        H3("Proven")
        P(proven)


feature(1, "Chat widget", "Every user with an agent role", "Proven", "For chat answers",
        "A wide panel docked on the right of every PeopleSoft page, opened with Ctrl+/ or the launcher.",
        ["<b>Welcome screen:</b> tiles for what the user can do (by role and tasks), recent questions, overnight alerts for monitors.",
         "<b>Context bar:</b> the page the user is on and any error shown on it, with <i>Explain this page</i> and <i>Help with this error</i>.",
         "<b>Activity feed and avatar:</b> every step the agent takes, with timings; ready, thinking, working, waiting, stopped.",
         "<b>History:</b> kept for the browser session; wide mode for large cards."],
        ["Hidden for users without an agent role (no token)."],
        "Loaded through Branding on HR92U052; sign-on through the WEBLIB_PSAGENT token.")
feature(2, "Tasks: the agent does the work", "USER", "Proven", "To collect the values; no Claude during the run",
        "<i>\"Create a user profile for JSMITH, roles EMPLOYEE and Manager.\"</i> A task is a recorded PeopleSoft "
        "procedure with its fields and screen steps (tasks/*.yaml).",
        ["Claude picks the task and collects the values in the chat, asking only for what is missing.",
         "The service validates every value against the task definition.",
         "A <b>confirm card</b> lists everything; passwords are typed on the card.",
         "After <b>Confirm</b> the widget performs the steps on screen: search or add, fields, grids, tabs, the Process Scheduler pop-up.",
         "It stops on any PeopleSoft error and explains it; otherwise it reports Saved or the process instance."],
        ["Nothing runs before Confirm; changing a value withdraws the card.", "IDs, roles and permission lists are never invented.",
         "The run is in the user's session with their permissions."],
        "Create User Profile, Sync Portal Security and Run an Application Engine program on HR92U052; about $0.02 per run.")
feature(3, "Recorder: teach a task by doing it once", "ADMIN", "Proven", "Once, to suggest field names and questions",
        "An administrator clicks <b>Record</b>, does the task once by hand, and the agent turns the recording into a reusable task.",
        ["Understands search pages (Add / Find / already exists), transaction pages, grids, pop-ups such as the Process Scheduler request, and the outcome.",
         "Leaves out edits the user undid and clicks that did nothing.",
         "Claude suggests field names, which fields to ask for and which are fixed or optional; the teacher reviews and saves.",
         "The task is available at once, to every USER allowed by PeopleSoft to use that page."],
        ["Only ADMIN may record or overwrite tasks; the recorded task never stores passwords."],
        "A user profile and a process run recorded and replayed on the real system.")
feature(4, "Live mode: pages it was never taught", "USER", "Proven", "One call per page step",
        "When no task fits, or the user says <i>\"do it live\"</i>, Claude operates PeopleSoft one page at a time.",
        ["The widget sends a snapshot of the page: fields, buttons, tabs, messages and pop-ups.",
         "Claude chooses the next action; the widget performs it and sends the next snapshot.",
         "Missing values are asked for; passwords go on a card."],
        ["Any action that saves, submits, deletes or posts needs the user's approval click, enforced by the service.",
         "Classic pages only today."],
        "On real classic pages; about $0.015 to $0.02 per step.")
feature(5, "Live recovery", "USER", "Tested", "When a recorded step fails",
        "If a page has changed since a task was recorded (a moved field, a new tab), the recorded run hands over to live mode at the failed step.",
        ["Live mode finishes the task; the save is approved again by the user.", "The agent then offers to update the task from what it learned."],
        None, "Browser test with a moved field; not yet seen on a real page change.")
feature(6, "Documents: a form fills PeopleSoft", "USER", "Tested", "To read the document",
        "Attach a PDF, an image or a spreadsheet, such as an access request form listing several people.",
        ["Claude reads the document and maps it to the right task.",
         "Handwritten, ambiguous or described values are flagged for checking.",
         "Several people appear in one review table; the user fixes, fills in or unticks rows and the service checks every edit.",
         "Rows run one by one with a result each."],
        ["Documents are read and discarded, never stored."], "Browser test with a two-person access form.")
feature(7, "Process troubleshooter", "USER (own processes); MONITOR and ADMIN (anyone's)", "Proven", "An investigation of 20 to 60 seconds",
        "<i>\"Why did process 557060 fail?\"</i> or <i>\"why did my last process fail?\"</i> The agent investigates like a PeopleSoft administrator, read-only.",
        ["<b>Log files</b> (stdout, .log, .trc) read in the user's browser session, the same files as View Log/Trace; only the end of very large files.",
         "<b>Request details:</b> parameters (passwords hidden), the message log with the text filled in, earlier runs.",
         "<b>Code:</b> Application Engine sections, steps and SQL; PeopleCode with statement numbers matching \"Statement:n\"; a search of all PeopleCode for functions defined elsewhere, including delivered code.",
         "<b>Runs at the same time:</b> overlapping runs, Max Concurrent, restart settings, mutual exclusion.",
         "<b>Diagnosis card:</b> category and confidence, exact place, root cause, evidence, fix steps and corrected code, how to rerun, every query it ran, Copy report."],
        ["A USER may investigate only their own processes; Claude's own queries are for MONITOR and ADMIN.", "Nothing is rerun or changed."],
        "AEMINITEST 557060 (division by zero at statement 3, 21 seconds) and PORTAL_CSS 557046 (three runs at once, Max Concurrent 0).")
feature(8, "Security access troubleshooter", "USER (own access); ADMIN (anyone's, and Apply)", "Proven", "An investigation of about 25 seconds",
        "<i>\"Why can't ABSMITH access Create Job Opening?\"</i> The agent walks the security chain from the PeopleTools tables.",
        ["The page to its component (MENU.COMPONENT.MARKET); the user profile (exists, locked), roles and permission lists.",
         "The component's pages and actions against what the permission lists authorise; the menu link permission.",
         "Row security on the search record, including the menu item's override; HCM row security (SJT tables).",
         "<b>Least-privilege fix:</b> every role that would grant the access, with how many other pages it would also give and how many users it affects.",
         "<b>Apply</b> (ADMIN): a live run makes exactly that change and stops for approval before Save."],
        ["Nothing is ever granted automatically.", "A USER can check only their own access."],
        "ABSMITH lacks HCCPRS1040; the smallest role, Hiring Manager, adds 62 components against 81 to 178 for the others. VELU has an empty SJT_OPR_CLS: the fix is a refresh, not a new role.")
feature(9, "Absence approval reviewer", "USER (their approvals); ADMIN (everyone's)", "Proven", "A review of 30 to 45 seconds",
        "The approver lists the absence requests waiting for them and clicks <b>Review with AI</b>.",
        ["Checks from the database, read-only: the request, the entitlement balance, overlaps, team coverage under the same manager, timing, employment status and history.",
         "The card shows PeopleSoft's approval status, a recommendation (approve, send back, deny, needs your review), a confidence score 0 to 100 with reasons, each check and a suggested comment."],
        ["The agent never approves; the approver decides in PeopleSoft (Open Approvals).", "An approval despite a failed check is capped at 50."],
        "Request 1589: all checks fine but the date 8 years ago, so \"needs your review\" at 65/100, in 42 seconds.")
feature(10, "Runbooks from a checklist", "ADMIN builds and publishes; USER runs", "Proven", "To draft; not during a run",
         "An administrator attaches the checklist they already have (Excel, CSV or PDF) and asks for a runbook.",
         ["Claude turns each row into a step: a check (read-only query), a task or process from the catalog, an approval or a manual step; values that change each run become inputs.",
          "The service checks tasks and fields exist, stores no passwords, and test-runs every check query right away.",
          "<b>Publish</b> saves runbooks/&lt;id&gt;.yaml; a <i>Run:</i> tile appears.",
          "A run asks for the inputs, then: checks run on the service, tasks show their confirm card, processes are followed in Process Monitor, approvals and manual steps wait for a click."],
         ["The run stops at the first problem; every run is logged with who, inputs and each step's result."],
         "A 5-step checklist drafted by real Claude in 30 seconds; a process followed to Success (PORTAL_CSS 557093).")
feature(11, "Watcher: watchdogs and alert emails", "MONITOR", "Proven", "Only to investigate a new failure",
         "Watchdogs run on the service day and night, with nobody signed in.",
         ["<b>Four kinds:</b> failure (Error or No Success), stuck (still queued N minutes after the run time), must succeed by (a time of day; catches a job that never ran), and data check (a read-only query returning wrong rows).",
          "Set up from an Excel template (watch plans) or one sentence in the chat; a preview shows what each would have caught in the last 30 days.",
          "Every minute: one read-only query per due watchdog, no Claude.",
          "Every new failure is investigated by the troubleshooter, with log files downloaded as PSAGENT_SVC; repeats within 12 hours are counted, not re-investigated; investigations are capped per day.",
          "The alert email gives what happened, the analysis and the action needed, with links; a daily summary goes to the team."],
         ["The watcher only reads; nothing is rerun or changed."],
         "AEMINITEST 557089 found and analysed in 43 seconds; 60 stuck requests found; a 4-row plan read by Claude in 33 seconds.")
feature(12, "Roles from PeopleSoft", "Everyone", "Proven", "No",
        "The four agent roles are ordinary PeopleSoft roles on the user profile, signed into the token (Chapter 3).",
        None, None, "USER, MONITOR and ADMIN on HR92U052; the AUDITOR role and the updated iScript are to be deployed.")
feature(13, "Regression tests", "ADMIN, test systems only", "Partly proven", "Only to finish a case live when a page changed",
         "Every recorded task is an exact replay, so on a test system it becomes a regression test after a patch or a PUM image.",
         ["A smoke test of every task with no setup; or a test plan from an Excel template with expected results (Saved, Process submitted, or an expected Error for negative tests).",
          "Results: Passed; Passed with changes (the page changed and the case was finished live, with the change listed); Failed.",
          "Compared with the last run; reports as Excel; run in the browser or unattended in a headless browser on the service, daily or when PS_MAINTENANCE_LOG changes."],
         ["Off unless PSAGENT_TEST_ENV=on, because the cases really save data."],
         "Browser tests of all three results; the headless runner signed in to PeopleTools 8.61.")
feature(14, "Feature bar", "Everyone, by role", "Tested", "Depends on the flow",
        "<b>+ Features</b> under the message box lists the guided flows the user's roles allow: watch jobs, manage watchdogs, "
        "regression tests, runbooks, troubleshoot, check access, absence approvals, record a task, ask the data, load with a CI, audit.",
        None, None, None)
feature(15, "Admin dashboard and audit log", "AUDITOR", "Tested", "Only when an auditor asks about it in the chat",
        "Opens on the left of the screen with the chat on the right.",
        ["<b>Overview:</b> people, conversations, task runs, Claude cost against the period before, failures, refused attempts, alerts, the month's spend against the budget, cost per day, by feature and top users.",
         "<b>Usage and cost</b> by user or feature; <b>audit log</b> with filters (user, feature, kind, outcome, dates, words) and the SQL or confirmed values per row; <b>failures</b>; <b>watcher and tests</b>. Exports to Excel.",
         "Budget emails at 80% and 100% of PSAGENT_BUDGET; a weekly usage email."],
        ["Chat text, answers and passwords are not kept; events are kept 90 days.", "Cost is an estimate from tokens at list prices."],
        "Browser test with 30 days of seeded history; real data starts after the service restart.")
feature(16, "Excel to CI", "USER (PeopleSoft's CI permissions decide which CIs)", "Partly proven", "No (only \"Explain the errors\")",
        "ExcelToCI's job without the macro workbook: pick a Component Interface, download a template, upload it filled in, and load.",
        ["The CI's shape is read with GetCIShape through the delivered WEBLIB_SOAPTOCI iScript (as ExcelToCI's \"New Template\"), plus PeopleTools metadata: prompt tables, translate values, Yes/No, required fields, create and get keys.",
         "The template: the CI's sheet (Action, keys, chosen properties), one sheet per collection linked by the parent keys, a Help sheet, drop-downs.",
         "Every row is checked before anything is saved: keys, required fields, lengths, numbers, dates and times, translate and prompt values (read-only queries), Create of an existing key, Update of a missing one, duplicates, orphans.",
         "The browser sends each row to WEBLIB_SOAPTOCI in the user's session; results per row; Pause, Stop, Resume; the rows that failed download as a template to fix."],
        ["Saved as the user with their CI permissions; the CI's own PeopleCode applies.", "Passwords are never in the file.", "Every row is in the audit log."],
        "All 457 CIs on HR92U052 through template, checks and requests; the update keys found existing records for 250 CIs (read-only); formats confirmed from the delivered SOAPTOCI code. No real Create or Update has been run yet.")
feature(17, "Ask the data", "USER (their AI_ queries and row security)", "Partly proven", "Two to three chat turns per question",
        "<i>\"How many new hires this year by department?\"</i> answered only from PS Queries named AI_..., which an "
        "administrator writes in Query Manager. The agent never writes SQL for data questions.",
        ["The catalog: public AI_ queries and the user's own private ones whose every record is in the user's query trees.",
         "Claude picks the query and fills its prompts from the question; the service checks every value (dates as YYYY-MM-DD, translate and prompt values).",
         "The browser runs the query with Query Viewer's Run to Excel link in the user's session; PeopleSoft applies row security.",
         "The service reads the answer; the user sees a card with the rows (first 200), a chart, Download Excel and Open in Query Viewer.",
         "Claude gets totals; for \"who has X\" it filters the whole result and gets at most 25 matching rows without sensitive columns."],
        ["Links are built only for queries in the user's catalog.", "Sensitive columns are never shown to Claude, totalled or filtered on.", "Results stay in memory for the conversation."],
        "The catalog per user, the link with prompts, the date format and real answers (324 and 11,646 rows).")

# ===== 5 =====================================================================================================
H1("5  End-to-end flows")
P("The diagrams use five colours of arrow: blue between the user, the widget, PeopleSoft and the service; brown to and "
  "from Claude; red for the service's read-only database queries. Yellow boxes are work done inside one participant.")
FIG(sequence(["User", "Widget", "Agent service", "Claude", "PeopleSoft"],
             [(0, 1, "\"create a user profile for TSTUSR01\""),
              (1, 2, "POST /api/chat (token, page)"),
              (2, 3, "conversation + tools"),
              (3, 2, "start_task, set_fields"),
              (2, 2, "validate values"),
              (3, 2, "request_confirmation"),
              (2, 1, "confirm card"),
              (0, 1, "password on card, Confirm"),
              (1, 2, "POST /api/confirm (no password)"),
              (2, 1, "playbook: steps and values"),
              (1, 4, "steps on screen, as the user"),
              (4, 1, "Saved / error"),
              (1, 2, "POST /api/result"),
              (2, 1, "short answer")]),
    "Figure 5-1  A recorded task from request to Saved", heading="5.1  A recorded task")
FIG(sequence(["User", "Widget", "Agent service", "Claude", "PeopleSoft"],
             [(0, 1, "\"do it live: ...\""),
              ("--", "repeat until the goal is reached"),
              (1, 4, "read the page"),
              (1, 2, "POST /api/live/step (snapshot)"),
              (2, 3, "snapshot + live tools"),
              (3, 2, "next action"),
              (2, 2, "save? approval required"),
              (2, 1, "action (or ask the user)"),
              (0, 1, "approve the save"),
              (1, 4, "perform the action")]),
    "Figure 5-2  Live mode: one Claude call per page step; saves need approval", heading="5.2  Live mode")
FIG(sequence(["User", "Widget", "Agent service", "Claude", "DB (read-only)"],
             [(0, 1, "\"why did process 557060 fail?\""),
              (1, 2, "POST /api/chat"),
              (2, 2, "owner check (USER: own only)"),
              (2, 1, "log files to fetch"),
              (1, 1, "View Log/Trace, as the user"),
              (1, 2, "POST /api/troubleshoot/step (logs)"),
              ("--", "investigation loop"),
              (2, 3, "request, message log, logs"),
              (3, 2, "get_ae_program / get_peoplecode"),
              (2, 4, "read-only lookups"),
              (3, 2, "diagnosis"),
              (2, 1, "diagnosis card")]),
    "Figure 5-3  Investigating a failed process", heading="5.3  Process troubleshooting")
FIG(sequence(["Watcher", "DB (read-only)", "PeopleSoft", "Claude", "SMTP / users"],
             [("--", "every minute, nobody signed in"),
              (0, 1, "one query per due watchdog"),
              (1, 0, "new failed request 557089"),
              (0, 0, "new? within daily cap?"),
              (0, 2, "log files as PSAGENT_SVC"),
              (0, 3, "investigation (troubleshooter)"),
              (3, 0, "diagnosis: cause, action"),
              (0, 4, "alert email with links"),
              ("--", "next morning"),
              (4, 4, "welcome screen: N alerts")]),
    "Figure 5-4  A failure found, investigated and emailed overnight", heading="5.4  The watcher overnight")
FIG(sequence(["User", "Widget", "Agent service", "PeopleSoft", "DB (read-only)"],
             [(0, 1, "+ Features > Load with a CI"),
              (1, 2, "GET /api/ci, /api/ci/shape"),
              (1, 3, "SOAPTOCI GetCIShape (as user)"),
              (1, 2, "shape; template requested"),
              (2, 0, "template .xlsx"),
              (0, 2, "upload filled template"),
              (2, 4, "keys, prompt values"),
              (2, 1, "review: ready / problems"),
              ("--", "Load: for each ready row"),
              (1, 2, "POST /api/ci/next"),
              (1, 3, "SOAPTOCI Create/Update (as user)"),
              (1, 2, "POST /api/ci/result"),
              (2, 1, "results, rows to fix")]),
    "Figure 5-5  Excel to CI: no Claude in the checks or the load", heading="5.5  Excel to CI")
FIG(sequence(["User", "Widget", "Agent service", "Claude", "PeopleSoft"],
             [(0, 1, "\"who has the PSAGENT_ADMIN role?\""),
              (1, 2, "POST /api/chat"),
              (2, 3, "conversation + tools"),
              (3, 2, "find_queries"),
              (2, 2, "catalog from query trees (DB)"),
              (3, 2, "run_query(AI_USERS_WITH_ROLE)"),
              (2, 1, "checked link and prompts"),
              (1, 4, "Run to Excel, as the user"),
              (4, 1, "xlsx (row security applied)"),
              (1, 2, "POST /api/query/result"),
              (2, 3, "totals only"),
              (3, 2, "find_rows(Role = PSAGENT_ADMIN)"),
              (2, 3, "matches (max 25, no sensitive)"),
              (2, 1, "answer + matching rows card")]),
    "Figure 5-6  Ask the data: Claude picks the query; PeopleSoft runs it as the user", heading="5.6  Ask the data")

# ===== 6 =====================================================================================================
H1("6  Operations")
H2("6.1  Installing and deploying")
T([["Step", "What", "Detail"],
   ["1", "Agent service", "Copy agent-service/ to the host; pip install -r requirements.txt; create .env (6.2); start with uvicorn."],
   ["2", "Sign-on iScript", "Create WEBLIB_PSAGENT.ISCRIPT1 FieldFormula from psagent_iscript; set &AGENT_URL and &AGENT_SECRET (= PSAGENT_SHARED_SECRET); grant the web library to the users' permission lists."],
   ["3", "Widget", "Upload psagent.js through Branding (custom JavaScript object) so it loads on every page; check PSAgent.version in the browser console."],
   ["4", "Roles", "Create PSAGENT_USER, PSAGENT_MONITOR, PSAGENT_ADMIN, PSAGENT_AUDITOR and give them to users."],
   ["5", "Feature setup", "WEBLIB_SOAPTOCI and CI methods for Excel to CI; AI_ queries for Ask the data; PSAGENT_SVC for log downloads; SMTP for email."],
   ["6", "Verify", "Start-up log clean; the live API test passes; run the end-to-end test workbook (6.4)."]], [0.07, 0.18, 0.75])
H2("6.2  Configuration")
T([["Setting", "Purpose", "Default"],
   ["ANTHROPIC_API_KEY", "Claude API key", "required"],
   ["PSAGENT_SHARED_SECRET", "Same value as &AGENT_SECRET in the iScript", "required"],
   ["PSAGENT_ALLOWED_ORIGINS", "PeopleSoft address(es) allowed to call the service (CORS)", "none (same origin)"],
   ["PSAGENT_MODEL, PSAGENT_EFFORT", "Claude model and effort for chat", "claude-opus-5-5, low"],
   ["PSAGENT_TROUBLESHOOT_EFFORT", "Effort for investigations", "medium"],
   ["PSAGENT_DB_DSN, _USER, _PASSWORD, _SCHEMA", "Read-only database access (troubleshooting, watcher, CI checks, Ask the data)", "off when empty"],
   ["PSAGENT_CI, PSAGENT_QUERIES, PSAGENT_WATCH, PSAGENT_AUDIT", "Feature switches", "on"],
   ["PSAGENT_QUERY_PREFIX", "Name prefix of curated queries", "AI_"],
   ["PSAGENT_PS_URL", "PeopleSoft address for links and the service user's sign-in", ""],
   ["PSAGENT_SVC_USER, _PASSWORD", "Service user for overnight log downloads", ""],
   ["PSAGENT_SMTP_*, PSAGENT_ALERT_TO, PSAGENT_AUDIT_TO", "Email: server and recipients", "no email"],
   ["PSAGENT_WATCH_MAX_INVESTIGATIONS, _SUMMARY", "Daily investigation cap; daily summary time", "20, 07:00"],
   ["PSAGENT_AUDIT_DAYS, PSAGENT_BUDGET, PSAGENT_AUDIT_WEEKLY", "Audit retention; monthly budget in USD; weekly email", "90, none, Mon 08:00"],
   ["PSAGENT_TEST_ENV, PSAGENT_TEST_USER, _PASSWORD", "Regression tests (test systems only); unattended runs", "off"],
   ["PSAGENT_DEV_OPRID, PSAGENT_DEV_ROLES", "Development only: run without a token. Refused with the secret.", "unset"]],
  [0.40, 0.42, 0.18])
H2("6.3  Cost")
P("Measured on Claude Opus 5.5 at list prices. Recorded tasks, the watcher's routine checks, the audit dashboard and "
  "Excel to CI loads use little or no Claude.")
T([["Action", "Claude cost", "Time"],
   ["Recorded task run", "about $0.02", "seconds"],
   ["Live mode, per page step", "about $0.015 to $0.02", ""],
   ["Process investigation", "a few tens of cents", "20 to 60 s"],
   ["Security access check / absence review", "similar to an investigation", "25 to 45 s"],
   ["Runbook draft / watch plan from a template", "a few cents to tens of cents", "about 30 s"],
   ["Watcher, routine check", "none (one read-only query)", ""],
   ["Watcher, new failure", "like an investigation", "about 45 s"],
   ["Ask the data", "two to three chat turns", ""],
   ["Excel to CI load", "none", "about 1 s per row"],
   ["Admin dashboard", "none (local SQLite)", ""]], [0.50, 0.32, 0.18])
H2("6.4  Testing and quality")
T([["Level", "What it covers", "Count"],
   ["Unit tests", "Every module, each role through the API, refused attempts, masking, prompts, CI checks, 457 real CI shapes", "232"],
   ["Live API test", "The real Claude API accepts every tool list (strict-tool and grammar limits); run on request", "1 (in the 232)"],
   ["Browser tests", "The widget end to end against mock PeopleSoft pages, a mock SOAPTOCI and a scripted Claude", "23"],
   ["End-to-end test workbook", "Manual tests on the real system with real Claude, by role (PSAgent_E2E_Test_Cases.xlsx)", "110 cases (52 P1)"]],
  [0.22, 0.60, 0.18])
NOTE("Why the manual end-to-end tests still matter",
     "Automated tests use a scripted Claude and mock pages, so they cannot see limits of the real API, real PeopleSoft "
     "behaviour or real data. One example: the real API refused a request with too many strict tools, which no unit test "
     "could detect; the live API test now covers it.")
H2("6.5  Running the service")
B(["<b>Start-up log:</b> lists the allowed origins, the roles model, the database, the features that are on, the loaded runbooks and warnings for unused or development-only settings.",
   "<b>Audit dashboard:</b> usage, cost and failures; refused attempts show misconfigured roles or probing.",
   "<b>Scaling:</b> one worker with in-memory sessions today; shared sessions (Redis) and several workers are planned."])

# ===== 7 =====================================================================================================
H1("7  Limitations and roadmap")
H2("7.1  Open items before production")
T([["Item", "Why it matters"],
   ["Dedicated read-only database account (PSAGENT_RO)", "The service uses SYSADM today; read-only is enforced by the service, but the account should not be able to write at all."],
   ["Shorter token lifetime", "Tokens last a day; one hour with silent refresh makes role changes and leaked tokens expire quickly."],
   ["PSAGENT_SVC permissions and password", "Remove PSADMIN; keep Process Monitor and Report Manager only; change the password used in testing."],
   ["Query Viewer link and query trees", "The run link did not check query trees for a PSADMIN user; retest with a normal user."],
   ["SMTP server", "Needed for alert, summary, budget and weekly emails."],
   ["PSAGENT_AUDITOR role and the updated iScript", "Needed for the admin dashboard."],
   ["Hosting", "Replace ngrok with a reverse proxy under the PeopleSoft host; keep keys in a vault."],
   ["Real Excel to CI Create and Update", "Proven read-only so far; run on a test system first."]], [0.36, 0.64])
H2("7.2  Known limitations")
B(["Classic pages only for tasks, live mode and the recorder (no Fluid yet).",
   "The troubleshooter is strongest on Application Engine; SQR and COBOL source and Integration Broker failures are not covered.",
   "The security troubleshooter does not check query, process or Finance row security.",
   "Runbook approvals are clicks in the same browser, not routed to the approver; a run does not survive closing the browser.",
   "Chat history lasts only for the browser session; one service worker."])
H2("7.3  Roadmap")
T([["Area", "Next"],
   ["Fix and rerun", "After an alert and its diagnosis, propose a known fix; Rerun after a person's click; the watcher confirms and closes the incident."],
   ["Excel to CI", "Large files through the Process Scheduler with a process instance; bulk loads for recorded tasks; matching columns of any spreadsheet."],
   ["Ask the data", "A query result as a watchdog (\"tell me when this changes\"); favourite questions on the welcome screen."],
   ["Security", "Apply for row-security refresh; compare two users' access; access reviews and an access request workflow."],
   ["Runbooks", "Edit in the chat; real approvals by email or Teams; resume and hand-off; version history; evidence packs for auditors."],
   ["Watcher", "Integration Broker and long-running processes; pay cycle deadlines; Teams; learning what is normal."],
   ["Platform", "Fluid pages; shared sessions and several workers; production hosting and a security review."]], [0.18, 0.82])

# ===== Appendix ==============================================================================================
H1("Appendix A  Glossary")
T([["Term", "Meaning"],
   ["AE", "Application Engine, PeopleSoft's batch program framework"],
   ["Branding", "PeopleTools feature used to load custom JavaScript on every page"],
   ["CI", "Component Interface: PeopleSoft's API to a component, used by ExcelToCI and SOAPTOCI"],
   ["Confirm card", "The widget's summary of a task's values; nothing runs until the user clicks Confirm"],
   ["iScript", "A PeopleCode function callable by URL in the user's session (WEBLIB_ records)"],
   ["Live mode", "Claude operating PeopleSoft one page step at a time"],
   ["Query trees", "PeopleSoft query security: permission lists to access groups to records"],
   ["Runbook", "A published checklist of checks, tasks, processes, approvals and manual steps"],
   ["SJT tables", "HCM row-security tables (for example SJT_OPR_CLS) that decide which people a user sees"],
   ["SOAPTOCI", "The delivered WEBLIB_SOAPTOCI iScript that ExcelToCI uses to call Component Interfaces"],
   ["Strict tool", "A tool whose input shape the Claude API guarantees to match its schema"],
   ["Token", "The signed proof of the user's identity and agent roles, issued by WEBLIB_PSAGENT"],
   ["Watchdog / watch plan", "A rule the watcher checks on its own / a saved set of watchdogs"]], [0.22, 0.78])

H1("Appendix B  HTTP API summary")
P("Every call needs <font name='Consolas'>Authorization: Bearer &lt;token&gt;</font> and is recorded in the audit log "
  "under the user and the feature its path belongs to.")
T([["Area", "Endpoints"],
   ["Chat and tasks", "POST /api/chat, /api/confirm, /api/result, /api/batch/result; GET /api/me"],
   ["Live mode", "POST /api/live/step, /api/live/recover"],
   ["Investigations", "POST /api/troubleshoot/step, /api/approvals/review, /api/security/apply"],
   ["Recorder (ADMIN)", "POST /api/recordings/draft, /api/recordings/save"],
   ["Runbooks", "POST /api/runbooks/save (ADMIN), /api/runbooks/step"],
   ["Watcher (MONITOR)", "POST /api/monitors/save, /api/watchplans/save, /api/watchdogs/action; GET /api/watchdogs, /api/incidents, /api/watchplans/export"],
   ["Excel to CI", "GET /api/ci, /api/ci/shape, /api/ci/loads; POST /api/ci/template, /api/ci/check, /api/ci/start, /api/ci/next, /api/ci/result"],
   ["Ask the data", "GET /api/queries; POST /api/query/result"],
   ["Audit (AUDITOR)", "GET /api/audit, /api/audit/export"],
   ["Regression (ADMIN, test systems)", "GET /api/regress; POST /api/regress/save, /start, /next, /result, /live, /stop, /unattended"],
   ["Health", "GET /health"]], [0.25, 0.75])

doc = Doc(OUT)
doc.multiBuild(story)
print("written", OUT)
