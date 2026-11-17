/* 球隊名單頁（設計稿）：讀取 teams.sample.json，渲染、排序、hash 導覽。
   所有資料文字一律用 textContent / setAttribute，不拼 innerHTML。 */
(function () {
  "use strict";

  var DATA_URL = "assets/teams.sample.json";
  var LOGO_DARK = "https://www.mlbstatic.com/team-logos/team-cap-on-dark/";
  var LOGO_LIGHT = "https://www.mlbstatic.com/team-logos/team-cap-on-light/";
  var SAVANT = "https://baseballsavant.mlb.com/savant-player/";
  var DASH = "—";

  var GROUPS = [
    ["SP", "SP 先發投手"], ["RP", "RP 後援投手"], ["C", "C 捕手"], ["1B", "1B 一壘手"],
    ["2B", "2B 二壘手"], ["3B", "3B 三壘手"], ["SS", "SS 游擊手"], ["LF", "LF 左外野手"],
    ["CF", "CF 中外野手"], ["RF", "RF 右外野手"], ["DH", "DH 指定打擊"], ["UTIL", "UTIL 多功能（含二刀流）"]
  ];
  var DIVISIONS = [
    ["AL", "E", "美聯東區"], ["AL", "C", "美聯中區"], ["AL", "W", "美聯西區"],
    ["NL", "E", "國聯東區"], ["NL", "C", "國聯中區"], ["NL", "W", "國聯西區"]
  ];
  var ROSTER_LABEL = { active: "26 人現役名單", "40Man": "40 人名單" };

  /* ---------- 格式化 ---------- */
  function fixed(d) { return function (n) { return n == null ? DASH : n.toFixed(d); }; }
  function rate3(n) { return n == null ? DASH : (n < 1 ? n.toFixed(3).replace(/^0/, "") : n.toFixed(3)); }
  function pct(n) { return n == null ? DASH : n.toFixed(1) + "%"; }
  function int(n) { return n == null ? DASH : String(Math.round(n)); }
  function str(s) { return s == null || s === "" ? DASH : String(s); }
  function ipOuts(s) {            // "182.1" -> 547 出局數，供排序
    if (s.ip == null) return null;
    var p = String(s.ip).split(".");
    return parseInt(p[0], 10) * 3 + (p[1] ? parseInt(p[1], 10) : 0);
  }

  /* ---------- 欄位定義：k 資料欄位、fmt 顯示文字、sort 取排序值 ---------- */
  function col(k, label, fmt, tip, sortFn) {
    return { k: k, label: label, fmt: fmt, tip: tip, sort: sortFn || function (s) { return s[k]; } };
  }
  var HIT_COLS = [
    col("g", "G", int, "出賽場數"), col("pa", "PA", int, "打席"),
    col("avg", "AVG", rate3, "打擊率"), col("obp", "OBP", rate3, "上壘率"), col("slg", "SLG", rate3, "長打率"),
    col("hr", "HR", int, "全壘打"), col("sb", "SB", int, "盜壘"),
    col("bbPct", "BB%", pct, "保送率"), col("kPct", "K%", pct, "三振率"),
    col("wrcPlus", "wRC+", int, "加權得分創造（100 為聯盟平均）"), col("war", "WAR", fixed(1), "勝場貢獻值")
  ];
  var SP_COLS = [
    col("g", "G", int, "出賽場數"), col("gs", "GS", int, "先發場數"),
    col("ip", "IP", str, "投球局數", ipOuts),
    col("w", "W-L", function (n, s) { return s.w == null ? DASH : s.w + "-" + s.l; }, "勝敗", function (s) { return s.w; }),
    col("era", "ERA", fixed(2), "防禦率"), col("whip", "WHIP", fixed(2), "每局被上壘率"),
    col("so", "SO", int, "三振"), col("k9", "K/9", fixed(1), "每九局三振"),
    col("fip", "FIP", fixed(2), "投手獨立防禦率"), col("war", "WAR", fixed(1), "勝場貢獻值")
  ];
  var RP_COLS = [
    col("g", "G", int, "出賽場數"),
    col("ip", "IP", str, "投球局數", ipOuts),
    col("sv", "SV", int, "救援成功"), col("hld", "HLD", int, "中繼成功"),
    col("era", "ERA", fixed(2), "防禦率"), col("whip", "WHIP", fixed(2), "每局被上壘率"),
    col("so", "SO", int, "三振"), col("k9", "K/9", fixed(1), "每九局三振"),
    col("fip", "FIP", fixed(2), "投手獨立防禦率")
  ];

  /* ---------- 狀態 ---------- */
  var state = { data: null, current: null, sorts: {} };   // sorts[表格鍵] = {key, dir}
  function $(id) { return document.getElementById(id); }

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    return e;
  }

  function logo(team, size) {
    var pic = el("picture");
    pic.appendChild(el("source", { media: "(prefers-color-scheme: light)", srcset: LOGO_LIGHT + team.id + ".svg" }));
    pic.appendChild(el("img", { src: LOGO_DARK + team.id + ".svg", width: size, height: size, alt: team.abbr }));
    return pic;
  }

  function isDarkColor(hex) {
    var m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return false;
    var c = [0, 2, 4].map(function (i) {
      var v = parseInt(m[1].substr(i, 2), 16) / 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] < 0.08;
  }

  /* ---------- 隊徽選單 ---------- */
  function buildPicker(teams) {
    var picker = $("picker");
    DIVISIONS.forEach(function (d) {
      var group = el("div", { "class": "pick-group", role: "group", "aria-label": d[2] });
      group.appendChild(el("h3", null, d[2]));
      var row = el("div", { "class": "pick-row" });
      teams.filter(function (t) { return t.league === d[0] && t.division === d[1]; }).forEach(function (t) {
        var b = el("button", { type: "button", "class": "team-btn", "data-abbr": t.abbr, "aria-pressed": "false", "aria-label": t.name, title: t.name });
        b.appendChild(logo(t, 24));
        b.appendChild(el("span", null, t.abbr));
        b.addEventListener("click", function () { location.hash = t.abbr; });
        row.appendChild(b);
      });
      group.appendChild(row);
      picker.appendChild(group);
    });
  }

  function buildSelect(teams) {
    var sel = $("teamSelect");
    var ph = el("option", { value: "" }, "請選擇球隊…");
    sel.appendChild(ph);
    DIVISIONS.forEach(function (d) {
      var og = el("optgroup", { label: d[2] });
      teams.filter(function (t) { return t.league === d[0] && t.division === d[1]; }).forEach(function (t) {
        og.appendChild(el("option", { value: t.abbr }, t.abbr + " " + t.name));
      });
      sel.appendChild(og);
    });
    sel.addEventListener("change", function () { if (sel.value) location.hash = sel.value; });
  }

  function markPicker(abbr) {
    var sel = $("teamSelect");
    sel.value = Array.prototype.some.call(sel.options, function (o) { return o.value === abbr; }) ? abbr : "";
    Array.prototype.forEach.call(document.querySelectorAll(".team-btn"), function (b) {
      b.setAttribute("aria-pressed", b.getAttribute("data-abbr") === abbr ? "true" : "false");
    });
  }

  /* ---------- 排序 ---------- */
  function sortRows(rows, cols, st) {
    var c = cols.filter(function (x) { return x.k === st.key; })[0];
    function val(r) {
      if (st.key === "_name") return r.name.toLowerCase();
      if (st.key === "_num") { var n = parseInt(r.number, 10); return isNaN(n) ? null : n; }
      return r.stats ? c.sort(r.stats) : null;
    }
    return rows.slice().sort(function (a, b) {
      var x = val(a), y = val(b);
      if (x == null && y == null) return 0;
      if (x == null) return 1;           // 「—」永遠排最後（不受升降冪影響）
      if (y == null) return -1;
      var r = typeof x === "string" ? x.localeCompare(y) : x - y;
      return st.dir === "asc" ? r : -r;
    });
  }

  function onSort(tkey, key, defaultDir) {
    var cur = state.sorts[tkey];
    state.sorts[tkey] = cur && cur.key === key
      ? { key: key, dir: cur.dir === "asc" ? "desc" : "asc" }
      : { key: key, dir: defaultDir };
    renderTeam(state.current, { tkey: tkey, key: key });
  }

  /* ---------- 表格 ---------- */
  function buildTable(tkey, rows, cols, defaultSort) {
    var st = state.sorts[tkey] || (state.sorts[tkey] = defaultSort);
    var scroll = el("div", { "class": "tbl-scroll", tabindex: "0", role: "region", "aria-label": "可左右捲動的球員數據表" });
    var table = el("table", { "class": "roster", "data-tkey": tkey });
    var tr = el("tr");

    function th(key, label, cls, tip, defDir) {
      var h = el("th", { scope: "col", "class": cls });
      var active = st.key === key;
      h.setAttribute("aria-sort", active ? (st.dir === "asc" ? "ascending" : "descending") : "none");
      var b = el("button", { type: "button", "class": "sort-btn", "data-key": key, title: tip });
      b.appendChild(document.createTextNode(label));
      b.appendChild(el("span", { "class": "arrow", "aria-hidden": "true" }, active ? (st.dir === "asc" ? "▲" : "▼") : ""));
      b.addEventListener("click", function () { onSort(tkey, key, defDir); });
      h.appendChild(b);
      tr.appendChild(h);
    }
    th("_num", "#", "c-num", "背號", "asc");
    th("_name", "球員", "c-name", "依姓名排序", "asc");
    // 越低越好的欄位（ERA/WHIP/FIP）第一次點擊先升冪，其餘先降冪
    cols.forEach(function (c) { th(c.k, c.label, "", c.tip, /^(era|whip|fip)$/.test(c.k) ? "asc" : "desc"); });
    var thead = el("thead");
    thead.appendChild(tr);
    table.appendChild(thead);

    var tbody = el("tbody");
    sortRows(rows, cols, st).forEach(function (r) {
      var row = el("tr");
      row.appendChild(el("td", { "class": "c-num" }, r.number || DASH));
      var nameTd = el("td", { "class": "c-name" });
      nameTd.appendChild(el("a", { "class": "plink", href: SAVANT + r.id, target: "_blank", rel: "noopener" }, r.name));
      if (r.twp) nameTd.appendChild(el("small", { "class": "tag tag-twp" }, "二刀流"));
      row.appendChild(nameTd);
      cols.forEach(function (c) {
        var text = r.stats ? c.fmt(r.stats[c.k], r.stats) : DASH;
        row.appendChild(el("td", { "class": "n" + (text === DASH ? " is-empty" : "") }, text));
      });
      tbody.appendChild(row);
    });
    table.appendChild(tbody);
    scroll.appendChild(table);
    return scroll;
  }

  function section(label, count) {
    var s = el("section", { "class": "roster-section", "aria-label": label });
    var head = el("div", { "class": "section-head" });
    head.appendChild(el("h2", null, label));
    head.appendChild(el("span", { "class": "count" }, count + " 人"));
    s.appendChild(head);
    return s;
  }

  function row(p, stats, twp) { return { id: p.id, name: p.name, number: p.number, stats: stats, twp: twp }; }

  /* ---------- 球隊 ---------- */
  function renderTeam(abbr, refocus) {
    var box = $("content");
    box.textContent = "";
    markPicker(abbr);
    if (!abbr) {
      // 無 hash：顯示「請選擇球隊」，不預設任何一隊
      box.appendChild(stateBox("請選擇球隊", "點選上方隊徽，查看該隊名單與本季數據。"));
      return;
    }
    var info = state.data.index.teams.filter(function (t) { return t.abbr === abbr; })[0];
    if (!info) {
      box.appendChild(stateBox("找不到球隊「" + abbr + "」", "請從上方選單挑選球隊。"));
      return;
    }
    var team = state.data.teams[abbr];
    if (!team) {
      box.appendChild(titleBar(info, null));
      var nb = stateBox("此隊示範資料未提供", "設計稿只完整示範紐約洋基（NYY）與洛杉磯道奇（LAD）。");
      nb.className += " team-empty";
      box.appendChild(nb);
      return;
    }
    var players = team.players;
    box.appendChild(titleBar(info, players.length));

    GROUPS.forEach(function (g) {
      var list = players.filter(function (p) { return p.group === g[0]; });
      if (!list.length) return;
      var s = section(g[1], list.length);
      if (g[0] === "SP" || g[0] === "RP") {
        s.appendChild(buildTable(abbr + ":" + g[0],
          list.map(function (p) { return row(p, p.pitching); }),
          g[0] === "SP" ? SP_COLS : RP_COLS, { key: "ip", dir: "desc" }));
      } else if (g[0] === "UTIL") {
        // 二刀流：同區塊內「打擊」「投球」各一張表，球員名旁標「二刀流」
        var two = list.filter(function (p) { return p.hitting && p.pitching; });
        if (two.length) s.appendChild(el("p", { "class": "sub-head" }, "打擊"));
        s.appendChild(buildTable(abbr + ":UTIL:bat",
          list.map(function (p) { return row(p, p.hitting, !!(p.hitting && p.pitching)); }),
          HIT_COLS, { key: "pa", dir: "desc" }));
        if (two.length) {
          s.appendChild(el("p", { "class": "sub-head" }, "投球（二刀流）"));
          s.appendChild(buildTable(abbr + ":UTIL:pit",
            two.map(function (p) { return row(p, p.pitching, true); }),
            SP_COLS, { key: "ip", dir: "desc" }));
        }
      } else {
        s.appendChild(buildTable(abbr + ":" + g[0],
          list.map(function (p) { return row(p, p.hitting); }),
          HIT_COLS, { key: "pa", dir: "desc" }));
      }
      box.appendChild(s);
    });

    if (refocus) {      // 排序後重繪，把焦點還給剛按的表頭按鈕
      var tables = box.querySelectorAll("table");
      for (var i = 0; i < tables.length; i++) {
        if (tables[i].getAttribute("data-tkey") === refocus.tkey) {
          var btn = tables[i].querySelector('.sort-btn[data-key="' + refocus.key + '"]');
          if (btn) btn.focus({ preventScroll: true });
        }
      }
    }
  }

  function titleBar(info, count) {
    var bar = el("div", { "class": "team-title" + (isDarkColor(info.color) ? " team-stripe--dark" : "") });
    bar.style.setProperty("--team", info.color);
    bar.appendChild(logo(info, 44));
    bar.appendChild(el("h2", null, info.name));
    if (count != null) bar.appendChild(el("span", { "class": "count" }, "名單 " + count + " 人"));
    return bar;
  }

  function stateBox(title, text) {
    var b = el("div", { "class": "state-box", role: "status" });
    b.appendChild(el("h2", null, title));
    b.appendChild(el("p", null, text));
    return b;
  }

  /* ---------- 刊頭副標 ---------- */
  function dateline(idx) {
    var label = ROSTER_LABEL[idx.rosterType] || "名單";
    var m = /^\d{4}-(\d\d)-(\d\d)T(\d\d:\d\d)/.exec(idx.updatedAt || "");
    $("dateline").textContent = label + " · " + idx.season + " 球季數據" + (m ? " · 更新於 " + m[1] + "/" + m[2] + " " + m[3] : "");
  }

  /* ---------- hash 導覽 ---------- */
  function route() {
    var abbr = decodeURIComponent(location.hash.replace(/^#/, "")).toUpperCase();
    state.current = abbr || null;
    renderTeam(state.current);
  }

  function boot() {
    fetch(DATA_URL).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (data) {
      state.data = data;
      dateline(data.index);
      buildPicker(data.index.teams);
      buildSelect(data.index.teams);
      $("app").setAttribute("aria-busy", "false");
      route();
      window.addEventListener("hashchange", route);
    }).catch(function (err) {
      $("dateline").textContent = "名單載入失敗";
      var b = stateBox("無法讀取名單", "請稍後重新整理頁面。");
      b.appendChild(el("p", { "class": "detail" }, String(err.message || err)));
      $("content").appendChild(b);
      $("app").setAttribute("aria-busy", "false");
    });
  }

  boot();
})();
