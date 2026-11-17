/* 首頁戰績側邊欄
 * 只負責：桌機收合／展開（localStorage 記憶）、手機抽屜、焦點管理；不插入任何資料。
 */
(function () {
  'use strict';

  var KEY = 'mlbdaily.standings.collapsed';
  var root = document.documentElement;
  var aside = document.getElementById('standings');
  if (!aside) return;

  var fab = document.querySelector('[data-st="open"]');
  var collapseBtn = aside.querySelector('[data-st="collapse"]');
  var railBtn = aside.querySelector('[data-st="expand"]');
  var closeBtn = aside.querySelector('.st-close');
  var backdrop = document.querySelector('.st-backdrop');
  var desktop = window.matchMedia('(min-width: 1024px)');
  // 抽屜開啟時要整個停用（inert）的背景區塊
  var background = [document.querySelector('.masthead'), document.querySelector('.intro-wrap'),
    document.querySelector('.layout > main'), document.querySelector('.site-footer'), fab];

  /* ---- 桌機：收合／展開 ---- */
  function readCollapsed() {
    try { return window.localStorage.getItem(KEY) === '1'; } catch (e) { return false; }
  }
  function writeCollapsed(value) {
    try { window.localStorage.setItem(KEY, value ? '1' : '0'); } catch (e) { /* 失敗時只是不記憶 */ }
  }
  function setCollapsed(value, persist) {
    root.classList.toggle('standings-collapsed', value);
    if (collapseBtn) collapseBtn.setAttribute('aria-expanded', value ? 'false' : 'true');
    if (railBtn) railBtn.setAttribute('aria-expanded', value ? 'false' : 'true');
    if (persist) writeCollapsed(value);
  }

  setCollapsed(readCollapsed(), false);

  if (collapseBtn) collapseBtn.addEventListener('click', function () {
    setCollapsed(true, true);
    if (railBtn) railBtn.focus();
  });
  if (railBtn) railBtn.addEventListener('click', function () {
    setCollapsed(false, true);
    if (collapseBtn) collapseBtn.focus();
  });

  /* ---- 平板／手機：抽屜 ---- */
  var drawerOpen = false;

  function focusables() {
    return Array.prototype.filter.call(
      aside.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'),
      function (el) { return el.offsetParent !== null; }
    );
  }

  function setBackgroundInert(value) {
    background.forEach(function (el) {
      if (!el) return;
      if (value) el.setAttribute('inert', ''); else el.removeAttribute('inert');
    });
  }

  function openDrawer() {
    if (drawerOpen || desktop.matches) return;
    drawerOpen = true;
    root.classList.add('st-drawer-open');
    aside.setAttribute('role', 'dialog');
    aside.setAttribute('aria-modal', 'true');
    if (fab) fab.setAttribute('aria-expanded', 'true');
    setBackgroundInert(true);
    if (closeBtn) closeBtn.focus();
  }

  function closeDrawer(restoreFocus) {
    if (!drawerOpen) return;
    drawerOpen = false;
    root.classList.remove('st-drawer-open');
    aside.removeAttribute('role');
    aside.removeAttribute('aria-modal');
    if (fab) fab.setAttribute('aria-expanded', 'false');
    setBackgroundInert(false);
    if (restoreFocus !== false && fab) fab.focus();
  }

  if (fab) fab.addEventListener('click', function () {
    if (drawerOpen) closeDrawer(true); else openDrawer();
  });
  if (closeBtn) closeBtn.addEventListener('click', function () { closeDrawer(true); });
  if (backdrop) backdrop.addEventListener('click', function () { closeDrawer(true); });

  document.addEventListener('keydown', function (e) {
    if (!drawerOpen) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      closeDrawer(true);
      return;
    }
    if (e.key === 'Tab') {
      // 焦點只在抽屜內循環
      var items = focusables();
      if (!items.length) return;
      var first = items[0];
      var last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });

  // 視窗放大到桌機寬度時，收起抽屜狀態（不搶焦點）
  function onBreakpoint() { if (desktop.matches) closeDrawer(false); }
  if (desktop.addEventListener) desktop.addEventListener('change', onBreakpoint);
  else if (desktop.addListener) desktop.addListener(onBreakpoint);
})();
