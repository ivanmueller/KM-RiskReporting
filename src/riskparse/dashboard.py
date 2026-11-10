# ruff: noqa: E501  (the embedded page template has long HTML/JavaScript lines)
"""The run's risk dashboard: one self-contained HTML page, and a temporary local server.

``write_dashboard`` turns a review result (parsed confirmations, the trade sheet check and
the swaps) into ``dashboard.html`` in the run folder: floating buttons on the left for the
checks (a checkmark when every check is OK, a flag with a count when not; it slides out a
panel listing each check, what failed and a link to it) and for export (the daily risk
report PDF and Excel, raw-data.xlsx, review.xlsx), key figures, positions by delivery month and grade, the trade sheet reconciliation, the bank
swaps and the counterparties. Everything is embedded in the page (no internet, no
libraries), so it also opens straight from the folder.

``serve`` shows that page at ``http://127.0.0.1:<port>/`` until the window is closed. It
listens on this computer only: nobody else on the network can open it.
"""

from __future__ import annotations

import base64
import http.server
import json
import socketserver
import webbrowser
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from riskparse.sheetcheck import (
    DIFFERENCES,
    MATCHED,
    MONTH_NOT_COVERED,
    NO_CONFIRMATION,
    NO_CONFIRMATION_DIRECT,
    NOT_ON_SHEET,
    OUTSIDE_PERIOD,
    is_financial,
    month_label,
    month_of,
)

BBL_PER_M3 = 6.28981
FILE = "dashboard.html"
DEFAULT_PORT = 8765
LOGO = Path(__file__).parent / "assets" / "km-logo.png"


def _f(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    return v


def _m3(t) -> float | None:
    """A confirmation's volume in m3 (bbl converted), or None."""
    if t.quantity is None:
        return None
    q = float(t.quantity)
    return q / BBL_PER_M3 if (t.unit or "").lower().startswith("bbl") else q


def _short(label: str) -> str:
    return label.split()[0].capitalize()          # "Monthly"


def _source(chk) -> dict:
    """One trade sheet: its reconciliation, position and counts."""
    sh = chk.sheet
    count = chk.count
    recon = []
    for r in chk.rows:
        t, s = r.trade, r.sheet
        recon.append({
            "status": r.status, "contract": t.contract_no if t else "",
            "sheet_contract": s.internal_contract if s else "",
            "where": s.where if s else "",
            "side": (t or s).side,
            "counterparty": t.counterparty if t else (s.master_counterparty or s.counterparty),
            "grade": (t or s).grade,
            "month": month_label(month_of(t.start_date)) if t else month_label(s.delivery_month),
            "conf_qty": _f(t.quantity) if t else None, "sheet_qty": _f(s.volume) if s else None,
            "conf_diff": _f(t.fixed_differential) if t else None,
            "sheet_diff": _f(s.differential) if s else None,
            "conf_date": _f(t.trade_date) if t else None,
            "sheet_date": _f(s.trade_date) if s else None,
            "conf_broker": t.family.split(" (")[0] if t else "",
            "sheet_broker": s.broker if s else "",
            "differences": r.differences, "notes": r.notes})
    pos = defaultdict(lambda: {"buy": 0.0, "sell": 0.0})
    for s in sh.trades:
        if s.volume is not None and s.delivery_month is not None:
            pos[(s.delivery_month, s.grade or "-")]["buy" if s.side == "BUY" else "sell"] += \
                float(s.volume)
    position = [{"month": month_label(m), "grade": g, "bought": p["buy"], "sold": p["sell"],
                 "net": p["buy"] - p["sell"]}
                for (m, g), p in sorted(pos.items(), key=lambda kv: kv[0])]
    return {
        "label": sh.label, "named": sh.named, "short": _short(sh.label), "files": [p.name for p in sh.paths],
        "modified": f"{sh.modified:%Y-%m-%d %H:%M}", "tabs": sh.sheets_read,
        "rows": len(sh.trades), "missing": sh.missing,
        "standing": chk.standing_rows, "internal": chk.internal_rows,
        "period": [_f(chk.period[0]), _f(chk.period[1])] if chk.period else None,
        "kpi": {"matched": count(MATCHED) + count(DIFFERENCES),
                "differences": count(DIFFERENCES), "not_on_sheet": count(NOT_ON_SHEET),
                "month_not_covered": count(MONTH_NOT_COVERED),
                "missing": count(NO_CONFIRMATION) + count(NO_CONFIRMATION_DIRECT),
                "outside": count(OUTSIDE_PERIOD)},
        "recon": recon, "position": position}


def _logo() -> str:
    try:
        return "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode("ascii")
    except OSError:
        return ""


def build_data(result) -> dict:
    trades = result.trades
    checks_by_source = list(getattr(result, "sheet_checks", []) or [])
    financial = getattr(result, "financial", []) or []
    physical = [t for t in trades if not is_financial(t)]
    sources = [_source(c) for c in checks_by_source]

    # validated on every trade sheet read
    both = 0
    if checks_by_source:
        for t in physical:
            rows = [c.for_trade(t) for c in checks_by_source]
            if all(r is not None and r.status in (MATCHED, DIFFERENCES) for r in rows):
                both += 1

    pos = defaultdict(lambda: {"buy": 0.0, "sell": 0.0, "buy_dv": 0.0, "sell_dv": 0.0})
    for t in physical:
        m3 = _m3(t)
        if m3 is None or not t.start_date:
            continue
        p = pos[(month_of(t.start_date), t.grade or "-")]
        side = "buy" if t.side == "BUY" else "sell"
        p[side] += m3
        if t.fixed_differential is not None:
            p[side + "_dv"] += m3 * float(t.fixed_differential)
    conf_position = [{
        "month": month_label(m), "grade": g, "bought": p["buy"], "sold": p["sell"],
        "net": p["buy"] - p["sell"],
        "wa_buy_diff": p["buy_dv"] / p["buy"] if p["buy"] else None,
        "wa_sell_diff": p["sell_dv"] / p["sell"] if p["sell"] else None}
        for (m, g), p in sorted(pos.items(), key=lambda kv: kv[0])]

    fin_rows = [{"contract": f.trade.contract_no, "kind": f.kind,
                 "partner": f.partner.contract_no if f.partner else "",
                 "side": f.trade.side, "term": f.trade.term,
                 "quantity": _f(f.trade.quantity), "unit": f.trade.unit,
                 "fixed_price": _f(f.trade.fixed_price), "price_unit": f.trade.price_unit,
                 "counterparty": f.trade.counterparty, "trade_date": _f(f.trade.trade_date),
                 "note": f.note} for f in financial]

    cp = defaultdict(lambda: {"bought": 0.0, "sold": 0.0, "trades": 0})
    for t in physical:
        c = cp[t.counterparty or "-"]
        c["bought" if t.side == "BUY" else "sold"] += _m3(t) or 0.0
        c["trades"] += 1
    counterparties = sorted(({"name": k, **v} for k, v in cp.items()),
                            key=lambda c: -(c["bought"] + c["sold"]))

    checks = []

    def check(text, items, hint="", jump=None):
        checks.append({"check": text, "value": len(items), "target": 0, "ok": not items,
                       "items": items[:40], "more": max(0, len(items) - 40), "hint": hint,
                       "jump": jump})

    check("Documents no parser recognised",
          [d.get("file", "") for d in result.documents if d.get("parsed_as") == "UNRECOGNISED"],
          "They are left out of raw-data. See the documents sheet in review.xlsx; a new "
          "layout needs a new parser.")
    check("Company names not in the counterparty map",
          [r[0] for r in result.names if r[2] == "NOT IN MAP"],
          "Add each spelling to config\\counterparty_map.csv and run again.")
    for label, err in (getattr(result, "sheet_errors", {}) or {}).items():
        check(f"{label[0].upper()}{label[1:]} read", [err],
              "Check the trade sheet folder chosen in SETUP.bat.")
    for i, src in enumerate(sources):
        rows = src["recon"]
        check(f"Physical confirmations not on {src['named']}",
              [f"{r['contract']} - {r['counterparty']} {r['side']} {r['grade']} {r['month']}"
               for r in rows if r["status"] == NOT_ON_SHEET],
              "The desk has not recorded these deals, or recorded them under another "
              "counterparty, grade or month.",
              {"section": "reconsec", "source": i, "filter": NOT_ON_SHEET})
        check(f"Matched trades that differ from {src['named']}",
              [f"{r['contract']} - {'; '.join(r['differences'])}"
               for r in rows if r["status"] == DIFFERENCES],
              "Same deal on both, but a field differs: the confirmation or the trade sheet "
              "needs correcting.",
              {"section": "reconsec", "source": i, "filter": DIFFERENCES})
        check(f"{src['label'][0].upper()}{src['label'][1:]} deals in the period without a "
              "confirmation",
              [f"{r['sheet_contract']} - {r['counterparty']} {r['side']} {r['grade']} "
               f"{r['month']} ({r['sheet_broker'] or 'direct'})"
               for r in rows if r["status"] in (NO_CONFIRMATION, NO_CONFIRMATION_DIRECT)],
              "Ask the broker or counterparty for the confirmation, and add it to the "
              "contracts folder.",
              {"section": "reconsec", "source": i, "filter": "MISSING"})
    check("Bank swaps not identified as a roll or forward hedge",
          [f"{f.trade.contract_no} - {f.trade.side} {f.trade.term}"
           for f in financial if f.kind == "SWAP"],
          "Check what these swaps hedge.", {"section": "finsec"})
    return {
        "generated": f"{datetime.now():%Y-%m-%d %H:%M}",
        "position_date": f"{date.today():%B} {date.today().day}, {date.today():%Y}",
        "source": str(getattr(result, "source", "")), "build": getattr(result, "build", ""),
        "logo": _logo(), "sources": sources,
        "sheet_errors": dict(getattr(result, "sheet_errors", {}) or {}),
        "kpi": {"physical": len(physical), "financial": len(financial),
                "validated_all": both,
                "swap_bbl": sum(float(f.trade.quantity or 0) for f in financial),
                "rolls": sum(1 for f in financial if f.kind == "ROLL") // 2,
                "hedges": sum(1 for f in financial if f.kind == "FORWARD HEDGE")},
        "checks": checks, "financial": fin_rows, "conf_position": conf_position,
        "counterparties": counterparties,
        "attention": [{"contract": t.contract_no, "counterparty": t.counterparty,
                       "flags": t.flags} for t in trades if t.flags],
        "bbl_per_m3": BBL_PER_M3,
    }


EXPORT_LABELS = (("Risk Report - CTT Position Summary", ".pdf", "Daily risk report (PDF)"),
                 ("Risk Report - CTT Position Summary", ".xlsx", "Daily risk report (Excel)"),
                 ("raw-data", ".xlsx", "raw-data.xlsx (firm's format + checks)"),
                 ("review", ".xlsx", "review.xlsx (evidence, trade sheet, swaps)"))


def exports(folder: Path) -> list[dict]:
    """The run's files the dashboard offers for download (newest report first)."""
    out = []
    for stem, suffix, label in EXPORT_LABELS:
        found = sorted((p for p in Path(folder).glob(f"{stem}*{suffix}") if p.is_file()),
                       reverse=True)
        if found:
            out.append({"file": found[0].name, "label": label})
    return out


def write_dashboard(result, out_dir: Path, data: dict | None = None) -> Path:
    data = dict(data or build_data(result))
    data["exports"] = exports(out_dir)
    data = json.dumps(data, default=str).replace("</", "<\\/")
    path = Path(out_dir) / FILE
    path.write_text(TEMPLATE.replace("__DATA__", data), encoding="utf-8")
    return path


def find_dashboard(location: str | Path) -> Path:
    """The dashboard in a run folder, or the newest one under a results folder."""
    p = Path(location)
    if p.is_file():
        return p
    if (p / FILE).is_file():
        return p / FILE
    found = sorted(p.glob(f"*/{FILE}"), key=lambda f: f.stat().st_mtime) if p.is_dir() else []
    if not found:
        raise FileNotFoundError(f"no {FILE} in {p} or its run folders")
    return found[-1]


def make_server(location: str | Path, port: int = DEFAULT_PORT):
    """A server for the dashboard on this computer only (127.0.0.1): (server, url).
    Tries ``port`` and the next 19 if it is taken."""
    page_path = find_dashboard(location)
    page = page_path.read_bytes()
    # only the run's own export files, by exact name: nothing else in the folder is served
    files = {e["file"]: page_path.parent / e["file"] for e in exports(page_path.parent)}
    types = {".pdf": "application/pdf",
             ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):          # noqa: N802 (http.server's name)
            from urllib.parse import unquote
            name = unquote(self.path.split("?")[0]).lstrip("/")
            if name in ("", FILE):
                body, ctype, extra = page, "text/html; charset=utf-8", {}
            elif name in files:
                body = files[name].read_bytes()
                ctype = types.get(files[name].suffix.lower(), "application/octet-stream")
                disposition = "inline" if name.lower().endswith(".pdf") else "attachment"
                extra = {"Content-Disposition": f'{disposition}; filename="{name}"'}
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            for k, v in extra.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    for p in range(port, port + 20):
        try:
            server = socketserver.ThreadingTCPServer(("127.0.0.1", p), Handler)
        except OSError:
            continue
        server.daemon_threads = True
        return server, f"http://127.0.0.1:{server.server_address[1]}/"
    raise OSError(f"no free port between {port} and {port + 19}")


def serve(location: str | Path, port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    """Show the dashboard until the window is closed (or Ctrl+C)."""
    server, url = make_server(location, port)
    print(f"Dashboard: {url}")
    print("It stays available while this window is open. Close the window (or press "
          "Ctrl+C) to stop it.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Risk Dashboard</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--ink:#1d2433;--muted:#5d6678;--line:#dde1e8;--head:#14213d;
--headink:#fff;--ok:#1a7f4b;--okbg:#e3f4ea;--bad:#b42318;--badbg:#fde8e6;--warn:#9a6700;
--warnbg:#fff4d6;--buy:#2f6fb3;--sell:#e8862a;--chip:#eef1f6;--accent:#f39a1e}
@media (prefers-color-scheme:dark){:root{--bg:#11151c;--panel:#1a2029;--ink:#e6e9ef;
--muted:#9aa3b2;--line:#2c3441;--head:#0b1220;--headink:#e6e9ef;--ok:#5cc28c;--okbg:#14301f;
--bad:#ff8a7a;--badbg:#3a1714;--warn:#f2c94c;--warnbg:#33290d;--buy:#6ea8e8;--sell:#f5a65b;
--chip:#232a35}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.45 "Segoe UI",system-ui,-apple-system,Arial,sans-serif}
header{background:var(--head);color:var(--headink);padding:14px 24px;display:flex;
align-items:center;gap:18px;border-bottom:4px solid var(--accent)}
header .logo{background:#fff;border-radius:8px;padding:6px 10px;display:flex;flex:none}
header .logo img{height:44px;width:auto;display:block}
header h1{margin:0;font-size:20px;letter-spacing:.02em}header p{margin:4px 0 0;opacity:.8;
overflow-wrap:anywhere}
main{max-width:1400px;margin:0 auto;padding:18px 16px 60px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
.kpi b{display:block;font-size:26px;font-variant-numeric:tabular-nums}
.kpi span{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.kpi small{display:block;color:var(--muted);margin-top:2px}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:16px;margin-top:16px;min-width:0}
section h2{margin:0 0 4px;font-size:16px}section .sub{color:var(--muted);margin:0 0 12px}
.scroll{overflow-x:auto;max-width:100%}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;
white-space:nowrap}td.wrap{white-space:normal;min-width:320px;max-width:520px}
th{font-size:12px;color:var(--muted);font-weight:600;background:var(--panel)}
td.n,th.n{text-align:right}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;white-space:nowrap}
.ok{background:var(--okbg);color:var(--ok)}.bad{background:var(--badbg);color:var(--bad)}
.warn{background:var(--warnbg);color:var(--warn)}.mute{background:var(--chip);color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 10px}
.chips button,.tabs button{border:1px solid var(--line);background:var(--chip);color:var(--ink);
border-radius:99px;padding:4px 12px;cursor:pointer;font:inherit}
.chips button.on,.tabs button.on{background:var(--head);color:var(--headink);border-color:var(--head)}
.tabs{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 12px}.tabs button{border-radius:8px;
font-weight:600}
.grid2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}
@media (max-width:900px){.grid2{grid-template-columns:1fr}header{flex-wrap:wrap}}
.bar{display:grid;grid-template-columns:minmax(70px,150px) minmax(0,1fr) 90px;align-items:center;
gap:8px;margin:4px 0}.bar .lab{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar .track{position:relative;height:16px;background:var(--chip);border-radius:4px}
.bar i{position:absolute;left:0;border-radius:4px;height:50%}
.bar .v{text-align:right;font-variant-numeric:tabular-nums}
.legend{display:flex;gap:14px;color:var(--muted);font-size:12px;margin-bottom:8px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px}
ul.att{margin:0;padding-left:18px}ul.att li{margin:2px 0}
.note{color:var(--muted);font-size:12px}.empty{color:var(--muted);font-style:italic}
main{max-width:1460px;padding-left:84px}
.fab{position:fixed;left:18px;z-index:30;width:52px;height:52px;border-radius:50%;
border:1px solid var(--line);cursor:pointer;display:grid;place-items:center;
background:var(--panel);box-shadow:0 4px 14px rgba(15,23,42,.22);
transition:transform .15s ease,box-shadow .15s ease}
.fab:hover{transform:scale(1.06);box-shadow:0 6px 18px rgba(15,23,42,.28)}
.fab:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
#railbtn{top:calc(50% - 60px)}#exportbtn{top:calc(50% + 8px);color:var(--ink)}
#exportmenu{position:fixed;left:82px;top:calc(50% - 4px);z-index:31;background:var(--panel);
border:1px solid var(--line);border-radius:10px;box-shadow:0 8px 24px rgba(15,23,42,.22);
padding:6px;min-width:290px;display:none}
#exportmenu.open{display:block}
#exportmenu .t{font-size:12px;color:var(--muted);padding:6px 10px 4px}
#exportmenu a{display:flex;gap:10px;align-items:center;padding:8px 10px;border-radius:8px;
color:var(--ink);text-decoration:none}
#exportmenu a:hover,#exportmenu a:focus-visible{background:var(--chip);outline:none}
#exportmenu a small{display:block;color:var(--muted);font-size:11px}
#railbtn.ok{color:var(--ok)}#railbtn.bad{color:var(--bad)}
#railbtn .badge{position:absolute;top:-4px;right:-4px;min-width:20px;height:20px;
border-radius:10px;background:#d92d20;color:#fff;font-size:11px;font-weight:700;
display:grid;place-items:center;padding:0 5px;border:2px solid var(--panel)}
#panel{position:fixed;left:0;top:0;bottom:0;width:min(420px,100vw);
background:var(--panel);border-right:1px solid var(--line);z-index:40;overflow-y:auto;
transform:translateX(-105%);transition:transform .25s ease;box-shadow:6px 0 24px rgba(0,0,0,.18);
padding:18px 18px 40px;visibility:hidden}
#panel.open{transform:none;visibility:visible}
#scrim{position:fixed;inset:0;background:rgba(10,15,25,.35);z-index:35;opacity:0;
pointer-events:none;transition:opacity .25s}#scrim.open{opacity:1;pointer-events:auto}
#panel h2{margin:0 0 2px;font-size:17px}#panel .close{float:right;border:0;background:none;
font-size:22px;line-height:1;cursor:pointer;color:var(--muted)}
.ck{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-top:10px}
.ck.bad{border-color:var(--bad);background:transparent;color:var(--ink)}
.ck.ok{background:transparent;color:var(--ink)}
.ck .top{display:grid;grid-template-columns:22px 1fr auto;gap:8px;align-items:start}
.ck .ic{width:20px;height:20px}.ck.ok .ic{color:var(--ok)}.ck.bad .ic{color:var(--bad)}
.ck .val{font-variant-numeric:tabular-nums;color:var(--muted);font-size:12px;white-space:nowrap}
.ck ul{margin:8px 0 0 30px;padding:0;font-size:12.5px}.ck li{margin:2px 0}
.ck .hint{margin:6px 0 0 30px;color:var(--muted);font-size:12px}
.ck button.go{margin:8px 0 0 30px;border:1px solid var(--line);background:var(--chip);
color:var(--ink);border-radius:8px;padding:4px 10px;cursor:pointer;font:inherit;font-size:12px}
section.flash{animation:flash 1.6s ease}
@keyframes flash{0%,40%{box-shadow:0 0 0 3px var(--accent)}100%{box-shadow:none}}
@media (max-width:700px){main{padding-left:16px}.fab{left:14px;width:48px;height:48px}
#railbtn{top:auto;bottom:76px}#exportbtn{top:auto;bottom:18px}
#exportmenu{left:72px;top:auto;bottom:18px}}
</style>
</head>
<body>
<button id="railbtn" class="fab" aria-expanded="false" aria-controls="panel" title="Checks"></button>
<button id="exportbtn" class="fab" aria-expanded="false" aria-controls="exportmenu" title="Export"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg></button>
<div id="exportmenu" role="menu" aria-label="Export"></div>
<div id="scrim"></div>
<aside id="panel" aria-label="Checks" aria-hidden="true">
<button class="close" id="panelclose" aria-label="Close">&times;</button>
<h2>Checks</h2><p class="sub" id="panelsub"></p><div id="cklist"></div>
</aside>
<header>
<div class="logo" id="logo"></div>
<div><h1>KINGSTON MARKETING LIMITED | RISK DASHBOARD</h1><p id="meta"></p></div>
</header>
<main>
<div class="kpis" id="kpis"></div>
<div class="grid2">
<section><h2>Confirmed physical position by delivery month (m3)</h2>
<p class="sub">From the confirmations: bought, sold and net per month and grade, with the
volume-weighted differential (USD/bbl).</p><div id="confbars"></div>
<div class="scroll"><table id="confpos"></table></div></section>
<section><h2 id="sheetposh">Trade sheet position by delivery month (m3)</h2>
<p class="sub" id="sheetsub"></p><div id="sheetbars"></div>
<div class="scroll"><table id="sheetpos"></table></div></section>
</div>
<section id="reconsec"><h2>Trade sheet reconciliation</h2><div class="tabs" id="srctabs"></div>
<p class="sub" id="reconsub"></p><div class="chips" id="chips"></div>
<div class="scroll"><table id="recon"></table></div></section>
<section id="finsec"><h2>Financial swaps (bank)</h2><p class="sub">Not on the trade sheets by design. A
roll is two swaps (four legs) moving exposure from one month to the next; a forward hedge
locks in a price months ahead.</p><div class="scroll"><table id="fin"></table></div>
</section>
<div class="grid2">
<section><h2>Counterparties (confirmed m3)</h2><div class="legend"><span><i
style="background:var(--buy)"></i>bought</span><span><i style="background:var(--sell)"></i>sold
</span></div><div id="cpty"></div></section>
<section><h2>Needs attention</h2><p class="sub">Everything flagged on the checks sheet.</p>
<div id="att"></div></section>
</div>
<p class="note" id="foot"></p>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const num = (v, d=0, maxd=d) => v == null || v === '' ? '' : Number(v).toLocaleString('en-CA', {minimumFractionDigits:d, maximumFractionDigits:maxd});
const statusClass = s => s.startsWith('MATCHED -') || s === 'NOT ON TRADE SHEET' ? 'bad' :
  s.startsWith('NO CONFIRMATION') ? 'warn' : s === 'MATCHED' ? 'ok' : 'mute';
const cap = s => s.charAt(0).toUpperCase() + s.slice(1);

if (D.logo) $('logo').innerHTML = `<img src="${D.logo}" alt="KM">`; else $('logo').remove();
$('meta').textContent = `Position date: ${D.position_date}  |  generated ${D.generated}` +
  (D.sources.length ? '  |  ' + D.sources.map(s => `${s.label}: ${s.files.join(', ')} (saved ${s.modified})`).join('  |  ') : '  |  no trade sheet compared');

const K = D.kpi, kp = [];
kp.push(['Confirmed trades', K.physical + K.financial, `${K.physical} physical, ${K.financial} bank swaps`]);
D.sources.forEach(s => {
  const k = s.kpi, base = K.physical - k.month_not_covered;
  const pct = base ? Math.round(100 * k.matched / base) : 0;
  kp.push([`On ${s.named}`, `${pct}%`, `${k.matched} of ${base} physical; ${k.differences} differ, ${k.not_on_sheet} missing from it, ${k.missing} of its deals unconfirmed` + (k.month_not_covered ? `; ${k.month_not_covered} for months it does not hold` : '')]);
});
if (D.sources.length > 1) kp.push(['Validated on both', K.validated_all, `of ${K.physical} physical confirmations`]);
kp.push(['Bank swaps', num(K.swap_bbl), `bbl; ${K.rolls} roll(s), ${K.hedges} forward hedge(s)`]);
const vol = {}; D.conf_position.forEach(p => vol[p.month] = (vol[p.month] || 0) + p.bought + p.sold);
const main = Object.keys(vol).sort((a, b) => vol[b] - vol[a])[0] || '';
const netMain = D.conf_position.filter(p => p.month === main).reduce((a, p) => a + p.net, 0);
if (main) kp.push([`Confirmed net ${main}`, num(netMain), `m3 (${num(netMain * D.bbl_per_m3)} bbl), long/(short)`]);
$('kpis').innerHTML = kp.map(([l, v, s]) => `<div class="kpi"><span>${esc(l)}</span><b>${esc(v)}</b><small>${esc(s)}</small></div>`).join('');

const ICON_OK = '<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12.5l5 5L20 6.5"/></svg>';
const ICON_FLAG = '<svg class="ic" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M5 2a1 1 0 0 1 1 1v.6c1.3-.6 2.9-.9 4.6-.4 1.3.4 2.3 1.1 3.6 1.4 1.4.3 2.9.1 4.4-.7a1 1 0 0 1 1.4.9v9.4a1 1 0 0 1-.5.9c-1.9 1.1-3.9 1.4-5.8 1-1.5-.3-2.6-1-3.7-1.4-1.3-.4-2.6-.2-4 .5V21a1 1 0 1 1-2 0V3a1 1 0 0 1 1-1z"/></svg>';
const failing = D.checks.filter(c => !c.ok);
const railbtn = $('railbtn'), panel = $('panel'), scrim = $('scrim');
railbtn.classList.add(failing.length ? 'bad' : 'ok');
railbtn.innerHTML = (failing.length ? ICON_FLAG : ICON_OK).replace('class="ic"', 'width="24" height="24"') +
  (failing.length ? `<span class="badge">${failing.length}</span>` : '');
railbtn.title = failing.length ? `${failing.length} check(s) need a look` : 'All checks OK';
$('panelsub').textContent = failing.length ? `${failing.length} of ${D.checks.length} need a look. Each compares a count with its target of 0, like the checks block of the risk report.` : `All ${D.checks.length} checks OK.`;
$('cklist').innerHTML = [...failing, ...D.checks.filter(c => c.ok)].map((c, i) => `<div class="ck ${c.ok ? 'ok' : 'bad'}">
  <div class="top">${c.ok ? ICON_OK : ICON_FLAG}<div>${esc(c.check)}</div><div class="val">${c.value} / ${c.target}</div></div>
  ${c.ok ? '' : `<ul>${c.items.map(x => `<li>${esc(x)}</li>`).join('')}${c.more ? `<li class="note">and ${c.more} more</li>` : ''}</ul>
  ${c.hint ? `<div class="hint">${esc(c.hint)}</div>` : ''}
  ${c.jump ? `<button class="go" data-k="${D.checks.indexOf(c)}">Show me &rarr;</button>` : ''}`}</div>`).join('');
function openPanel(open) {
  panel.classList.toggle('open', open); scrim.classList.toggle('open', open);
  railbtn.setAttribute('aria-expanded', open); panel.setAttribute('aria-hidden', !open);
}
railbtn.onclick = () => openPanel(!panel.classList.contains('open'));
scrim.onclick = $('panelclose').onclick = () => openPanel(false);
const exportbtn = $('exportbtn'), exportmenu = $('exportmenu');
const FILE_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>';
if ((D.exports || []).length) {
  exportmenu.innerHTML = '<div class="t">Export this run</div>' + D.exports.map(x => {
    const pdf = x.file.toLowerCase().endsWith('.pdf');
    return `<a role="menuitem" href="${encodeURIComponent(x.file)}" ${pdf ? 'target="_blank" rel="noopener"' : 'download'}>${FILE_ICON}<span>${esc(x.label)}<small>${esc(x.file)}</small></span></a>`;
  }).join('');
} else { exportbtn.remove(); exportmenu.remove(); }
function openExport(open) {
  if (!exportmenu.isConnected) return;
  exportmenu.classList.toggle('open', open); exportbtn.setAttribute('aria-expanded', open);
}
exportbtn.onclick = e => { e.stopPropagation(); openExport(!exportmenu.classList.contains('open')); };
document.addEventListener('click', e => { if (!e.target.closest('#exportmenu')) openExport(false); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') { openPanel(false); openExport(false); } });
$('cklist').onclick = e => {
  const b = e.target.closest('button.go'); if (!b) return;
  const j = D.checks[+b.dataset.k].jump;
  openPanel(false);
  if (j.section === 'reconsec' && D.sources.length) {
    src = j.source; filter = j.filter;
    [...$('srctabs').children].forEach((x, i) => x.classList.toggle('on', i === src));
    drawSource();
  }
  const sec = $(j.section);
  sec.scrollIntoView({behavior: 'smooth', block: 'start'});
  sec.classList.remove('flash'); void sec.offsetWidth; sec.classList.add('flash');
};

function monthBars(el, rows) {
  const by = {};
  rows.forEach(r => { const m = by[r.month] ||= {b:0, s:0}; m.b += r.bought; m.s += r.sold; });
  const months = Object.keys(by), max = Math.max(1, ...months.map(m => Math.max(by[m].b, by[m].s)));
  $(el).innerHTML = months.length ? '<div class="legend"><span><i style="background:var(--buy)"></i>bought</span><span><i style="background:var(--sell)"></i>sold</span></div>' +
    months.map(m => `<div class="bar"><div class="lab">${esc(m)}</div><div class="track">
      <i style="top:0;width:${100*by[m].b/max}%;background:var(--buy)"></i>
      <i style="top:50%;width:${100*by[m].s/max}%;background:var(--sell)"></i></div>
      <div class="v">${num(by[m].b - by[m].s)}</div></div>`).join('') : '';
}
function posTable(el, rows, diffs) {
  if (!rows.length) { $(el).innerHTML = '<tr><td class="empty">Nothing to show.</td></tr>'; return; }
  $(el).innerHTML = `<tr><th>Month</th><th>Grade</th><th class="n">Bought</th><th class="n">Sold</th><th class="n">Net m3</th><th class="n">Net bbl</th>${diffs ? '<th class="n">Buy diff</th><th class="n">Sell diff</th>' : ''}</tr>` +
    rows.map(r => `<tr><td>${esc(r.month)}</td><td>${esc(r.grade)}</td><td class="n">${num(r.bought)}</td><td class="n">${num(r.sold)}</td><td class="n"><b>${num(r.net)}</b></td><td class="n">${num(r.net * D.bbl_per_m3)}</td>${diffs ? `<td class="n">${num(r.wa_buy_diff, 2)}</td><td class="n">${num(r.wa_sell_diff, 2)}</td>` : ''}</tr>`).join('');
}
monthBars('confbars', D.conf_position); posTable('confpos', D.conf_position, true);

let src = 0, filter = 'IN PERIOD';
const inGroup = (r, f) => f === 'ALL' || (f === 'IN PERIOD' ? !['OUTSIDE CONFIRMATION PERIOD', 'MONTH NOT ON THIS SHEET'].includes(r.status) : f === 'MISSING' ? r.status.startsWith('NO CONFIRMATION') : r.status === f);
function drawSource() {
  const S = D.sources[src];
  $('sheetposh').textContent = `${cap(S.label)}: position by delivery month (m3)`;
  $('sheetsub').textContent = `Sum of its volumes as entered (${S.tabs.join(', ')}), including standing agreements and internal rows.`;
  monthBars('sheetbars', S.position); posTable('sheetpos', S.position, false);
  const p = S.period;
  $('reconsub').textContent = `${S.files.join(', ')}: confirmations ${p ? `dated ${p[0]} to ${p[1]}` : ''} compared with its ${S.rows} rows. Matched on counterparty, side, grade and delivery month; then differential, trade date, broker and volume. ${S.standing} standing (Evergreen/Term) and ${S.internal} internal rows are not expected to have confirmations.` + (S.missing.length ? ` Not in the folder yet: ${S.missing.join(', ')}.` : '');
  const counts = {}; S.recon.forEach(r => counts[r.status] = (counts[r.status] || 0) + 1);
  const missing = S.recon.filter(r => inGroup(r, 'MISSING')).length;
  const chips = [['IN PERIOD', S.recon.filter(r => inGroup(r, 'IN PERIOD')).length], ...(missing ? [['MISSING', missing]] : []), ...Object.entries(counts), ['ALL', S.recon.length]];
  const label = s => s === 'ALL' ? 'Everything' : s === 'IN PERIOD' ? 'In the confirmation period' : s === 'MISSING' ? 'All missing confirmations' : s;
  $('chips').innerHTML = chips.map(([s, n]) => `<button data-s="${esc(s)}" class="${s === filter ? 'on' : ''}">${esc(label(s))} (${n})</button>`).join('');
  drawRecon();
}
function drawRecon() {
  const rows = D.sources[src].recon.filter(r => inGroup(r, filter));
  $('recon').innerHTML = '<tr><th>Status</th><th>Contract #</th><th>Trade sheet #</th><th>Side</th><th>Counterparty</th><th>Grade</th><th>Month</th><th class="n">Confirmed m3</th><th class="n">Sheet m3</th><th class="n">Conf diff</th><th class="n">Sheet diff</th><th>Trade date</th><th>Broker</th><th>Differences / notes</th></tr>' +
    (rows.length ? rows.map(r => `<tr><td><span class="pill ${statusClass(r.status)}">${esc(r.status)}</span></td><td>${esc(r.contract)}</td><td>${esc(r.sheet_contract)}<div class="note">${esc(r.where)}</div></td><td>${esc(r.side)}</td><td>${esc(r.counterparty)}</td><td>${esc(r.grade)}</td><td>${esc(r.month)}</td><td class="n">${num(r.conf_qty)}</td><td class="n">${num(r.sheet_qty, 0, 2)}</td><td class="n">${num(r.conf_diff, 2)}</td><td class="n">${num(r.sheet_diff, 2)}</td><td>${esc(r.conf_date || r.sheet_date)}${r.conf_date && r.sheet_date && r.conf_date !== r.sheet_date ? ' / ' + esc(r.sheet_date) : ''}</td><td>${esc(r.conf_broker || r.sheet_broker)}${r.conf_broker && r.sheet_broker ? ' / ' + esc(r.sheet_broker) : ''}</td><td class="wrap">${r.differences.map(d => `<div class="pill bad" style="white-space:normal">${esc(d)}</div>`).join('')}${r.notes.map(n => `<div class="note">${esc(n)}</div>`).join('')}</td></tr>`).join('')
     : '<tr><td colspan="14" class="empty">Nothing in this group.</td></tr>');
}
if (D.sources.length) {
  $('srctabs').innerHTML = D.sources.map((s, i) => `<button data-i="${i}" class="${i === src ? 'on' : ''}">${esc(cap(s.label))}</button>`).join('');
  $('srctabs').onclick = e => { const b = e.target.closest('button'); if (!b) return; src = +b.dataset.i;
    [...$('srctabs').children].forEach(x => x.classList.toggle('on', x === b)); drawSource(); };
  $('chips').onclick = e => { const b = e.target.closest('button'); if (!b) return; filter = b.dataset.s;
    [...$('chips').children].forEach(x => x.classList.toggle('on', x === b)); drawRecon(); };
  drawSource();
} else {
  const errs = Object.entries(D.sheet_errors);
  $('sheetsub').textContent = 'No trade sheet was compared in this run.';
  $('reconsub').textContent = errs.length ? errs.map(([l, e]) => `${cap(l)} could not be read: ${e}`).join(' ') : 'No trade sheet folder is set up, so nothing was compared. Run SETUP.bat to choose it.';
}

$('fin').innerHTML = D.financial.length ? '<tr><th>Contract #</th><th>Type</th><th>Paired with</th><th>Side</th><th>Month</th><th class="n">Quantity</th><th class="n">Fixed price</th><th>Counterparty</th><th>Trade date</th><th>What it is</th></tr>' +
  D.financial.map(f => `<tr><td>${esc(f.contract)}</td><td><span class="pill ${f.kind === 'SWAP' ? 'bad' : 'ok'}">${esc(f.kind)}</span></td><td>${esc(f.partner)}</td><td>${esc(f.side)}</td><td>${esc(f.term)}</td><td class="n">${num(f.quantity)} ${esc(f.unit)}</td><td class="n">${num(f.fixed_price, 2)} ${esc(f.price_unit)}</td><td>${esc(f.counterparty)}</td><td>${esc(f.trade_date)}</td><td class="note wrap">${esc(f.note)}</td></tr>`).join('')
  : '<tr><td class="empty">No bank swaps in this run.</td></tr>';

const cmax = Math.max(1, ...D.counterparties.map(c => Math.max(c.bought, c.sold)));
$('cpty').innerHTML = D.counterparties.map(c => `<div class="bar"><div class="lab" title="${esc(c.name)}">${esc(c.name)}</div><div class="track">
  <i style="top:0;width:${100*c.bought/cmax}%;background:var(--buy)"></i>
  <i style="top:50%;width:${100*c.sold/cmax}%;background:var(--sell)"></i></div>
  <div class="v">${c.trades} trade${c.trades === 1 ? '' : 's'}</div></div>`).join('') || '<p class="empty">No physical trades.</p>';

$('att').innerHTML = D.attention.length ? '<ul class="att">' + D.attention.map(a => `<li><b>${esc(a.contract)}</b> ${esc(a.counterparty)}<ul>${a.flags.map(f => `<li>${esc(f)}</li>`).join('')}</ul></li>`).join('') + '</ul>' : '<p class="empty">Nothing flagged.</p>';
$('foot').textContent = `Source: ${D.source}  |  ${D.build}  |  m3 to bbl at ${D.bbl_per_m3}`;
</script>
</body>
</html>
"""
