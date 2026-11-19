/* 歷史存檔頁：讀取 reports/metadata.json，依月份分組。
   所有外部資料一律以 textContent / setAttribute 寫入，不使用 innerHTML 拼接。 */
(function () {
  'use strict';

  var DATA_URL = 'reports/metadata.json';
  var WEEKLY_URL = 'reports/weekly/index.json';
  var WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六'];

  var app = document.getElementById('app');
  var content = document.getElementById('content');
  var nav = document.getElementById('monthNav');
  var summaryLine = document.getElementById('summaryLine');

  function el(tag, opts, children) {
    var node = document.createElement(tag);
    opts = opts || {};
    if (opts.cls) node.className = opts.cls;
    if (opts.text != null) node.textContent = opts.text;
    if (opts.attrs) {
      Object.keys(opts.attrs).forEach(function (k) { node.setAttribute(k, opts.attrs[k]); });
    }
    (children || []).forEach(function (c) { node.appendChild(c); });
    return node;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function weekday(dateStr) {
    var p = dateStr.split('-').map(Number);
    return WEEKDAYS[new Date(Date.UTC(p[0], p[1] - 1, p[2])).getUTCDay()];
  }

  function isDate(s) { return /^\d{4}-\d{2}-\d{2}$/.test(s); }

  // ---------- 狀態畫面 ----------
  function showLoading() {
    app.setAttribute('aria-busy', 'true');
    nav.hidden = true;
    clear(content);
    var grid = el('div', { cls: 'days', attrs: { 'aria-hidden': 'true' } });
    for (var i = 0; i < 12; i++) grid.appendChild(el('div', { cls: 'skeleton' }));
    content.appendChild(el('div', { cls: 'state-box', attrs: { role: 'status' } }, [
      el('h2', { text: '讀取中…' }),
      el('p', { text: '正在載入存檔索引' })
    ]));
    content.appendChild(grid);
  }

  function showError(err) {
    app.setAttribute('aria-busy', 'false');
    nav.hidden = true;
    clear(content);
    var retry = el('button', { cls: 'btn', text: '重試', attrs: { type: 'button' } });
    retry.addEventListener('click', load);
    content.appendChild(el('div', { cls: 'state-box is-error', attrs: { role: 'alert' } }, [
      el('h2', { text: '無法讀取存檔索引' }),
      el('p', { text: '請檢查網路連線後再試一次。' }),
      el('p', { cls: 'detail', text: String(err && err.message ? err.message : err) }),
      retry
    ]));
  }

  function showEmpty() {
    app.setAttribute('aria-busy', 'false');
    nav.hidden = true;
    clear(content);
    content.appendChild(el('div', { cls: 'state-box' }, [
      el('h2', { text: '目前沒有存檔' }),
      el('p', { text: '尚未產生任何戰報，請稍後再來。' })
    ]));
  }

  // ---------- 正常畫面 ----------
  function render(data) {
    var dates = Object.keys(data).filter(isDate).sort().reverse(); // 新到舊
    if (!dates.length) { showEmpty(); return; }

    // 依月份分組
    var months = [];
    var byMonth = {};
    dates.forEach(function (d) {
      var m = d.slice(0, 7);
      if (!byMonth[m]) { byMonth[m] = []; months.push(m); }
      byMonth[m].push(d);
    });

    summaryLine.textContent = '共 ' + dates.length + ' 期 · ' + dates[dates.length - 1] + ' 至 ' + dates[0];

    clear(nav);
    clear(content);

    months.forEach(function (m) {
      var parts = m.split('-');
      var label = parts[0] + ' 年 ' + parts[1] + ' 月';
      var id = 'm-' + m;

      var chip = el('a', { cls: 'chip', attrs: { href: '#' + id } }, [
        document.createTextNode(Number(parts[1]) + ' 月'),
        el('span', { cls: 'num', text: byMonth[m].length })
      ]);
      nav.appendChild(chip);

      var list = el('ul', { cls: 'days' });
      byMonth[m].forEach(function (d) {
        var info = data[d] || {};
        var games = Number(info.total_games) || 0;
        var pp = Number(info.postponed) || 0;
        var day = d.slice(8);
        var yy = d.slice(0, 4), mm = d.slice(5, 7);
        var href = 'reports/' + yy + '/' + mm + '/' + d + '.html';

        var top = el('span', { cls: 'top' }, [
          el('span', { cls: 'd', text: mm + '-' + day }),
          el('span', { cls: 'wk', text: '週' + weekday(d) })
        ]);
        var isOff = info.type ? info.type === 'offseason' : games === 0;
        var count = el('span', { cls: 'c', text: isOff ? '休賽' : games + ' 場' });
        var kids = [top, count];
        if (pp) kids.push(el('span', { cls: 'pp', text: '延賽 ' + pp }));

        var a = el('a', {
          cls: 'day' + (isOff ? ' is-off' : ''),
          attrs: {
            href: href,
            title: info.summary ? String(info.summary) : d
          }
        }, kids);
        list.appendChild(el('li', {}, [a]));
      });

      content.appendChild(el('section', { cls: 'month', attrs: { id: id, 'aria-labelledby': id + '-h' } }, [
        el('div', { cls: 'section-head' }, [
          el('h2', { text: label, attrs: { id: id + '-h' } }),
          el('span', { cls: 'count', text: byMonth[m].length + ' 期' })
        ]),
        list
      ]));
    });

    nav.hidden = false;
    app.setAttribute('aria-busy', 'false');
  }

  // ---------- 每週總整理（讀不到、格式不符或沒有週報就不顯示） ----------
  function isWeek(s) { return /^\d{4}-W\d{2}$/.test(s); }

  function mdText(s) {
    return isDate(s) ? Number(s.slice(5, 7)) + '/' + Number(s.slice(8, 10)) : '';
  }

  function renderWeekly(data) {
    var weeks = data && Array.isArray(data.weeks) ? data.weeks.filter(function (w) {
      return w && typeof w === 'object' && typeof w.week === 'string' && isWeek(w.week);
    }) : [];
    if (!weeks.length) return;
    weeks.sort(function (a, b) { return a.week < b.week ? 1 : (a.week > b.week ? -1 : 0); }); // 新到舊

    var list = el('ul', { cls: 'weeks' });
    weeks.forEach(function (w) {
      var range = mdText(w.start) && mdText(w.end) ? mdText(w.start) + '～' + mdText(w.end) : '';
      var kids = [el('span', { cls: 'top' }, [
        el('span', { cls: 'd', text: w.week }),
        el('span', { cls: 'wk', text: range })
      ])];
      var txCount = Number(w.tx_count);
      if (isFinite(txCount)) kids.push(el('span', { cls: 'c', text: '異動 ' + txCount + ' 筆' }));
      var days = Number(w.days);
      if (isFinite(days) && days > 0 && days < 7) kids.push(el('span', { cls: 'pp', text: '涵蓋 ' + days + '／7 天' }));
      list.appendChild(el('li', {}, [el('a', {
        cls: 'day',
        attrs: { href: 'reports/weekly/' + w.week + '.html', title: w.week }
      }, kids)]));
    });

    content.insertBefore(el('section', { cls: 'month', attrs: { id: 'weekly', 'aria-labelledby': 'weekly-h' } }, [
      el('div', { cls: 'section-head' }, [
        el('h2', { text: '每週總整理', attrs: { id: 'weekly-h' } }),
        el('span', { cls: 'count', text: weeks.length + ' 期' })
      ]),
      list
    ]), content.firstChild);
  }

  function loadWeekly() {
    fetch(WEEKLY_URL, { cache: 'no-store' })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) { if (data) renderWeekly(data); })
      .catch(function () { /* 讀不到就不顯示 */ });
  }

  // ---------- 載入 ----------
  function load() {
    showLoading();

    fetch(DATA_URL, { cache: 'no-store' })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status + '（' + DATA_URL + '）');
        return res.json();
      })
      .then(function (data) {
        if (!data || typeof data !== 'object' || Array.isArray(data)) {
          throw new Error('索引格式不正確');
        }
        render(data);
        loadWeekly();
      })
      .catch(showError);
  }

  load();
})();
