/* 球隊名單頁：讀取 data/teams/index.json 與 data/teams/{id}.json，渲染、排序、hash 導覽。
   所有資料文字一律用 textContent / setAttribute 寫入 DOM，不以字串拼接 HTML。 */
(function () {
  "use strict";

  var DATA_DIR = "data/teams/";
  var LOGO_DARK = "https://www.mlbstatic.com/team-logos/team-cap-on-dark/";
  var LOGO_LIGHT = "https://www.mlbstatic.com/team-logos/team-cap-on-light/";
  var SAVANT = "https://baseballsavant.mlb.com/savant-player/";
  var DASH = "—";

  // 舊縮寫（MLB 官方 API 改用 AZ / KC / ATH）：舊連結仍可開啟
  var LEGACY_ABBR = { ARI: "AZ", KCR: "KC", OAK: "ATH" };

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
  // index：index.json；cache[隊伍 id]：已載入的隊伍檔；sorts[表格鍵] = {key, dir}
  var state = { index: null, cache: {}, current: null, sorts: {}, listening: false };
  function $(id) { return document.getElementById(id); }

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    return e;
  }

  function teamId(team) { return String(parseInt(team.id, 10)); }

  function logo(team, size) {
    var id = teamId(team);
    var pic = el("picture");
    pic.appendChild(el("source", { media: "(prefers-color-scheme: light)", srcset: LOGO_LIGHT + id + ".svg" }));
    pic.appendChild(el("img", { src: LOGO_DARK + id + ".svg", width: size, height: size, alt: team.abbr }));
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

  function fetchJson(path) {
    return fetch(path, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status + "（" + path + "）");
      return r.json();
    });
  }

  /* ---------- 隊徽選單 ---------- */
  function buildPicker(teams) {
    var picker = $("picker");
    picker.textContent = "";
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
    sel.textContent = "";
    sel.appendChild(el("option", { value: "" }, "請選擇球隊…"));
    DIVISIONS.forEach(function (d) {
      var og = el("optgroup", { label: d[2] });
      teams.filter(function (t) { return t.league === d[0] && t.division === d[1]; }).forEach(function (t) {
        og.appendChild(el("option", { value: t.abbr }, t.abbr + " " + t.name));
      });
      sel.appendChild(og);
    });
    sel.onchange = function () { if (sel.value) location.hash = sel.value; };
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
      return r.stats && c ? c.sort(r.stats) : null;
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
    var bth = el("th", { scope: "col", "class": "c-bt", title: "打擊／投球慣用手（左 L、右 R、左右開弓 S）" }, "B/T");
    tr.appendChild(bth);
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
      if (typeof r.id === "number" && isFinite(r.id) && Math.floor(r.id) === r.id) {   // Savant 連結只由整數 id 組成
        nameTd.appendChild(el("a", { "class": "plink", href: SAVANT + r.id, target: "_blank", rel: "noopener" }, r.name));
      } else {
        nameTd.appendChild(document.createTextNode(r.name));
      }
      if (r.twp) nameTd.appendChild(el("small", { "class": "tag tag-twp" }, "二刀流"));
      row.appendChild(nameTd);
      row.appendChild(el("td", { "class": "c-bt" }, handText(r.bats, r.throws)));
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

  function handText(bats, throws) {
    if (typeof bats !== "string" || !bats) bats = DASH;
    if (typeof throws !== "string" || !throws) throws = DASH;
    return bats === DASH && throws === DASH ? DASH : bats + "/" + throws;
  }

  function row(p, stats, twp) {
    return { id: p.id, name: String(p.name == null ? "" : p.name), number: p.number, pos: p.pos, bats: p.bats, throws: p.throws, stats: stats || null, twp: twp };
  }

  /* ---------- 球隊 ---------- */
  function findTeam(abbr) {
    return state.index.teams.filter(function (t) { return t.abbr === abbr; })[0];
  }

  function renderTeam(abbr, refocus) {
    var box = $("content");
    box.textContent = "";
    markPicker(abbr);
    if (!abbr) {
      // 無 hash：顯示「請選擇球隊」，不預設任何一隊
      box.appendChild(stateBox("請選擇球隊", "點選上方隊徽，查看該隊名單與本季數據。"));
      return;
    }
    var info = findTeam(abbr);
    if (!info) {
      box.appendChild(stateBox("找不到球隊「" + abbr + "」", "請從上方選單挑選球隊。"));
      return;
    }
    var data = state.cache[teamId(info)];
    if (!data) { loadTeam(info); return; }
    renderRoster(box, info, data, refocus);
  }

  function loadTeam(info) {
    var box = $("content");
    box.appendChild(titleBar(info, null));
    var wait = stateBox("讀取中…", "正在載入 " + info.name + " 的名單");
    wait.className += " team-empty";
    box.appendChild(wait);
    var id = teamId(info);
    fetchJson(DATA_DIR + id + ".json").then(function (data) {
      if (!data || !Array.isArray(data.players)) throw new Error("資料格式不符（" + DATA_DIR + id + ".json）");
      state.cache[id] = data;
      if (state.current === info.abbr) renderTeam(info.abbr);
    }).catch(function (err) {
      if (state.current !== info.abbr) return;      // 使用者已切換到別隊，忽略這次結果
      box.textContent = "";
      box.appendChild(titleBar(info, null));
      var retry = el("button", { type: "button", "class": "btn" }, "重試");
      retry.addEventListener("click", function () { renderTeam(info.abbr); });
      var eb = el("div", { "class": "state-box is-error team-empty", role: "alert" });
      eb.appendChild(el("h2", null, "無法讀取 " + info.name + " 的名單"));
      eb.appendChild(el("p", null, info.ok === false ? "這支球隊最近一次更新失敗，且沒有可用的舊資料。" : "請檢查網路連線後再試一次。"));
      eb.appendChild(el("p", { "class": "detail" }, String(err && err.message ? err.message : err)));
      eb.appendChild(retry);
      box.appendChild(eb);
    });
  }

  function renderRoster(box, info, data, refocus) {
    var abbr = info.abbr;
    var players = data.players;
    box.appendChild(titleBar(info, players.length));

    if (info.ok === false) {     // 最近一次更新失敗：仍顯示舊檔，並標明資料時間
      var when = data.meta && data.meta.updatedAt ? formatTime(data.meta.updatedAt) : "";
      var note = el("p", { "class": "section-empty" }, "這支球隊最近一次更新失敗，以下為較早的資料" + (when ? "（更新於 " + when + "）" : "") + "。");
      box.appendChild(note);
    }

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

    if (!players.length) {
      var eb = stateBox("名單是空的", "這支球隊目前沒有名單資料。");
      eb.className += " team-empty";
      box.appendChild(eb);
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

  function titleBar(info, count) {
    var bar = el("div", { "class": "team-title" + (isDarkColor(info.color) ? " team-stripe--dark" : "") });
    if (/^#[0-9a-f]{6}$/i.test(info.color || "")) bar.style.setProperty("--team", info.color);
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
  function formatTime(iso) {
    var m = /^\d{4}-(\d\d)-(\d\d)T(\d\d:\d\d)/.exec(iso || "");
    return m ? parseInt(m[1], 10) + "/" + parseInt(m[2], 10) + " " + m[3] : "";
  }

  function dateline(idx) {
    var label = ROSTER_LABEL[idx.rosterType] || "名單";
    var t = formatTime(idx.updatedAt);
    $("dateline").textContent = label + " · " + idx.season + " 球季數據" + (t ? " · 更新於 " + t : "");
  }

  /* ---------- hash 導覽 ---------- */
  function route() {
    var raw = "";
    try { raw = decodeURIComponent(location.hash.replace(/^#/, "")); } catch (e) { raw = ""; }
    var abbr = raw.toUpperCase();
    if (LEGACY_ABBR.hasOwnProperty(abbr)) abbr = LEGACY_ABBR[abbr];
    state.current = abbr || null;
    renderTeam(state.current);
  }

  function showIndexError(err) {
    $("dateline").textContent = "名單載入失敗";
    $("picker").textContent = "";
    $("teamSelect").textContent = "";
    var box = $("content");
    box.textContent = "";
    var retry = el("button", { type: "button", "class": "btn" }, "重試");
    retry.addEventListener("click", boot);
    var eb = el("div", { "class": "state-box is-error", role: "alert" });
    eb.appendChild(el("h2", null, "無法讀取球隊名單"));
    eb.appendChild(el("p", null, "請檢查網路連線後再試一次。"));
    eb.appendChild(el("p", { "class": "detail" }, String(err && err.message ? err.message : err)));
    eb.appendChild(retry);
    box.appendChild(eb);
    $("app").setAttribute("aria-busy", "false");
  }

  function boot() {
    $("app").setAttribute("aria-busy", "true");
    $("dateline").textContent = "讀取中…";
    fetchJson(DATA_DIR + "index.json").then(function (idx) {
      if (!idx || !Array.isArray(idx.teams) || !idx.teams.length) throw new Error("資料格式不符（" + DATA_DIR + "index.json）");
      state.index = idx;
      dateline(idx);
      buildPicker(idx.teams);
      buildSelect(idx.teams);
      $("app").setAttribute("aria-busy", "false");
      route();
      if (!state.listening) {
        state.listening = true;
        window.addEventListener("hashchange", function () { if (state.index) route(); });
      }
    }).catch(showIndexError);
  }

  boot();
})();
