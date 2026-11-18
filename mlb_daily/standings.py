"""戰績表的抓取與渲染。"""

import re

from . import htmlutil, net, storage
from .config import (
    CLINCH_LABELS,
    CLINCH_LEGEND,
    DIVISION_LAYOUT,
    NL_DIVISION_IDS,
    STANDINGS_URL,
    TEAM_META,
)


def fetch_standings(season):
    """抓取例行賽戰績。失敗（無回應、結構不符、解析不到任何球隊）回傳 None。

    成功回傳 {"season", "divisions": [{"id", "name", "teams": [...]}], "records": {team_id: (W, L)}}。
    分區以 API 的 division id 分組並照 DIVISION_LAYOUT 排序；每區依 divisionRank 排序；隊伍以 team id 對 TEAM_META。"""
    season = htmlutil._int_id(season)
    if season is None:
        return None
    data = net.fetch_json(STANDINGS_URL.format(season=season))
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        return None
    by_div = {}
    records = {}
    for rec in data["records"]:
        if not isinstance(rec, dict):
            continue
        div_id = htmlutil._int_id((rec.get("division") or {}).get("id"))
        for tr in rec.get("teamRecords") or []:
            if not isinstance(tr, dict):
                continue
            tid = htmlutil._int_id((tr.get("team") or {}).get("id"))
            if div_id is None or tid not in TEAM_META:
                continue
            wins, losses = storage._to_int(tr.get("wins")), storage._to_int(tr.get("losses"))
            pct = tr.get("winningPercentage")
            if not pct:
                pct = f"{wins / (wins + losses):.3f}".lstrip("0") if wins + losses else ".000"
            last_ten = "–"
            for split in (tr.get("records") or {}).get("splitRecords") or []:
                if isinstance(split, dict) and split.get("type") == "lastTen":
                    last_ten = f"{storage._to_int(split.get('wins'))}-{storage._to_int(split.get('losses'))}"
                    break
            gb = tr.get("gamesBack")
            clinch = tr.get("clinchIndicator")
            by_div.setdefault(div_id, []).append({
                "id": tid,
                "wins": wins,
                "losses": losses,
                "pct": str(pct),
                "gb": "–" if gb in (None, "", "-") else str(gb),
                "last_ten": last_ten,
                "streak": str((tr.get("streak") or {}).get("streakCode") or "–"),
                "clinch": str(clinch) if clinch else "",
                "rank": storage._to_int(tr.get("divisionRank"), 99),
            })
            records[tid] = (wins, losses)
    divisions = []
    for div_id, name in DIVISION_LAYOUT:
        teams = sorted(by_div.get(div_id, []), key=lambda t: t["rank"])
        if teams:
            divisions.append({"id": div_id, "name": name, "teams": teams})
    if not divisions:
        return None
    return {"season": season, "divisions": divisions, "records": records}


def render_standings_row(t):
    meta = TEAM_META[t["id"]]
    stripe = htmlutil.stripe_class(meta["color"]).strip()
    clinch = t["clinch"]
    label = htmlutil.esc(meta["name"])
    sup = ""
    if clinch:
        title = CLINCH_LABELS.get(clinch)
        if title:
            label = f"{label}，{htmlutil.esc(title)}"
            sup = f'<sup class="st-clinch" title="{htmlutil.esc(title)}" aria-hidden="true">{htmlutil.esc(clinch)}</sup>'
        else:
            label = f"{label}，標記 {htmlutil.esc(clinch)}"
            sup = f'<sup class="st-clinch" aria-hidden="true">{htmlutil.esc(clinch)}</sup>'
    streak = t["streak"]
    streak_cls = " is-w" if re.fullmatch(r"W\d+", streak) else (" is-l" if re.fullmatch(r"L\d+", streak) else "")
    cls = f' class="{stripe}"' if stripe else ""
    return (
        f'<tr class="st-row"><th scope="row"{cls} style="--team:{htmlutil.safe_color(meta["color"])}">'
        f'<a class="st-team" href="teams.html#{htmlutil.esc(meta["abbr"])}" aria-label="{label}" title="{htmlutil.esc(meta["name"])}">'
        f'{htmlutil.logo_picture(t["id"], meta["abbr"], 18, alt="")}<span>{htmlutil.esc(meta["abbr"])}</span>{sup}</a></th>'
        f'<td>{t["wins"]}</td><td>{t["losses"]}</td><td class="is-pct">{htmlutil.esc(t["pct"])}</td>'
        f'<td class="is-sub">{htmlutil.esc(t["gb"])}</td><td class="is-sub">{htmlutil.esc(t["last_ten"])}</td>'
        f'<td class="st-streak{streak_cls}">{htmlutil.esc(streak)}</td></tr>'
    )


def render_standings_division(div, league_start):
    div_id = int(div["id"])
    extra = " st-div--league-start" if league_start else ""
    rows = "\n            ".join(render_standings_row(t) for t in div["teams"])
    name = htmlutil.esc(div["name"])
    return f"""      <section class="st-div{extra}" aria-labelledby="st-d{div_id}">
        <h3 id="st-d{div_id}">{name}</h3>
        <table class="st-table">
          <caption class="visually-hidden">{name}戰績</caption>
          <colgroup><col><col class="c-w"><col class="c-l"><col class="c-pct"><col class="c-gb"><col class="c-l10"><col class="c-strk"></colgroup>
          <thead><tr><th scope="col">隊</th><th scope="col">W</th><th scope="col">L</th><th scope="col">PCT</th><th scope="col">GB</th><th scope="col">L10</th><th scope="col">STRK</th></tr></thead>
          <tbody>
            {rows}
          </tbody>
        </table>
      </section>"""


STANDINGS_ICON = ('<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" '
                  'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
                  '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>')


def render_standings_aside(standings, season, in_season, now):
    """戰績側邊欄 <aside>。standings 為 None（抓取失敗）時輸出「戰績暫時無法取得」版本。"""
    season_int = htmlutil._int_id(season)
    title = f"{season_int} 例行賽戰績" if in_season else f"{season_int} 例行賽最終戰績"
    if standings:
        meta_line = (f'\n        <p class="st-meta">資料來源 MLB Stats API · 更新於 '
                     f'<span class="num">{now.month}/{now.day} {now:%H:%M}</span></p>')
        divisions = standings["divisions"]
        parts = []
        for i, div in enumerate(divisions):
            league_start = div["id"] in NL_DIVISION_IDS and i > 0 and divisions[i - 1]["id"] not in NL_DIVISION_IDS
            parts.append(render_standings_division(div, league_start))
        # 圖例只列出當前資料中實際出現的標記
        present = {t["clinch"] for div in divisions for t in div["teams"]}
        legend_items = "".join(f"<dt>{htmlutil.esc(k)}</dt><dd>{htmlutil.esc(v)}</dd>" for k, v in CLINCH_LEGEND if k in present)
        legend = (f'\n      <div class="st-legend">\n        <h3>標記說明</h3>\n        <dl>{legend_items}</dl>\n      </div>'
                  if legend_items else "")
        body = '    <div class="st-body st-data">\n' + "\n".join(parts) + legend + '\n    </div>'
        aside_cls = "standings"
    else:
        meta_line = ""
        body = ('    <div class="st-error" role="status">\n'
                '      <strong>戰績暫時無法取得</strong>\n'
                '      <p>資料來源暫時沒有回應，不影響每日戰報。將於下次更新時重新取得。</p>\n'
                '    </div>')
        aside_cls = "standings is-error"
    return f"""<aside class="{aside_cls}" id="standings" aria-labelledby="standings-title">
  <button class="st-rail" type="button" data-st="expand" aria-expanded="false" aria-controls="standings-panel" aria-label="展開戰績側邊欄" title="戰績">
    {STANDINGS_ICON}
  </button>
  <div class="st-panel" id="standings-panel">
    <div class="st-head">
      <div>
        <h2 class="st-title" id="standings-title">{htmlutil.esc(title)}</h2>{meta_line}
      </div>
      <div class="st-actions">
        <button class="st-btn" type="button" data-st="collapse" aria-expanded="true" aria-controls="standings-panel">收合 <span aria-hidden="true">›</span></button>
        <button class="st-btn st-close" type="button" aria-label="關閉戰績">✕</button>
      </div>
    </div>
{body}
  </div>
</aside>"""


def render_standings_fab():
    return (f'<button class="st-fab" type="button" data-st="open" aria-expanded="false" aria-controls="standings" '
            f'aria-haspopup="dialog" aria-label="戰績" title="戰績">{STANDINGS_ICON}</button>\n'
            f'<div class="st-backdrop" aria-hidden="true"></div>')
