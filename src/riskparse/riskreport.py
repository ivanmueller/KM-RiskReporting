"""The daily risk report: the "CTT Position Summary" page as Excel and PDF.

Built from what the run has: the trade sheet (Trading tab), the confirmations and bank
swaps, the checks, and the market prices in the trading workbook (:mod:`riskparse.market`).

The terminal figures of the risk report (opening inventory, receipts, butane injection,
deliveries, gain/loss, the inventory rolled and the Westspur short) come from the
scheduler's forecast, which is not among the sources yet. The Excel report has yellow
cells for them: typing them in completes the net open position, the inventory hedged and
the MTM roll exposure through formulas; Excel's File > Export then gives the full PDF. The
PDF written by the run shows those figures as "-" and says so. Nothing is invented.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from riskparse.market import Market

BBL_PER_M3 = 6.29287            # the risk report's factor
NAVY, ORANGE, INPUT, GREY = "14213D", "F39A1E", "FFF2CC", "EEF1F6"
BRIDGE = ["Opening inventory", "Receipts in (5-day avg)", "Butane injection",
          "Deliveries out", "Gain/loss"]
GRADES = ["LSB", "Midale", "SW", "C4", "C5"]
LOGO = Path(__file__).parent / "assets" / "km-logo.png"


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def mon(d: date) -> str:
    return f"{d:%b-%y}"


@dataclass
class ReportData:
    position_date: date
    month: date                                  # the position (delivery) month
    months: list[date]                           # market columns
    market: Market | None
    market_note: str = ""
    trade_sheet: str = ""
    book: list[tuple[str, dict[str, float]]] = field(default_factory=list)  # stream -> grade
    swaps: list[dict] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    kpi: dict = field(default_factory=dict)
    sheet_kpi: dict = field(default_factory=dict)

    @property
    def book_total(self) -> dict[str, float]:
        tot: dict[str, float] = defaultdict(float)
        for _s, g in self.book:
            for k, v in g.items():
                tot[k] += v
        return tot

    def price(self, component: str, month: date) -> float | None:
        """CAD/bbl: (WTI CMA or the grade's differential) x USD/CAD for that month."""
        m = self.market
        if m is None:
            return None
        usd = m.wti_cma.get(month) if component == "WTICMA" else m.diff(component, month)
        fx = m.fx.get(month)
        return usd * fx if usd is not None and fx is not None else None

    def commentary(self) -> str:
        k, s = self.kpi, self.sheet_kpi
        net = sum(self.book_total.values())
        parts = []
        if self.book:
            parts.append(f"Trade sheet book for {self.month:%B %Y}: {net:,.0f} m3 "
                         f"({'long' if net >= 0 else 'short'}; {net * BBL_PER_M3:,.0f} bbl).")
        if s:
            other = s.get("month_not_covered", 0)
            base = k.get("physical", 0) - other
            parts.append(f"{k.get('physical', 0)} physical confirmations and "
                         f"{k.get('financial', 0)} bank swaps: {s.get('matched', 0)} of the "
                         f"{base} physical deals for the trade sheet's month are on it"
                         + (f" ({other} more are for months whose trade sheet is not in the "
                            "folder)" if other else "")
                         + f"; {s.get('missing', 0)} trade sheet deals have no confirmation "
                           "yet.")
        if k.get("financial"):
            parts.append(f"Bank swaps: {k.get('rolls', 0)} roll(s) and {k.get('hedges', 0)} "
                         f"forward hedge(s), {k.get('swap_bbl', 0):,.0f} bbl.")
        m = self.market
        if m is not None and self.month in m.wti_cma:
            fx = m.fx.get(self.month)
            parts.append(f"WTI CMA {mon(self.month)} {m.wti_cma[self.month]:.2f} USD/bbl"
                         + (f", USD/CAD {fx:.4f}" if fx else "")
                         + (f" (curve of {m.curve_date:%Y-%m-%d})." if m.curve_date else "."))
        parts.append("Terminal figures (inventory, receipts, deliveries) come from the "
                     "scheduler's forecast, not yet a source: enter them in the yellow cells "
                     "of the Excel report to complete the net open position and MTM roll "
                     "exposure.")
        return " ".join(parts)


def build_report_data(result, data: dict, today: date | None = None) -> ReportData:
    """``data`` is the dashboard's :func:`riskparse.dashboard.build_data` result."""
    from riskparse.market import load_market
    from riskparse.tradesheet import monthly_month
    today = today or date.today()
    chk = getattr(result, "sheet_check", None)
    month = today.replace(day=1)
    market, note, name = None, "no trade sheet folder was given", ""
    if chk is not None:
        paths = sorted(chk.sheet.paths, key=lambda p: monthly_month(p) or date.min)
        name = paths[-1].name
        month = monthly_month(paths[-1]) or month
        try:
            market = load_market(paths[-1])
            note = (f"{market.source}: Pricing V2 (trade cycle "
                    f"{market.trade_cycle:%b %Y})" if market.trade_cycle else market.source)
            if market.curve_date:
                note += f" and Forward Curve of {market.curve_date:%Y-%m-%d}"
        except Exception as exc:          # a damaged workbook: report without prices
            note = f"prices not read from {name}: {exc}"
    rd = ReportData(position_date=today, month=month,
                    months=[_add_months(month, i) for i in range(5)], market=market,
                    market_note=note, trade_sheet=name, checks=data.get("checks", []),
                    kpi=data.get("kpi", {}),
                    sheet_kpi=(data.get("sources") or [{}])[0].get("kpi", {}))
    if chk is not None:
        book: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for s in chk.sheet.trades:
            if s.delivery_month == month and s.volume is not None:
                grade = s.grade if s.grade in GRADES else "Other"
                book[s.stream or "(no stream)"][grade] += float(s.signed_volume)
        rd.book = sorted(((k, dict(v)) for k, v in book.items()), key=lambda kv: kv[0])
    rd.swaps = data.get("financial", [])
    return rd


# --------------------------------------------------------------------------- Excel

def write_xlsx(rd: ReportData, path: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "CTT Position Summary"
    head = PatternFill("solid", fgColor=NAVY)
    sub = PatternFill("solid", fgColor=GREY)
    inp = PatternFill("solid", fgColor=INPUT)
    white, bold = Font(color="FFFFFF", bold=True), Font(bold=True)
    thin = Side(style="thin", color="C9CED8")
    box = Border(bottom=thin)
    m3, money, price, pct = "#,##0;(#,##0)", "#,##0;(#,##0)", "0.00;(0.00)", "0%"
    for col, w in zip("ABCDEFGHI", [2, 38, 15, 15, 15, 15, 15, 18, 16], strict=True):
        ws.column_dimensions[col].width = w

    def title_bar(r, text, cols=8):
        for c in range(2, 2 + cols):
            ws.cell(r, c).fill = head
        ws.cell(r, 2, text).font = white

    def headers(r, values, start=2, text_cols=(0,)):
        for i, v in enumerate(values):
            c = ws.cell(r, start + i, v)
            c.font, c.fill = bold, sub
            c.alignment = Alignment(horizontal="left" if i in text_cols else "right",
                                    wrap_text=True)

    if LOGO.is_file():
        try:
            from openpyxl.drawing.image import Image
            img = Image(str(LOGO))
            img.height, img.width = 45, 100
            ws.add_image(img, "H2")
        except Exception:
            pass
    ws["B2"] = "KINGSTON MARKETING LIMITED | RISK REPORT"
    ws["B2"].font = Font(bold=True, size=16, color=NAVY)
    ws["B3"] = f"CTT Position Summary  -  position date {rd.position_date:%B %d, %Y}"
    ws["B3"].font = Font(bold=True, size=11)
    ws["B4"] = f"Sources: trade sheet {rd.trade_sheet or '-'}; prices {rd.market_note}"
    ws["B4"].font = Font(italic=True, size=9, color="5D6678")

    # key figures (formulas filled once the position and roll tables exist)
    r = 6
    title_bar(r, "Cromer")
    headers(r + 1, ["", "Net open (m3)", "Net open (bbl)", "MTM roll exposure (CAD)",
                    "CAD/bbl", "Inventory hedged", "Trade sheet net (m3)",
                    "Bank swaps (bbl)"])
    kpi_row = r + 2

    r = kpi_row + 2
    title_bar(r, "Commentary")
    ws.merge_cells(start_row=r + 1, start_column=2, end_row=r + 1, end_column=9)
    c = ws.cell(r + 1, 2, rd.commentary())
    c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[r + 1].height = 64

    # position bridge: scheduler inputs
    r += 3
    title_bar(r, f"Position - Cromer {rd.month:%b '%y}   (yellow cells: from the scheduler's "
                 "forecast)")
    headers(r + 1, ["", "m3", "bbl", "% of inventory hedged"])
    first = r + 2
    for i, label in enumerate(BRIDGE):
        row = first + i
        ws.cell(row, 2, label)
        ws.cell(row, 3).fill = inp
        ws.cell(row, 3).number_format = m3
        ws.cell(row, 4, f"=IF(C{row}=\"\",\"\",C{row}*{BBL_PER_M3})").number_format = m3
    end_row = first + len(BRIDGE)
    ws.cell(end_row, 2, "CTT/WTT/VTT ending position").font = bold
    ws.cell(end_row, 3, f"=IF(COUNT(C{first}:C{end_row - 1})=0,\"\",SUM(C{first}:"
                        f"C{end_row - 1}))").number_format = m3
    ws.cell(end_row, 4, f"=IF(C{end_row}=\"\",\"\",C{end_row}*{BBL_PER_M3})").number_format = m3
    rolled, opened, short, net = end_row + 1, end_row + 2, end_row + 3, end_row + 4
    ws.cell(rolled, 2, "Less inventory rolled")
    ws.cell(opened, 2, "CTT/WTT/VTT open").font = bold
    ws.cell(short, 2, "Less Westspur short (SES + MSM)")
    ws.cell(net, 2, "Net open position, long/(short)").font = bold
    for row in (rolled, short):
        ws.cell(row, 3).fill = inp
        ws.cell(row, 3).number_format = m3
        ws.cell(row, 4, f"=IF(C{row}=\"\",\"\",C{row}*{BBL_PER_M3})").number_format = m3
    ws.cell(opened, 3, f"=IF(C{end_row}=\"\",\"\",C{end_row}+N(C{rolled}))").number_format = m3
    ws.cell(opened, 4, f"=IF(C{opened}=\"\",\"\",C{opened}*{BBL_PER_M3})").number_format = m3
    ws.cell(net, 3, f"=IF(C{opened}=\"\",\"\",C{opened}+N(C{short}))").number_format = m3
    ws.cell(net, 4, f"=IF(C{net}=\"\",\"\",C{net}*{BBL_PER_M3})").number_format = m3
    ws.cell(net, 5, f"=IF(OR(C{end_row}=\"\",C{end_row}=0),\"\",-N(C{rolled})/C{end_row})")
    ws.cell(net, 5).number_format = pct
    for row in range(first, net + 1):
        for col in range(2, 6):
            ws.cell(row, col).border = box

    # trade sheet book by stream
    r = net + 2
    title_bar(r, f"Trade sheet book by stream - {rd.month:%B %Y} (m3, buys + / sales -)")
    cols = GRADES + ["Other"]
    headers(r + 1, ["Stream", *cols, "Total"])
    row = r + 2
    for stream, g in rd.book:
        ws.cell(row, 2, stream)
        for j, grade in enumerate(cols):
            if grade in g:
                ws.cell(row, 3 + j, round(g[grade], 3)).number_format = m3
        ws.cell(row, 3 + len(cols), f"=SUM(C{row}:{get_column_letter(2 + len(cols))}{row})")
        ws.cell(row, 3 + len(cols)).number_format = m3
        row += 1
    if not rd.book:
        ws.cell(row, 2, "No trade sheet rows for this month.").font = Font(italic=True)
        row += 1
    book_total = row
    ws.cell(row, 2, "Total").font = bold
    for j in range(len(cols) + 1):
        col = get_column_letter(3 + j)
        ws.cell(row, 3 + j, f"=SUM({col}{r + 2}:{col}{row - 1})").number_format = m3
        ws.cell(row, 3 + j).font = bold

    # roll table
    r = row + 2
    m1, m2, m3_ = rd.months[0], rd.months[1], rd.months[2]
    title_bar(r, "WTICMA + LSB index roll (CAD/bbl)")
    headers(r + 1, ["Component", mon(m1), mon(m2), mon(m3_), "Roll (CAD/bbl)", "Bbl",
                    "Exposure (CAD)", "Hedgeable?"], text_cols=(0, 7))
    rows = [("WTICMA", "WTICMA", (m1, m2), "Yes, by rolling"),
            ("LSB COMM", "LSB", (m1, m2), "No, basis risk"),
            ("WTICMA", "WTICMA", (m2, m3_), "Yes, by rolling"),
            ("LSB COMM", "LSB", (m2, m3_), "No, basis risk")]
    roll_first = r + 2
    col_of = {m1: "C", m2: "D", m3_: "E"}
    for i, (label, comp, (a, b), hedge) in enumerate(rows):
        row = roll_first + i
        ws.cell(row, 2, f"{label} ({mon(a)} to {mon(b)})")
        for mth in (a, b):
            v = rd.price(comp, mth)
            cell = ws.cell(row, 3 + rd.months.index(mth), round(v, 4) if v is not None else None)
            cell.number_format = price
        ca, cb = col_of[a], col_of[b]
        ws.cell(row, 6, f"=IF(OR({ca}{row}=\"\",{cb}{row}=\"\"),\"\",{cb}{row}-{ca}{row})")
        ws.cell(row, 6).number_format = price
        if i < 2:
            ws.cell(row, 7, f"=IF(D{net}=\"\",\"\",D{net})")
        else:
            ws.cell(row, 7).fill = inp            # the next month's position, bbl
        ws.cell(row, 7).number_format = m3
        ws.cell(row, 8, f"=IF(OR(F{row}=\"\",G{row}=\"\"),\"\",F{row}*G{row})")
        ws.cell(row, 8).number_format = money
        ws.cell(row, 9, hedge)
    roll_total = roll_first + len(rows)
    ws.cell(roll_total, 2, "Total").font = bold
    ws.cell(roll_total, 8, f"=IF(COUNT(H{roll_first}:H{roll_total - 1})=0,\"\","
                           f"SUM(H{roll_first}:H{roll_total - 1}))").number_format = money
    ws.cell(roll_total, 8).font = bold

    # key figures row
    ws.cell(kpi_row, 2, "Cromer")
    ws.cell(kpi_row, 3, f"=C{net}").number_format = m3
    ws.cell(kpi_row, 4, f"=D{net}").number_format = m3
    ws.cell(kpi_row, 5, f"=H{roll_total}").number_format = money
    ws.cell(kpi_row, 6, f"=IF(OR(E{kpi_row}=\"\",D{kpi_row}=\"\",D{kpi_row}=0),\"\","
                        f"E{kpi_row}/D{kpi_row})").number_format = price
    ws.cell(kpi_row, 7, f"=E{net}").number_format = pct
    ws.cell(kpi_row, 8, f"=I{book_total}").number_format = m3
    ws.cell(kpi_row, 9, rd.kpi.get("swap_bbl", 0) or 0).number_format = m3
    for col in range(3, 10):
        ws.cell(kpi_row, col).font = Font(bold=True, size=12)

    # bank swaps
    r = roll_total + 2
    title_bar(r, "Bank swaps (financial hedges, not on the trade sheet by design)")
    headers(r + 1, ["Contract #", "Type", "Paired with", "Side", "Month", "Quantity (bbl)",
                    "Fixed price", "Price unit"], text_cols=(0, 1, 2, 3, 4, 7))
    row = r + 2
    for f in rd.swaps:
        for j, v in enumerate([f["contract"], f["kind"], f["partner"], f["side"], f["term"],
                               f["quantity"], f["fixed_price"], f["price_unit"]]):
            cell = ws.cell(row, 2 + j, v)
            if j == 5:
                cell.number_format = m3
            if j == 6:
                cell.number_format = price
        row += 1
    if not rd.swaps:
        ws.cell(row, 2, "No bank swaps in this run.").font = Font(italic=True)
        row += 1

    # market
    r = row + 1
    title_bar(r, "Market (USD/bbl)")
    headers(r + 1, ["", *[mon(m) for m in rd.months], "Source"])
    mk = rd.market
    market_rows = [("WTICMA", lambda m: mk.wti_cma.get(m), "WTI CMA (calculated)")]
    for g in ("LSB", "Midale", "SW", "WCS"):
        market_rows.append((f"{g} COMM index", lambda m, g=g: mk.diff(g, m),
                            "COMM index / broker average"))
    market_rows += [("USDCAD", lambda m: mk.fx.get(m), "FX curve"),
                    ("MBV C4 (USD/gal)", lambda m: mk.c4.get(m), "NYMEX Mont Belvieu")]
    row = r + 2
    for label, fn, src in market_rows:
        ws.cell(row, 2, label)
        for j, mth in enumerate(rd.months):
            v = fn(mth) if mk is not None else None
            cell = ws.cell(row, 3 + j, round(v, 4) if v is not None else None)
            cell.number_format = "0.0000" if label in ("USDCAD",) else price
        ws.cell(row, 8, src).font = Font(size=9, color="5D6678")
        row += 1

    # checks
    r = row + 1
    title_bar(r, "Checks (all should show OK)")
    headers(r + 1, ["Check", "Value", "Target", "Result"], text_cols=(0, 3))
    row = r + 2
    for c in rd.checks:
        ws.cell(row, 2, c["check"])
        ws.cell(row, 3, c["value"])
        ws.cell(row, 4, c["target"])
        ws.cell(row, 5, "OK" if c["ok"] else "CHECK").font = Font(
            bold=True, color="1A7F4B" if c["ok"] else "B42318")
        row += 1
    ws.cell(row, 2, "Scheduler figures entered (yellow cells)")
    ws.cell(row, 3, f"=COUNT(C{first}:C{end_row - 1})")
    ws.cell(row, 4, len(BRIDGE))
    ws.cell(row, 5, f"=IF(C{row}=D{row},\"OK\",\"CHECK\")").font = bold
    row += 2
    ws.cell(row, 2, "Not in the current sources: Mitsue positions, C4 (butane) supply and "
                    "hedge coverage (NGL Deals / butane hedges), and the scheduler's terminal "
                    "forecast.").font = Font(italic=True, size=9, color="5D6678")

    ws.print_area = f"B2:I{row}"
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.oddFooter.left.text = "Kingston Marketing Limited - Risk Report"
    ws.oddFooter.right.text = "Page &P of &N"
    ws.sheet_view.showGridLines = False
    wb.save(path)
    return path


# --------------------------------------------------------------------------- PDF

def write_pdf(rd: ReportData, path: Path) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Image,
        KeepTogether,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    navy, orange = colors.HexColor("#" + NAVY), colors.HexColor("#" + ORANGE)
    grey, line = colors.HexColor("#" + GREY), colors.HexColor("#C9CED8")
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=8.5, leading=11)
    small = ParagraphStyle("s", parent=body, fontSize=7.5, leading=9.5,
                           textColor=colors.HexColor("#5D6678"))
    h = ParagraphStyle("h", parent=body, fontName="Helvetica-Bold", fontSize=9.5,
                       textColor=colors.white, alignment=TA_LEFT)
    dash = "-"
    hl = ParagraphStyle("hl", parent=body, fontName="Helvetica-Bold", fontSize=7.5, leading=9)
    hr = ParagraphStyle("hr", parent=hl, alignment=TA_RIGHT)

    def n(v, fmt="{:,.0f}"):
        if v is None or v == "":
            return dash
        return f"({fmt.format(-v)})" if isinstance(v, (int, float)) and v < 0 else fmt.format(v)

    def section(title, rows, widths, num_cols=(), bold_rows=()):
        if rows and all(isinstance(c, str) for c in rows[0]):      # wrap the column heads
            rows = [[Paragraph(c, hr if i in num_cols else hl) if c else ""
                     for i, c in enumerate(rows[0])]] + rows[1:]
        t = Table([[Paragraph(title, h)] + [""] * (len(widths) - 1)] + rows, colWidths=widths,
                  repeatRows=2)
        style = [("SPAN", (0, 0), (-1, 0)), ("BACKGROUND", (0, 0), (-1, 0), navy),
                 ("BACKGROUND", (0, 1), (-1, 1), grey),
                 ("FONT", (0, 1), (-1, 1), "Helvetica-Bold", 7.5),
                 ("FONT", (0, 2), (-1, -1), "Helvetica", 8),
                 ("LINEBELOW", (0, 1), (-1, -1), 0.4, line),
                 ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                 ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]
        for c in num_cols:
            style.append(("ALIGN", (c, 1), (c, -1), "RIGHT"))
        for r in bold_rows:
            style.append(("FONT", (0, r), (-1, r), "Helvetica-Bold", 8))
        t.setStyle(TableStyle(style))
        return t

    story = []
    title = [Paragraph("<b>KINGSTON MARKETING LIMITED | RISK REPORT</b>",
                       ParagraphStyle("t", parent=body, fontSize=14, leading=17,
                                      textColor=navy)),
             Paragraph(f"<b>CTT Position Summary</b> - position date "
                       f"{rd.position_date:%B %d, %Y}", body),
             Paragraph(f"Sources: trade sheet {rd.trade_sheet or dash}; prices "
                       f"{rd.market_note}", small)]
    logo = Image(str(LOGO), width=1.15 * inch, height=0.52 * inch) if LOGO.is_file() else ""
    top = Table([[title, logo]], colWidths=[5.9 * inch, 1.4 * inch])
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                             ("LINEBELOW", (0, 0), (-1, 0), 2, orange),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [top, Spacer(1, 8)]

    net = sum(rd.book_total.values())
    k = rd.kpi
    story.append(section("Cromer", [
        ["Net open (m3)", "Net open (bbl)", "MTM roll exposure (CAD)", "CAD/bbl",
         "Inventory hedged", "Trade sheet net (m3)", "Bank swaps (bbl)"],
        [dash, dash, dash, dash, dash, n(net) if rd.book else dash,
         n(k.get("swap_bbl", 0))]],
        [1.05 * inch] * 7, num_cols=range(7)))
    story.append(Paragraph("Net open, MTM roll exposure and inventory hedged need the "
                           "scheduler's terminal figures (enter them in the Excel report).",
                           small))
    story += [Spacer(1, 6), section("Commentary", [[Paragraph(rd.commentary(), body)]],
                                    [7.35 * inch]), Spacer(1, 6)]

    rows = [["", "m3", "bbl"]] + [[b, dash, dash] for b in BRIDGE] + [
        ["CTT/WTT/VTT ending position", dash, dash], ["Less inventory rolled", dash, dash],
        ["CTT/WTT/VTT open", dash, dash], ["Less Westspur short (SES + MSM)", dash, dash],
        ["Net open position, long/(short)", dash, dash]]
    story += [section(f"Position - Cromer {rd.month:%b '%y} (from the scheduler's forecast: "
                      "not yet a source)", rows, [3.35 * inch, 2 * inch, 2 * inch],
                      num_cols=(1, 2), bold_rows=(7, 9, 11)), Spacer(1, 6)]

    cols = GRADES + ["Other"]
    rows = [["Stream", *cols, "Total"]]
    for stream, g in rd.book:
        rows.append([stream, *[n(g.get(c)) if c in g else "" for c in cols],
                     n(sum(g.values()))])
    tot = rd.book_total
    rows.append(["Total", *[n(tot.get(c)) if c in tot else "" for c in cols], n(net)]
                if rd.book else ["No trade sheet rows for this month."] + [""] * 7)
    story += [section(f"Trade sheet book by stream - {rd.month:%B %Y} (m3, buys + / sales -)",
                      rows, [1.75 * inch] + [0.8 * inch] * 7, num_cols=range(1, 8),
                      bold_rows=(len(rows),)), Spacer(1, 6)]

    m1, m2, m3_ = rd.months[:3]
    rows = [["Component", mon(m1), mon(m2), mon(m3_), "Roll (CAD/bbl)", "Bbl",
             "Exposure (CAD)", "Hedgeable?"]]
    for label, comp, (a, b), hedge in [("WTICMA", "WTICMA", (m1, m2), "Yes, by rolling"),
                                       ("LSB COMM", "LSB", (m1, m2), "No, basis risk"),
                                       ("WTICMA", "WTICMA", (m2, m3_), "Yes, by rolling"),
                                       ("LSB COMM", "LSB", (m2, m3_), "No, basis risk")]:
        pa, pb = rd.price(comp, a), rd.price(comp, b)
        vals = {a: pa, b: pb}
        roll = pb - pa if pa is not None and pb is not None else None
        rows.append([f"{label} ({mon(a)} to {mon(b)})",
                     *[n(vals.get(m), "{:,.2f}") if m in vals else "" for m in (m1, m2, m3_)],
                     n(roll, "{:,.2f}"), dash, dash, hedge])
    rows.append(["Total", "", "", "", "", "", dash, ""])
    story += [section("WTICMA + LSB index roll (CAD/bbl)", rows,
                      [1.75 * inch, 0.7 * inch, 0.7 * inch, 0.7 * inch, 0.85 * inch,
                       0.75 * inch, 0.9 * inch, 1.0 * inch], num_cols=range(1, 7),
                      bold_rows=(len(rows),)), Spacer(1, 6)]

    rows = [["Contract #", "Type", "Paired with", "Side", "Month", "Quantity (bbl)",
             "Fixed price"]]
    for f in rd.swaps:
        rows.append([f["contract"], f["kind"], f["partner"], f["side"], f["term"],
                     n(f["quantity"]), f"{n(f['fixed_price'], '{:,.2f}')} {f['price_unit']}"])
    if not rd.swaps:
        rows.append(["No bank swaps in this run."] + [""] * 6)
    story += [section("Bank swaps (financial hedges, not on the trade sheet by design)", rows,
                      [1.05 * inch, 1.1 * inch, 1.05 * inch, 0.6 * inch, 0.7 * inch,
                       1.15 * inch, 1.7 * inch], num_cols=(5, 6)), Spacer(1, 6)]

    mk = rd.market
    rows = [["", *[mon(m) for m in rd.months]]]
    if mk is not None:
        rows.append(["WTICMA", *[n(mk.wti_cma.get(m), "{:,.2f}") for m in rd.months]])
        for g in ("LSB", "Midale", "SW", "WCS"):
            rows.append([f"{g} COMM index", *[n(mk.diff(g, m), "{:,.2f}") for m in rd.months]])
        rows.append(["USDCAD", *[n(mk.fx.get(m), "{:,.4f}") for m in rd.months]])
        rows.append(["MBV C4 (USD/gal)", *[n(mk.c4.get(m), "{:,.4f}") for m in rd.months]])
    else:
        rows.append(["No prices: " + rd.market_note] + [""] * 5)
    story += [section("Market (USD/bbl)", rows, [2.1 * inch] + [1.05 * inch] * 5,
                      num_cols=range(1, 6)), Spacer(1, 6)]

    rows = [["Check", "Value", "Target", "Result"]]
    for c in rd.checks:
        rows.append([Paragraph(c["check"], body), c["value"], c["target"],
                     "OK" if c["ok"] else "CHECK"])
    rows.append([Paragraph("Scheduler figures entered", body), 0, len(BRIDGE), "CHECK"])
    t = section("Checks (all should show OK)", rows,
                [4.85 * inch, 0.8 * inch, 0.8 * inch, 0.9 * inch], num_cols=(1, 2))
    for i, r in enumerate(rows[1:], start=2):
        t.setStyle(TableStyle([("TEXTCOLOR", (3, i), (3, i),
                                colors.HexColor("#1A7F4B" if r[3] == "OK" else "#B42318")),
                               ("FONT", (3, i), (3, i), "Helvetica-Bold", 8)]))
    story += [t, Spacer(1, 6),
              Paragraph("Not in the current sources: Mitsue positions, C4 (butane) supply and "
                        "hedge coverage (NGL Deals / butane hedges), and the scheduler's "
                        "terminal forecast.", small)]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#5D6678"))
        canvas.drawString(0.6 * inch, 0.45 * inch, "Kingston Marketing Limited - Risk Report - "
                          f"{rd.position_date:%Y-%m-%d}")
        canvas.drawRightString(letter[0] - 0.6 * inch, 0.45 * inch, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(path), pagesize=letter, leftMargin=0.55 * inch,
                            rightMargin=0.55 * inch, topMargin=0.5 * inch,
                            bottomMargin=0.7 * inch, title="Risk Report - CTT Position Summary",
                            author="Kingston Marketing Limited")
    # keep each section (title, table, note) on one page
    grouped, block = [], []
    for item in story:
        if isinstance(item, Spacer):
            if block:
                grouped.append(KeepTogether(block))
            grouped.append(item)
            block = []
        else:
            block.append(item)
    if block:
        grouped.append(KeepTogether(block))
    doc.build(grouped, onFirstPage=footer, onLaterPages=footer)
    return path


def report_names(day: date) -> tuple[str, str]:
    stem = f"Risk Report - CTT Position Summary - {day:%Y-%m-%d}"
    return f"{stem}.xlsx", f"{stem}.pdf"


def write_risk_report(result, data: dict, out_dir: Path,
                      today: date | None = None) -> tuple[Path, Path]:
    rd = build_report_data(result, data, today)
    xlsx_name, pdf_name = report_names(rd.position_date)
    return (write_xlsx(rd, Path(out_dir) / xlsx_name), write_pdf(rd, Path(out_dir) / pdf_name))
