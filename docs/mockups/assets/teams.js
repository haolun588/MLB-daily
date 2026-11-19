/* 球隊專頁（設計稿）：讀取 teams.sample.json，渲染、排序、hash 導覽。
   所有資料文字一律用 textContent / setAttribute，不拼 innerHTML。 */
(function () {
  "use strict";

  var DATA_URL = "assets/teams.sample.json";
  var LOGO_DARK = "https://www.mlbstatic.com/team-logos/team-cap-on-dark/";
  var LOGO_LIGHT = "https://www.mlbstatic.com/team-logos/team-cap-on-light/";
  var SAVANT = "https://baseballsavant.mlb.com/savant-player/";
  var DASH = "—";

  // 守位排序順序：C→1B→2B→3B→SS→LF→CF→RF→DH→其他（IF/OF/UT/TWP 等）
  var POS_ORDER = ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH"];
  function posRank(p) { var i = POS_ORDER.indexOf(p); return i < 0 ? POS_ORDER.length : i; }
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
      if (st.key === "_pos") return posRank(r.pos);
      if (st.key === "_num") { var n = parseInt(r.number, 10); return isNaN(n) ? null : n; }
      return r.stats ? c.sort(r.stats) : null;
    }
    return rows.slice().sort(function (a, b) {
      var x = val(a), y = val(b);
      if (x == null && y == null) return 0;
      if (x == null) return 1;           // 「—」永遠排最後（不受升降冪影響）
      if (y == null) return -1;
      var r = typeof x === "string" ? x.localeCompare(y) : x - y;
      if (r === 0 && st.key === "_pos") {   // 同守位內依 PA 由高到低（無打席資料者排後）
        var pa = a.stats ? a.stats.pa : null, pb = b.stats ? b.stats.pa : null;
        if (pa == null && pb == null) return 0;
        if (pa == null) return 1;
        if (pb == null) return -1;
        return pb - pa;
      }
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
  function buildTable(tkey, rows, cols, defaultSort, hasPos) {
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
    if (hasPos) th("_pos", "守位", "c-pos", "守位（C、1B、2B、3B、SS、LF、CF、RF、DH、其他）", "asc");
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
      if (hasPos) {
        var posTd = el("td", { "class": "c-pos" });
        posTd.appendChild(el("span", { "class": "pos" }, r.pos || DASH));
        row.appendChild(posTd);
      }
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

  function row(p, stats, twp) { return { id: p.id, name: p.name, number: p.number, pos: p.pos, stats: stats, twp: twp }; }

  /* ---------- 球隊 ---------- */
  function renderTeam(abbr, refocus) {
    var box = $("content");
    box.textContent = "";
    markPicker(abbr);
    if (!abbr) {
      // 無 hash：顯示「請選擇球隊」，不預設任何一隊
      box.appendChild(stateBox("請選擇球隊", "點選上方隊徽，查看該隊專頁：摘要、近期動態、隊內數據王與名單。"));
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
      var nb = stateBox("此隊示範資料未提供", "設計稿只完整示範洛杉磯道奇（LAD）、紐約洋基（NYY，近期動態為空狀態）與波士頓紅襪（BOS）。");
      nb.className += " team-empty";
      box.appendChild(nb);
      return;
    }
    var players = team.players;

    // 1 球隊摘要（頁首）
    box.appendChild(summaryCard(info, team));

    // 2 近期動態
    box.appendChild(recentBlock(team));

    // 3 隊內數據王
    box.appendChild(leadersBlock(players));

    // 4 名單（沿用原名單表格，三張表設計不變）
    var rwrap = el("section", { "class": "block", "aria-label": "名單" });
    rwrap.appendChild(blockHead("名單", players.length + " 人"));
    box.appendChild(rwrap);

    // 投手：SP／RP 各一張表；二刀流（pitchingGroup）依 SP/RP 判定放進對應表
    ["SP", "RP"].forEach(function (g) {
      var list = players.filter(function (p) { return p.group === g || p.pitchingGroup === g; });
      if (!list.length) return;
      var s = section(g === "SP" ? "SP 先發投手" : "RP 後援投手", list.length);
      s.appendChild(buildTable(abbr + ":" + g,
        list.map(function (p) { return row(p, p.pitching, !!(p.hitting && p.pitching)); }),
        g === "SP" ? SP_COLS : RP_COLS, { key: "ip", dir: "desc" }));
      box.appendChild(s);
    });

    // 野手：全部合併一張表（含二刀流的打擊數據，守位 TWP）
    var bats = players.filter(function (p) { return p.group === "POS"; });
    if (bats.length) {
      var bs = section("野手", bats.length);
      bs.appendChild(buildTable(abbr + ":POS",
        bats.map(function (p) { return row(p, p.hitting, !!(p.hitting && p.pitching)); }),
        HIT_COLS, { key: "_pos", dir: "asc" }, true));
      box.appendChild(bs);
    }

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

  /* ---------- 1 球隊摘要 ---------- */
  function signed(n) { return n == null ? DASH : (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(n); }

  function summaryCard(info, team) {
    var sm = team.summary;
    var card = el("section", { "class": "team-summary" + (isDarkColor(info.color) ? " team-stripe--dark" : ""), "aria-label": "球隊摘要" });
    card.style.setProperty("--team", info.color);

    var top = el("div", { "class": "sum-top" });
    top.appendChild(logo(info, 56));
    var ttl = el("div", { "class": "sum-title" });
    ttl.appendChild(el("h2", null, info.name));
    var divName = DIVISIONS.filter(function (d) { return d[0] === info.league && d[1] === info.division; })[0];
    ttl.appendChild(el("p", { "class": "sum-div" }, divName ? divName[2] : ""));
    top.appendChild(ttl);
    top.appendChild(el("span", { "class": "sum-final" }, state.data.index.season + " 賽季最終"));
    card.appendChild(top);

    if (sm) {
      var r = sm.record;
      var grid = el("dl", { "class": "sum-stats" });
      [["戰績 W-L", r.w + "-" + r.l, ""], ["勝率", rate3(r.pct), ""],
       ["分區排名", "第 " + r.divRank + " 名", ""],
       ["得失分差", signed(r.rd), r.rd > 0 ? "up" : (r.rd < 0 ? "down" : "")]].forEach(function (x) {
        var c = el("div", { "class": "sum-stat" });
        c.appendChild(el("dt", null, x[0]));
        c.appendChild(el("dd", x[2] ? { "class": "is-" + x[2] } : null, x[1]));
        grid.appendChild(c);
      });
      card.appendChild(grid);

      var ps = el("p", { "class": "sum-post is-" + sm.postseason.outcome });
      ps.appendChild(el("span", { "class": "sum-post-label" }, "季後賽"));
      ps.appendChild(document.createTextNode(sm.postseason.text));
      card.appendChild(ps);

      if (sm.tracked && sm.tracked.length) {
        var tr = el("div", { "class": "sum-tracked" });
        tr.appendChild(el("span", { "class": "sum-tracked-label" }, "台日韓球員"));
        sm.tracked.forEach(function (n) { tr.appendChild(el("span", { "class": "tag tag-track" }, n)); });
        card.appendChild(tr);
      }
    }
    return card;
  }

  /* ---------- 2 近期動態 ---------- */
  function blockHead(label, note) {
    var h = el("div", { "class": "block-head" });
    h.appendChild(el("h2", null, label));
    if (note) h.appendChild(el("span", { "class": "count" }, note));
    return h;
  }

  function moveList(title, moves, emptyText) {
    var col = el("div", { "class": "move-col" });
    col.appendChild(el("h3", null, title + "（" + moves.length + "）"));
    if (!moves.length) {
      col.appendChild(el("p", { "class": "empty-line" }, emptyText));
      return col;
    }
    var ul = el("ul", { "class": "moves" });
    moves.forEach(function (m) {
      var li = el("li");
      li.appendChild(el("time", null, m.date));
      var body = el("div", { "class": "move-body" });
      var line = el("p", { "class": "move-line" });
      line.appendChild(el("span", { "class": "move-name" }, m.player));
      line.appendChild(el("span", { "class": "tag tag-move" }, m.type));
      body.appendChild(line);
      body.appendChild(el("p", { "class": "move-note" }, m.note));
      li.appendChild(body);
      ul.appendChild(li);
    });
    col.appendChild(ul);
    return col;
  }

  function emptyBox(title, text) {
    var b = el("div", { "class": "empty-box", role: "status" });
    b.appendChild(el("strong", null, title));
    b.appendChild(el("span", null, text));
    return b;
  }

  function recentBlock(team) {
    var rc = team.recent || { moves: [], news: [] };
    var sec = el("section", { "class": "block", "aria-label": "近期動態" });
    sec.appendChild(blockHead("近期動態", "近 30 天"));

    sec.appendChild(el("h3", { "class": "sub-head" }, "人事異動"));
    var moves = rc.moves || [];
    if (!moves.length) {
      sec.appendChild(emptyBox("近 30 天沒有人事異動", "這段期間球隊沒有交易、簽約或名單異動的紀錄。"));
    } else {
      var cols = el("div", { "class": "move-cols" });
      cols.appendChild(moveList("加入", moves.filter(function (m) { return m.dir === "in"; }), "近 30 天沒有球員加入。"));
      cols.appendChild(moveList("離開", moves.filter(function (m) { return m.dir === "out"; }), rc.moves_note || "近 30 天沒有球員離開。"));
      sec.appendChild(cols);
    }

    sec.appendChild(el("h3", { "class": "sub-head" }, "相關新聞"));
    var news = rc.news || [];
    if (!news.length) {
      sec.appendChild(emptyBox("近 30 天沒有相關新聞", "休賽季消息較少，有新報導時會顯示在這裡。"));
    } else {
      var ul = el("ul", { "class": "news team-news" });
      news.forEach(function (n) {
        var li = el("li");
        li.appendChild(el("time", null, n.date));
        var body = el("div");
        body.appendChild(el("a", { "class": "title", href: n.url, target: "_blank", rel: "noopener" }, n.title));
        body.appendChild(el("span", { "class": "src" }, n.source));
        li.appendChild(body);
        ul.appendChild(li);
      });
      sec.appendChild(ul);
    }
    return sec;
  }

  /* ---------- 3 隊內數據王 ---------- */
  // 設計稿暫定門檻：AVG／OPS 需 PA ≥ MIN_PA、ERA／WHIP 需 IP ≥ MIN_IP（正式門檻待使用者決定）
  var MIN_PA = 300, MIN_IP = 50;

  function leaderOf(list, valFn, dir, qualFn) {
    var best = null;
    list.forEach(function (p) {
      if (qualFn && !qualFn(p)) return;
      var v = valFn(p);
      if (v == null) return;
      if (best == null || (dir === "asc" ? v < best.v : v > best.v)) best = { p: p, v: v };
    });
    return best;
  }

  function leadersBlock(players) {
    var hit = players.filter(function (p) { return p.hitting; });
    var pit = players.filter(function (p) { return p.pitching; });
    var qPa = function (p) { return p.hitting.pa >= MIN_PA; };
    var qIp = function (p) { return ipOuts(p.pitching) >= MIN_IP * 3; };
    var H = function (k) { return function (p) { return p.hitting[k]; }; };
    var Pi = function (k) { return function (p) { return p.pitching[k]; }; };
    var groups = [
      ["打擊", [
        ["AVG 打擊率", leaderOf(hit, H("avg"), "desc", qPa), rate3],
        ["HR 全壘打", leaderOf(hit, H("hr"), "desc"), int],
        ["OPS", leaderOf(hit, function (p) { return p.hitting.obp + p.hitting.slg; }, "desc", qPa), rate3],
        ["SB 盜壘", leaderOf(hit, H("sb"), "desc"), int],
        ["WAR", leaderOf(hit, H("war"), "desc"), fixed(1)]]],
      ["投球", [
        ["ERA 防禦率", leaderOf(pit, Pi("era"), "asc", qIp), fixed(2)],
        ["SO 三振", leaderOf(pit, Pi("so"), "desc"), int],
        ["SV 救援成功", leaderOf(pit, Pi("sv"), "desc", function (p) { return p.pitching.sv > 0; }), int],
        ["WHIP", leaderOf(pit, Pi("whip"), "asc", qIp), fixed(2)],
        ["WAR", leaderOf(pit, Pi("war"), "desc"), fixed(1)]]]
    ];
    var sec = el("section", { "class": "block", "aria-label": "隊內數據王" });
    sec.appendChild(blockHead("隊內數據王", state.data.index.season + " 球季"));
    groups.forEach(function (g) {
      sec.appendChild(el("h3", { "class": "sub-head" }, g[0]));
      var grid = el("div", { "class": "leader-grid" });
      g[1].forEach(function (it) {
        var card = el("div", { "class": "leader" });
        card.appendChild(el("span", { "class": "leader-cat" }, it[0]));
        if (it[1]) {
          card.appendChild(el("span", { "class": "leader-val" }, it[2](it[1].v)));
          card.appendChild(el("a", { "class": "plink leader-name", href: SAVANT + it[1].p.id, target: "_blank", rel: "noopener" }, it[1].p.name));
        } else {
          card.appendChild(el("span", { "class": "leader-val is-empty" }, DASH));
          card.appendChild(el("span", { "class": "leader-name is-empty" }, "無人達標"));
        }
        grid.appendChild(card);
      });
      sec.appendChild(grid);
    });
    sec.appendChild(el("p", { "class": "block-note" }, "AVG、OPS 需 " + MIN_PA + " 打席以上；ERA、WHIP 需 " + MIN_IP + " 局以上；SV 需至少 1 次救援成功。"));
    return sec;
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
