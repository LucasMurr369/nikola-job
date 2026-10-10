"""Nikola HTML performance report generator (--export-html).

Single-file, dependency-free (stdlib only) generator that renders a clean,
dark single-page report from ``data/paper_state.json``:

  * headline summary      (cash, starting, net P&L, trades, win rate, record)
  * equity curve          (inline SVG, cumulative realized P&L over time)
  * active trailing stops (live SL1/SL2/TP1/TP2 geometry, or last-trade profile)
  * recent trades table   (last 8 closed trades)
  * God Code quote        (a wisdom line from agents/god_code.py)
  * Tithe / Offering      (PayPal link + scannable QR via qrserver.com image)

Run standalone:
    python html_report.py                       # -> nikola_report.html
    python html_report.py --out foo.html
    python nikola.py --export-html              # from the bot CLI

Zero external Python packages required. The only external network *image* is
the PayPal QR (qrserver.com) per the project's zero-dependency Option A; every
chart/table/quote is rendered inline, so the file opens in any browser and is
printable to PDF without a network connection.
"""
from __future__ import annotations

import html
import json
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

# Make sibling packages importable when run as `python html_report.py`.
_BASE_DIR = Path(__file__).resolve().parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

# ---------------------------------------------------------------------------
# Public support links (Tithe / Offering / Donation — never a "tip")
# ---------------------------------------------------------------------------
PAYPAL_LINK = "https://www.paypal.me/LucasMurr369"
PAYPAL_EMAIL = "donations@nikola-bot.org"


def _paypal_qr_url(link: str = PAYPAL_LINK, size: int = 200) -> str:
    """Render a scannable PayPal QR as a qrserver.com image URL (Option A)."""
    return (
        "https://api.qrserver.com/v1/create-qr-code/"
        f"?size={size}x{size}&data={urllib.parse.quote(link, safe='')}"
    )


def _donation_info() -> dict:
    """PayPal Tithe info for the report footer. Prefers donation_monitor (the
    single source of truth) and falls back to the static link + generated QR."""
    try:
        from donation_monitor import paypal_donation_info  # type: ignore
        return paypal_donation_info()
    except Exception:
        return {
            "donate_link": PAYPAL_LINK,
            "email": PAYPAL_EMAIL,
            "qr_url": _paypal_qr_url(),
            "qr_alt": "Tithe / Donation via PayPal - scan to give",
        }


def _god_code_quote() -> tuple[str, str]:
    """Pull a God Code wisdom line for the report (graceful fallback)."""
    try:
        from agents.god_code import GodCodeAgent  # type: ignore
        agent = GodCodeAgent(faith_tradition="all")
        passage, source = agent.current_quote
        return passage, source
    except Exception:
        return "Do the thing -- then let the bot watch the math. -- Nikola", "System"


# ---------------------------------------------------------------------------
# State loading
# ---------------------------------------------------------------------------
def _state_path() -> Path:
    try:
        from engine import config as cfg  # type: ignore
        return cfg.PAPER_STATE_FILE
    except Exception:
        return _BASE_DIR / "data" / "paper_state.json"


def _load_state(path) -> dict:
    p = Path(path) if path else _state_path()
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _esc(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def _money(v) -> str:
    try:
        return f"${float(v):,.2f}"
    except Exception:
        return _esc(v)


def _pn(t: dict) -> float:
    try:
        return float(t.get("pnl_usd", 0) or 0)
    except Exception:
        return 0.0


def _cash(data: dict) -> float:
    try:
        return float(data.get("cash_usd", 0) or 0)
    except Exception:
        return 0.0


def _initial(data: dict) -> float:
    try:
        return float(data.get("initial_cash_usd",
                              data.get("initial_cash", 0)) or 0)
    except Exception:
        return 0.0


def _direction(d) -> str:
    return "LONG" if str(d).upper().startswith("L") else "SHORT"


def _perf_summary(data: dict) -> dict:
    trades = data.get("trade_history", []) or []
    wins = [t for t in trades if _pn(t) > 0]
    losses = [t for t in trades if _pn(t) < 0]
    rs = [t.get("r_multiple") for t in trades if t.get("r_multiple") is not None]
    avg_r = (sum(rs) / len(rs)) if rs else None
    best = max(trades, key=_pn, default=None)
    worst = min(trades, key=_pn, default=None)
    cash = _cash(data)
    initial = _initial(data)
    return {
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate": (len(wins) / len(trades) * 100.0) if trades else 0.0,
        "total_pnl": sum(_pn(t) for t in trades), "avg_r": avg_r,
        "best": best, "worst": worst, "cash": cash, "initial": initial,
        "net": cash - initial,
    }


def _short_time(ts) -> str:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt.strftime("%m/%d %H:%M")
    except Exception:
        return str(ts)[:16]


def _equity_points(data: dict) -> list[tuple[int, float, str]]:
    """Cumulative paper equity over closed trades (start at initial cash)."""
    trades = sorted(data.get("trade_history", []) or [],
                    key=lambda t: t.get("time") or "")
    initial = _initial(data)
    pts: list[tuple[int, float, str]] = [(0, round(initial, 2), "start")]
    cum = initial
    for i, t in enumerate(trades, start=1):
        cum += _pn(t)
        ts = t.get("time") or ""
        label = _short_time(ts) if ts else f"trade {i}"
        pts.append((i, round(cum, 2), label))
    cash = _cash(data)
    if not pts or pts[-1][1] != round(cash, 2):
        pts.append((len(trades), round(cash, 2), "now"))
    return pts


def _svg_curve(points, width: int = 720, height: int = 220) -> str:
    pad_l, pad_r, pad_t, pad_b = 42, 16, 18, 28
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    if len(points) < 2:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
                f'height="{height}" viewBox="0 0 {width} {height}">'
                f'<rect width="{width}" height="{height}" fill="#0f141c" rx="8"/>'
                f'<text x="{width/2}" y="{height/2}" font-size="13" fill="#94a3b8" '
                f'text-anchor="middle">Open a paper position to draw the equity curve.</text>'
                f'</svg>')
    vals = [p[1] for p in points]
    vmin, vmax = min(vals), max(vals)
    rng = (vmax - vmin) or 1.0
    n = len(points)

    def xy(i, v):
        x = pad_l + (i / (n - 1)) * plot_w
        y = pad_t + (1 - (v - vmin) / rng) * plot_h
        return f"{x:.1f},{y:.1f}"

    pline = " ".join(xy(i, v) for i, v, _ in points)
    last = pline.split()[-1].split(",")
    green = "#4ade80"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}" '
        f'style="background:#0b0f14;border-radius:8px;display:block">'
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#0f141c" rx="8"/>'
        f'<polyline points="{pline}" fill="none" stroke="{green}" '
        f'stroke-width="2.5" stroke-linejoin="round"/>'
        f'<line x1="{pad_l}" y1="{pad_t+plot_h}" x2="{pad_l+plot_w}" '
        f'y2="{pad_t+plot_h}" stroke="#334153"/>'
        f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t+plot_h}" '
        f'stroke="#334153"/>'
        f'<text x="6" y="{pad_t+11}" font-size="11" fill="#94a3b8">{_money(vmin)}</text>'
        f'<text x="6" y="{pad_t+plot_h-2}" font-size="11" fill="#94a3b8">'
        f'{_money(vmax)}</text>'
        f'<circle cx="{last[0]}" cy="{last[1]}" r="3.5" fill="{green}"/>'
        f'</svg>'
    )


def _render_positions(data: dict) -> str:
    active = data.get("active_positions", []) or []
    if active:
        rows = []
        for p in active:
            rows.append(
                f"<tr><td>{_esc(p.get('pair','?'))}</td>"
                f"<td>{_direction(p.get('direction'))}</td>"
                f"<td>{_money(p.get('entry_price'))}</td>"
                f"<td>{_money(p.get('current_price', p.get('mark_price', p.get('price'))))}</td>"
                f"<td>{_money(p.get('sl1_price'))}</td><td>{_money(p.get('sl2_price'))}</td>"
                f"<td>{_money(p.get('tp1_price'))}</td><td>{_money(p.get('tp2_price'))}</td>"
                f"<td>{_esc(p.get('r_multiple'))}</td>"
                f"<td>{_esc(p.get('leverage', p.get('pair_leverage')))}</td></tr>"
            )
        body = "\n".join(rows)
        table = (
            "<table><thead><tr><th>Pair</th><th>Side</th><th>Entry</th><th>Now</th>"
            "<th>SL1 -2.1%</th><th>SL2 -4.4% (exit)</th><th>TP1 +1.4%</th>"
            "<th>TP2 +2.8%</th><th>R</th><th>Lev</th></tr></thead>"
            f"<tbody>{body}</tbody></table>"
        )
        return "<h3>Active Trailing Stops</h3>" + table
    trades = data.get("trade_history", []) or []
    last = trades[-1] if trades else None
    if not last:
        note = ("<p class='muted'>No positions yet. Open a paper position and re-export "
                "to see live SL1/SL2/TP1/TP2 trailing geometry.</p>")
    else:
        note = (
            f"<p class='muted'>No open positions in the persisted snapshot "
            f"(trailing stops inactive). Last position profile: "
            f"<b>{_esc(last.get('pair'))} {_direction(last.get('direction'))}</b> "
            f"entry {_money(last.get('entry_price'))} &rarr; exit "
            f"{_money(last.get('exit_price'))} ({_money(_pn(last))}). Bot standard "
            f"geometry: SL1 -2.1% (partial), SL2 -4.4% (full exit), TP1 +1.4%/40%, "
            f"TP2 +2.8%/40%, 20% runner trails.</p>"
        )
    return "<h3>Active Trailing Stops</h3>" + note


def _render_trades(trades: list, n: int = 8) -> str:
    if not trades:
        return "<p class='muted'>No closed trades yet.</p>"
    rows = []
    for t in trades[-n:]:
        pnl = _pn(t)
        color = "#4ade80" if pnl > 0 else "#f87171"
        rows.append(
            f"<tr><td>{_esc(t.get('pair','?'))}</td><td>{_direction(t.get('direction'))}</td>"
            f"<td>{_money(t.get('entry_price'))}</td><td>{_money(t.get('exit_price'))}</td>"
            f"<td style='color:{color}'>{_money(pnl)}</td><td>{_esc(t.get('r_multiple'))}</td>"
            f"<td>{_esc(t.get('time'))}</td></tr>"
        )
    body = "\n".join(rows)
    return (
        "<h3>Recent Trades</h3><table><thead><tr><th>Pair</th><th>Side</th>"
        "<th>Entry</th><th>Exit</th><th>P&L</th><th>R</th><th>Time</th></tr>"
        f"</thead><tbody>{body}</tbody></table>"
    )


# ---------------------------------------------------------------------------
# HTML assembly
# ---------------------------------------------------------------------------
_CSS = """
  :root{--bg:#0b0f14;--card:#141923;--ink:#e2e8f0;--muted:#94a3b8;--line:#334153;
  --green:#4ade80;--red:#f87171;--gold:#f59e0b;--blue:#38bdf8;}
  body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
  font-size:14px;line-height:1.5;padding:24px;}
  .wrap{max-width:920px;margin:0 auto;}
  header{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;}
  h1{font-size:20px;margin:0;}
  h1 .logo{color:var(--gold);}
  .subtitle{color:var(--muted);font-size:13px;margin:4px 0 14px;}
  .grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:14px 0;}
  .stat{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:12px;text-align:center;}
  .stat .v{font-size:22px;font-weight:700;}
  .stat .l{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em;}
  .pos{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:14px;margin:14px 0;}
  h3{margin:18px 0 8px;font-size:16px;}
  table{width:100%;border-collapse:collapse;font-size:13px;}
  th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);
  font-variant-numeric:tabular-nums;}
  th{color:var(--muted);font-weight:600;text-transform:uppercase;font-size:11px;}
  .muted{color:var(--muted);}
  .quote{background:linear-gradient(135deg,#141923,#0f141c);border:1px solid var(--line);
  border-left:3px solid var(--gold);border-radius:10px;padding:16px 18px;margin:16px 0;
  font-style:italic;}
  .quote .by{color:var(--gold);font-style:normal;font-weight:600;}
  .tithe{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:16px;display:flex;align-items:center;gap:14px;}
  .tithe img{background:#fff;padding:6px;border-radius:6;width:110px;height:110px;}
  .actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:10px;}
  a.btn{background:var(--gold);color:#0b0f14;font-weight:700;padding:8px 14px;border-radius:8px;
  text-decoration:none;font-size:13px;}
  footer{color:var(--muted);font-size:12px;margin-top:22px;text-align:center;}
"""


def _build_html(data: dict) -> str:
    s = _perf_summary(data)
    pts = _equity_points(data)
    passage, source = _god_code_quote()
    don = _donation_info()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    mode = _esc(data.get("mode") or "paper")
    cycle = data.get("cycle", 0)
    net_color = "#4ade80" if s["net"] >= 0 else "#f87171"
    cash = s["cash"]

    stats = f"""
      <div class="grid">
        <div class="stat"><div class="v">{_money(cash)}</div><div class="l">Cash</div></div>
        <div class="stat"><div class="v">{_money(s['initial'])}</div><div class="l">Starting</div></div>
        <div class="stat"><div class="v" style="color:{net_color}">{_money(s['net'])}</div><div class="l">Net P&amp;L</div></div>
        <div class="stat"><div class="v">{s['trades']}</div><div class="l">Trades</div></div>
        <div class="stat"><div class="v">{s['win_rate']:.1f}%</div><div class="l">Win Rate</div></div>
        <div class="stat"><div class="v">{s['wins']}W-{s['losses']}L</div><div class="l">Record</div></div>
        <div class="stat"><div class="v">{_esc(source)}</div><div class="l">Quote</div></div>
      </div>"""

    curve = _svg_curve(pts)
    equity_now = _money(pts[-1][1]) if pts else _money(cash)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nikola - Performance Report</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <header>
    <div><h1><span class="logo">&#9889;</span> Nikola</h1>
      <div class="subtitle">Performance Report &middot; {mode} &middot; CYCLE {cycle} &middot; {now}</div>
    </div>
    <a class="btn" href="{_esc(don.get('donate_link'))}" target="_blank" rel="noopener">Tithe via PayPal</a>
  </header>

  {stats}

  <div class="pos">
    <h3>Equity Curve</h3>
    <p class="muted">Cumulative paper equity from closed trades (start {_money(s['initial'])}, now {equity_now}).</p>
    {curve}
  </div>

  <div class="pos">
    {_render_positions(data)}
  </div>

  <div class="pos">
    {_render_trades(data.get("trade_history", []) or [])}
  </div>

  <div class="pos quote">
    <span class="quote">&#8220;{_esc(passage)}&#8221;<br><span class="by">&#8212; {source}</span> &middot; God Code (all wisdom wells)</span>
  </div>

  <div class="tithe">
    <img src="{_esc(don.get('qr_url'))}" alt="{_esc(don.get('qr_alt','Tithe / Donation via PayPal - scan to give'))}">
    <div>
      <div><b>Tithe / Offering / Donation</b> — 100% free software, never required.</div>
      <div class="muted" style="font-size:13px">Nikola is donationware: keep what you use, give if it helped you.</div>
      <div class="muted" style="margin-top:6px">1) <b>Click:</b> <a href="{_esc(don.get('donate_link'))}" target="_blank" rel="noopener">paypal.me/LucasMurr369</a></div>
      <div class="muted">2) <b>Scan:</b> open the PayPal QR above in your phone camera</div>
      <div class="muted">3) <b>Crypto:</b> <code>python3 donation_monitor.py info</code> for wallet / Kraken memo / Lightning</div>
      <div class="muted" style="font-size:12px;margin-top:6px">Questions? {_esc(don.get('email'))}</div>
    </div>
  </div>

  <footer>Generated by Nikola — free, open-source, no App Store. No tithe, offering, or donation is required.</footer>
</div>
</body>
</html>"""


def generate_html_report(state_path=None, output_path=None) -> str:
    """Render a single-file HTML report. Returns the absolute output path."""
    data = _load_state(state_path)
    out = output_path or "nikola_report.html"
    out_path = Path(out)
    out_path.write_text(_build_html(data), encoding="utf-8")
    return str(out_path.resolve())


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Render a Nikola HTML performance report.")
    ap.add_argument("--out", default="nikola_report.html", help="Output HTML path.")
    ap.add_argument("--state", default=None,
                    help="paper_state.json path (default: data/paper_state.json).")
    a = ap.parse_args()
    path = generate_html_report(state_path=a.state, output_path=a.out)
    print(f"HTML report written: {path}")


if __name__ == "__main__":
    main()
