(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const COLS = "ABCDEFGHJKLMNOPQRST";
  const STONE_BLACK = 1;
  const STONE_WHITE = 2;

  const state = {
    started: false,
    boot: null,
    levels: [],
    levelId: "pro2",
    settings: { size: 19, player_color: "B", handicap: 0, komi: 7.5, rules: "chinese", time_limit: 0, byo_yomi: 0 },
    game: null,
    view: "play",
    tab: "analysis",
    engine: null,
    busy: false,
    pollTimer: null,
    pollFails: 0,
    extraPolls: 0,
    reviewPayload: null,
    reviewFetching: false,
    reviewPoll: null,
    reviewIndex: null,
    hintVisible: false,
    ownershipVisible: false,
    showNumbers: false,
    flip: false,
    selectedCandidate: null,
    sound: true,
    audioCtx: null,
    soundBuffer: null,
    modalOpen: false,
    confirmOpen: false,
    confirmClose: null,
    detailOpen: false,
    detailClose: null,
    modalResolve: null,
    resultKey: null,
    initialLoaded: false,
    lastMoveCount: -1,
    lastGameId: null,
    toastTimer: null,
    commentCount: -1,
    chart: null,
    chartHover: null,
    statusTimer: null,
    engineFails: 0,
    engineOffline: false,
    engineRetryBusy: false,
    engineRestarting: false,
    helpOpen: false,
    boardAnim: null,
    hover: null,
    coords: null,
    clockBase: 0,
    clockAt: 0,
    lang: "zh",
    reduceMotion: false,
  };

  // ==================== 工具 ====================
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (v) => `${(clamp(Number(v) || 0, 0, 1) * 100).toFixed(1)}%`;
  const fmtScore = (v) => {
    const n = Number(v) || 0;
    return `${n > 0 ? "+" : ""}${n.toFixed(1)}`;
  };

  // ==================== 双语界面 ====================
  const I18N = window.YixinI18N || { text: {}, rules: [], attr: {}, langKey: "yixin_lang" };
  const zhText = new Map();
  const zhAttrs = new Map();
  const LOCALIZED_ATTRS = ["title", "aria-label", "placeholder"];

  function padTranslation(source, translated) {
    const key = source.trim();
    const idx = source.indexOf(key);
    if (idx < 0) return translated;
    return source.slice(0, idx) + translated + source.slice(idx + key.length);
  }

  function translateText(raw) {
    const source = String(raw ?? "");
    const key = source.trim();
    if (!key) return null;
    const direct = I18N.text && Object.prototype.hasOwnProperty.call(I18N.text, key) ? I18N.text[key] : undefined;
    if (direct !== undefined) return padTranslation(source, direct);
    const rules = I18N.rules || [];
    for (let i = 0; i < rules.length; i++) {
      const pattern = rules[i][0];
      if (!pattern.test(key)) continue;
      const replacement = rules[i][1];
      const translated = typeof replacement === "function"
        ? key.replace(pattern, (...args) => replacement(args))
        : key.replace(pattern, replacement);
      return padTranslation(source, translated);
    }
    return null;
  }

  function translateAttr(raw) {
    const source = String(raw ?? "");
    const key = source.trim();
    if (!key) return null;
    if (I18N.attr && Object.prototype.hasOwnProperty.call(I18N.attr, key)) return I18N.attr[key];
    return translateText(source);
  }

  // 把中文 DOM 就地翻成英文；关闭英文时再用缓存把原中文还原，避免重建对局
  function localizeTree(root, on) {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    let node;
    while ((node = walker.nextNode())) {
      const el = node.parentElement;
      if (!el) continue;
      const tag = el.tagName;
      if (tag === "SCRIPT" || tag === "STYLE" || tag === "TITLE" || tag === "TEXTAREA") continue;
      if (el.closest("[data-i18n-skip]")) continue;
      if (!zhText.has(node)) zhText.set(node, node.nodeValue);
      const source = zhText.get(node);
      if (!on) {
        if (node.nodeValue !== source) node.nodeValue = source;
        continue;
      }
      const out = translateText(source);
      if (out !== null && out !== node.nodeValue) node.nodeValue = out;
    }
    const elements = [];
    if (root.nodeType === 1) elements.push(root);
    if (root.querySelectorAll) elements.push(...root.querySelectorAll("[title], [aria-label], [placeholder]"));
    for (const el of elements) {
      if (!el || el.nodeType !== 1 || el.closest("[data-i18n-skip]")) continue;
      let store = zhAttrs.get(el);
      if (!store) { store = {}; zhAttrs.set(el, store); }
      for (const name of LOCALIZED_ATTRS) {
        if (!el.hasAttribute(name)) continue;
        if (!(name in store)) store[name] = el.getAttribute(name);
        const source = store[name];
        if (!on) {
          if (el.getAttribute(name) !== source) el.setAttribute(name, source);
          continue;
        }
        const out = translateAttr(source);
        if (out !== null && el.getAttribute(name) !== out) el.setAttribute(name, out);
      }
    }
    if (zhText.size > 3000) {
      for (const n of Array.from(zhText.keys())) if (!n.isConnected) zhText.delete(n);
      for (const el of Array.from(zhAttrs.keys())) if (!el.isConnected) zhAttrs.delete(el);
    }
  }

  function syncLang() {
    if (state.lang === "en") localizeTree(document.body, true);
  }

  function applyDocumentLang() {
    const en = state.lang === "en";
    document.documentElement.lang = en ? "en" : "zh-CN";
    document.title = en ? "Yixin · Go Sparring" : "弈心 · 围棋陪练";
  }

  function updateLangButton() {
    const btn = $("#lang-btn");
    if (!btn) return;
    const en = state.lang === "en";
    btn.textContent = en ? "中" : "EN";
    btn.title = en ? "Switch to Chinese" : "Switch to English";
    btn.setAttribute("aria-label", btn.title);
  }

  function setLang(next, { persist = true } = {}) {
    const lang = next === "en" ? "en" : "zh";
    if (lang === state.lang) { updateLangButton(); syncLang(); return; }
    if (lang === "zh") {
      // 先把已翻成英文的节点还原成中文，再让各面板按中文重新渲染
      localizeTree(document.body, false);
      zhText.clear();
      zhAttrs.clear();
    }
    state.lang = lang;
    if (persist) { try { localStorage.setItem(I18N.langKey || "yixin_lang", lang); } catch (e) { /* ignore */ } }
    applyDocumentLang();
    updateLangButton();
    if (lang === "en") localizeTree(document.body, true);
    if (state.game) renderAll();
    drawBoard();
    syncLang();
  }

  function startLocalizeObserver() {
    if (!window.MutationObserver) return;
    let queued = false;
    const observer = new MutationObserver(() => {
      if (state.lang !== "en" || queued) return;
      queued = true;
      requestAnimationFrame(() => {
        queued = false;
        syncLang();
      });
    });
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: LOCALIZED_ATTRS,
    });
  }

  // ==================== 动效偏好 ====================
  const motionQuery = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  if (motionQuery) {
    state.reduceMotion = motionQuery.matches;
    const onMotionChange = (event) => { state.reduceMotion = event.matches; };
    if (motionQuery.addEventListener) motionQuery.addEventListener("change", onMotionChange);
    else if (motionQuery.addListener) motionQuery.addListener(onMotionChange);
  }

  async function api(path, options = {}) {
    const opts = { method: options.method || "GET", headers: {} };
    if (options.body !== undefined) {
      opts.method = options.method || "POST";
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(options.body);
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.timeout || 180000);
    opts.signal = controller.signal;
    let res;
    try {
      res = await fetch(path, opts);
    } catch (err) {
      clearTimeout(timer);
      if (err && err.name === "AbortError") throw new Error("请求超时，请稍后再试");
      throw new Error("无法连接本地服务，请确认程序仍在运行");
    }
    clearTimeout(timer);
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : {}; } catch (e) { data = { error: text }; }
    if (!res.ok) throw new Error((data && data.error) || `请求失败（${res.status}）`);
    return data;
  }

  function toast(text, type = "info", duration = 2600) {
    const el = $("#toast");
    el.textContent = text;
    if (state.lang === "en") localizeTree(el, true);
    el.className = `toast show${type === "error" ? " error" : ""}`;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => { el.className = "toast"; }, duration);
  }

  function loadPrefs() {
    try {
      const saved = JSON.parse(localStorage.getItem("yixin_settings") || "{}");
      state.settings = { ...state.settings, ...(saved || {}) };
      if (![0, 300, 600, 1200, 1800, 3600].includes(Number(state.settings.time_limit))) state.settings.time_limit = 0;
      if (![0, 30, 60].includes(Number(state.settings.byo_yomi))) state.settings.byo_yomi = 0;
      if (!Number(state.settings.time_limit)) state.settings.byo_yomi = 0;
      state.levelId = localStorage.getItem("yixin_level") || state.levelId;
      state.sound = localStorage.getItem("yixin_sound") !== "0";
      state.showNumbers = localStorage.getItem("yixin_numbers") === "1";
      state.flip = localStorage.getItem("yixin_flip") === "1";
      state.lang = localStorage.getItem("yixin_lang") === "en" ? "en" : "zh";
    } catch (e) { /* ignore */ }
  }

  function savePrefs() {
    try {
      localStorage.setItem("yixin_settings", JSON.stringify(state.settings));
      localStorage.setItem("yixin_level", state.levelId);
      localStorage.setItem("yixin_sound", state.sound ? "1" : "0");
      localStorage.setItem("yixin_numbers", state.showNumbers ? "1" : "0");
      localStorage.setItem("yixin_flip", state.flip ? "1" : "0");
    } catch (e) { /* ignore */ }
  }

  // ==================== 音效 ====================
  function ensureAudio() {
    if (!state.sound) return;
    if (!state.audioCtx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      state.audioCtx = new AC();
    }
    if (state.audioCtx.state === "suspended") state.audioCtx.resume().catch(() => {});
  }

  function playStoneSound(black = true) {
    if (!state.sound) return;
    try {
      ensureAudio();
      const ac = state.audioCtx;
      if (!ac) return;
      const t = ac.currentTime;
      if (!state.soundBuffer) {
        const len = Math.floor(ac.sampleRate * 0.055);
        const buf = ac.createBuffer(1, len, ac.sampleRate);
        const data = buf.getChannelData(0);
        for (let i = 0; i < len; i++) {
          const env = Math.exp(-i / (len * 0.16));
          data[i] = (Math.random() * 2 - 1) * env;
        }
        state.soundBuffer = buf;
      }
      const src = ac.createBufferSource();
      src.buffer = state.soundBuffer;
      const bp = ac.createBiquadFilter();
      bp.type = "bandpass";
      bp.frequency.value = black ? 940 : 1230;
      bp.Q.value = 0.9;
      const gain = ac.createGain();
      gain.gain.setValueAtTime(0.11, t);
      gain.gain.exponentialRampToValueAtTime(0.001, t + 0.07);
      src.connect(bp).connect(gain).connect(ac.destination);
      src.start(t);
      const osc = ac.createOscillator();
      const og = ac.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(black ? 176 : 226, t);
      osc.frequency.exponentialRampToValueAtTime(88, t + 0.06);
      og.gain.setValueAtTime(0.045, t);
      og.gain.exponentialRampToValueAtTime(0.001, t + 0.075);
      osc.connect(og).connect(ac.destination);
      osc.start(t);
      osc.stop(t + 0.08);
    } catch (e) { /* ignore */ }
  }

  // ==================== 初始化 ====================
  async function init() {
    if (state.started) return;
    state.started = true;
    loadPrefs();
    const urlLang = new URLSearchParams(location.search).get("lang");
    if (urlLang === "en" || urlLang === "zh") state.lang = urlLang;
    applyDocumentLang();
    bindEvents();
    setupBoard();
    updateSoundButton();
    updateNumbersButton();
    updateFlipButton();
    updateLangButton();
    startLocalizeObserver();
    try {
      const boot = await api("/api/bootstrap");
      state.boot = boot;
      state.levels = boot.levels || [];
      state.levelId = state.levels.some((l) => l.id === state.levelId) ? state.levelId : (boot.default_level || "pro2");
      state.engine = boot.engine || null;
      renderLevels();
      renderSettings();
      renderEngine();
      pollStatus();

      let game = null;
      const lastId = localStorage.getItem("yixin_last_game");
      if (lastId) {
        try { game = await api(`/api/game/${lastId}`); } catch (e) { game = null; }
      }
      if (!game) {
        game = await createGame({ silent: true, noConfirm: true });
      } else {
        applyGame(game, { initial: true });
      }
      const activeGames = boot.active_games || [];
      if (activeGames.length && !activeGames.some((item) => item.id === game.id)) {
        setTimeout(() => toast(`有 ${activeGames.length} 局未完成对局，可在「棋谱」继续`, 5200), 1200);
      }
    } catch (err) {
      showFatal(err.message || String(err));
    }
  }

  function showFatal(message) {
    const root = $("#modal-root");
    root.innerHTML = `<div class="modal-overlay"><div class="modal"><div class="modal-kicker">连接失败</div>
      <h3>无法启动棋局</h3><p>${esc(message)}</p>
      <p>请确认「弈心」控制台仍在运行，或重新打开启动程序。</p>
      <div class="modal-actions"><button class="primary-btn small" id="fatal-reload">重新连接</button></div></div></div>`;
    $("#fatal-reload").onclick = () => location.reload();
  }

  function bindEvents() {
    document.addEventListener("pointerdown", ensureAudio, { once: true, capture: true });

    $("#new-game-btn").addEventListener("click", () => requestNewGame());
    $("#level-list").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-level]");
      if (!btn) return;
      selectLevel(btn.dataset.level);
    });

    $$(".segmented").forEach((seg) => {
      seg.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-value]");
        if (!btn) return;
        applySetting(seg.dataset.setting, btn.dataset.value);
      });
    });
    $("#handicap-input").addEventListener("change", () => {
      const h = Number($("#handicap-input").value) || 0;
      state.settings.handicap = h;
      const defaultKomi = h > 0 ? 0.5 : (state.settings.rules === "chinese" ? 7.5 : 6.5);
      state.settings.komi = defaultKomi;
      $("#komi-input").value = defaultKomi;
      savePrefs();
    });
    $("#komi-input").addEventListener("change", () => {
      state.settings.komi = Number($("#komi-input").value) || 0;
      savePrefs();
    });
    $("#time-limit-input").addEventListener("change", () => {
      state.settings.time_limit = Number($("#time-limit-input").value) || 0;
      if (!state.settings.time_limit) state.settings.byo_yomi = 0;
      renderSettings();
      savePrefs();
    });
    $("#byo-yomi-input").addEventListener("change", () => {
      state.settings.byo_yomi = Number($("#byo-yomi-input").value) || 0;
      savePrefs();
    });

    $("#hint-btn").addEventListener("click", getHint);
    $("#estimate-btn").addEventListener("click", getEstimate);
    $("#undo-btn").addEventListener("click", undoMove);
    $("#redo-btn").addEventListener("click", redoMove);
    $("#ai-retry-btn").addEventListener("click", retryAi);
    $("#pass-btn").addEventListener("click", requestPass);
    $("#resign-btn").addEventListener("click", requestResign);
    $("#review-btn").addEventListener("click", () => {
      if (state.view === "review") { exitReview(); return; }
      if (!state.game) return;
      if (state.game.phase !== "ended") { toast("对局结束后才能复盘"); return; }
      startReview();
    });
    $("#sgf-btn").addEventListener("click", downloadSgf);
    $("#numbers-btn").addEventListener("click", () => {
      state.showNumbers = !state.showNumbers;
      updateNumbersButton();
      savePrefs();
      drawBoard();
    });
    $("#flip-btn").addEventListener("click", () => {
      state.flip = !state.flip;
      updateFlipButton();
      savePrefs();
      drawBoard();
      toast(state.flip ? "棋盘已翻转" : "棋盘已复位");
    });
    $("#sound-btn").addEventListener("click", () => {
      state.sound = !state.sound;
      updateSoundButton();
      savePrefs();
      if (state.sound) { ensureAudio(); playStoneSound(true); }
    });
    $("#help-btn").addEventListener("click", showHelp);
    $("#records-btn").addEventListener("click", showRecords);
    $("#lang-btn").addEventListener("click", () => setLang(state.lang === "en" ? "zh" : "en"));
    const settingsToggle = $("#settings-toggle");
    if (settingsToggle) {
      settingsToggle.addEventListener("click", () => {
        const open = $(".rail").classList.toggle("settings-open");
        settingsToggle.setAttribute("aria-expanded", open ? "true" : "false");
        settingsToggle.classList.toggle("open", open);
        if (open) {
          const settings = $(".settings");
          if (settings && settings.scrollIntoView) {
            settings.scrollIntoView({ block: "nearest", behavior: state.reduceMotion ? "auto" : "smooth" });
          }
        }
      });
    }
    $("#engine-card").addEventListener("click", () => {
      if (!engineNeedsAttention()) return;
      engineRetry();
    });
    $("#engine-card").addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      if (!engineNeedsAttention()) return;
      e.preventDefault();
      engineRetry();
    });
    $("#engine-retry-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      engineRetry();
    });

    $$(".nav-btn[data-view]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const view = btn.dataset.view;
        if (view === "review") {
          setTab("review");
          if (state.reviewPayload && state.reviewPayload.status === "done") {
            state.view = "review";
          }
        } else {
          state.view = "play";
          setTab("analysis");
        }
        renderAll();
      });
    });

    $$(".tab").forEach((tab) => {
      tab.addEventListener("click", () => setTab(tab.dataset.tab));
    });

    $("#scoring-bar").addEventListener("click", (e) => {
      if (e.target.closest("#confirm-score-btn")) confirmScore();
      else if (e.target.closest("#resume-btn")) resumePlay();
      else if (e.target.closest("#reestimate-btn")) reestimateScore();
    });
    $("#start-review-inline").addEventListener("click", () => {
      if (state.game && state.game.phase === "ended" && state.game.turn > 0) startReview();
      else toast("对局结束后才能复盘");
    });

    $("#review-list").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-review-index]");
      if (btn) selectReview(Number(btn.dataset.reviewIndex));
    });
    $("#review-chart").addEventListener("mousemove", onChartMove);
    $("#review-chart").addEventListener("mouseleave", () => {
      state.chartHover = null;
      $("#chart-tip").classList.add("hidden");
      drawReviewChart();
    });
    $("#review-chart").addEventListener("click", onChartClick);
    $("#export-review-btn").addEventListener("click", downloadReviewReport);

    document.addEventListener("keydown", (e) => {
      if (state.confirmOpen) {
        if (e.key === "Escape" && state.confirmClose) state.confirmClose();
        return;
      }
      if (state.detailOpen) {
        if (e.key === "Escape" && state.detailClose) state.detailClose();
        return;
      }
      if (state.modalOpen) {
        if (e.key === "Escape") closeModal();
        else if (e.key === "?" && state.helpOpen) closeModal();
        return;
      }
      if (e.target && /INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
      if (e.key === "?") { e.preventDefault(); showHelp(); return; }
      const key = e.key.toLowerCase();
      if (state.view === "review" && state.reviewPayload && state.reviewPayload.items && state.reviewPayload.items.length) {
        if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
          e.preventDefault();
          selectReview((state.reviewIndex ?? 0) + (e.key === "ArrowLeft" ? -1 : 1));
          return;
        }
      }
      if (key === "h") { e.preventDefault(); getHint(); }
      else if (key === "u") { e.preventDefault(); undoMove(); }
      else if (key === "y") { e.preventDefault(); redoMove(); }
      else if (key === "p") { e.preventDefault(); requestPass(); }
      else if (key === "e") { e.preventDefault(); getEstimate(); }
      else if (key === "n") { e.preventDefault(); requestNewGame(); }
      else if (key === "r") {
        e.preventDefault();
        if (state.view === "review") exitReview();
        else if (state.game && state.game.phase === "ended") startReview();
        else toast("对局结束后才能复盘");
      }
    });

    const board = $("#board");
    board.addEventListener("mousemove", onBoardMove);
    board.addEventListener("mouseleave", () => {
      state.hover = null;
      $("#coords-tip").textContent = "";
      drawBoard();
    });
    board.addEventListener("click", onBoardClick);

    window.addEventListener("resize", () => resizeBoard());
    if (window.ResizeObserver) {
      new ResizeObserver(() => resizeBoard()).observe($(".board-area"));
    }
    setInterval(updateClock, 1000);
  }

  // ==================== 设置与新建 ====================
  function renderLevels() {
    const el = $("#level-list");
    el.innerHTML = state.levels.map((level) => `
      <button class="level-btn ${level.id === state.levelId ? "active" : ""} ${level.guarantee ? "guarantee" : ""}"
        data-level="${esc(level.id)}" role="radio" aria-checked="${level.id === state.levelId}">
        <span class="lv-name">${esc(level.name)}</span>
        <span class="lv-short">${esc(level.short)}</span>
      </button>`).join("");
    updateLevelDesc();
  }

  function updateLevelDesc() {
    const level = state.levels.find((l) => l.id === state.levelId);
    $("#level-desc").textContent = level ? level.desc : "";
  }

  function renderSettings() {
    $$(".segmented").forEach((seg) => {
      const key = seg.dataset.setting;
      const value = String(state.settings[key]);
      $$("button", seg).forEach((btn) => btn.classList.toggle("active", btn.dataset.value === value));
    });
    $("#handicap-input").value = String(state.settings.handicap || 0);
    $("#komi-input").value = String(state.settings.komi);
    $("#time-limit-input").value = String(state.settings.time_limit || 0);
    $("#byo-yomi-input").value = String(state.settings.byo_yomi || 0);
    $("#byo-yomi-input").disabled = !Number(state.settings.time_limit);
    $("#strength-badge").innerHTML = `<span class="badge-dot"></span>${esc(strengthBadgeText())}`;
  }

  function strengthBadgeText() {
    const activeId = state.game && state.game.level ? state.game.level.id : state.levelId;
    const level = state.levels.find((l) => l.id === activeId);
    if (!level) return "职业二段 · 默认";
    if (level.guarantee) return "职业二段 · 默认";
    if (level.human) return `${level.name} · 人类风格`;
    return `${level.name} · 职业强度`;
  }

  function applySetting(key, value) {
    if (key === "size") state.settings.size = Number(value);
    else if (key === "player_color") state.settings.player_color = value;
    else if (key === "rules") {
      state.settings.rules = value;
      state.settings.komi = state.settings.handicap ? 0.5 : (value === "chinese" ? 7.5 : 6.5);
      $("#komi-input").value = String(state.settings.komi);
    }
    state.selectedCandidate = null;
    renderSettings();
    savePrefs();
  }

  async function selectLevel(levelId) {
    if (!levelId || levelId === state.levelId) return;
    if (!state.game) {
      state.levelId = levelId;
      renderLevels();
      savePrefs();
      return;
    }
    const level = state.levels.find((l) => l.id === levelId);
    const ok = await confirmDialog({
      kicker: "切换强度",
      title: `切换到${level ? level.name : "新档位"}？`,
      message: "更换陪练强度会立即开始一盘新棋，当前对局将被结束。",
      confirmText: "开始新对局",
    });
    if (!ok) return;
    state.levelId = levelId;
    renderLevels();
    savePrefs();
    await createGame({ noConfirm: true });
  }

  async function requestNewGame() {
    if (state.game && state.game.turn > 0 && state.game.phase !== "ended") {
      const ok = await confirmDialog({
        kicker: "新对局",
        title: "结束当前对局？",
        message: "当前棋局尚未结束，开始新对局会放弃本局进度。",
        confirmText: "开始新对局",
      });
      if (!ok) return;
    }
    await createGame({ noConfirm: true });
  }

  async function createGame({ silent = false, noConfirm = false } = {}) {
    try {
      setBusy(true);
      const body = {
        ...state.settings,
        level_id: state.levelId,
      };
      const game = await api("/api/new", { body });
      state.reviewPayload = null;
      state.reviewIndex = null;
      state.selectedCandidate = null;
      state.hintVisible = false;
      state.ownershipVisible = false;
      state.view = "play";
      state.tab = "analysis";
      state.resultKey = null;
      state.extraPolls = 0;
      applyGame(game, { initial: true });
      if (!silent) toast(`新对局开始 · ${game.level.name}`);
      if (game.ai_thinking) schedulePoll(500);
      return game;
    } catch (err) {
      toast(err.message || "新建对局失败", "error", 4000);
      throw err;
    } finally {
      setBusy(false);
    }
  }

  function setBusy(value) {
    state.busy = value;
    renderChrome();
  }

  function formatClock(totalSeconds) {
    const s = Math.max(0, Math.floor(Number(totalSeconds) || 0));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    if (h > 0) return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
    return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  }

  function updateClock() {
    if (!state.game) return;
    const el = $("#stat-time");
    if (!el) return;
    const elapsed = state.clockBase + (Date.now() - state.clockAt) / 1000;
    el.textContent = formatClock(elapsed);
  }

  // ==================== 状态应用与轮询 ====================
  function applyGame(game, { initial = false } = {}) {
    const prev = state.game;
    const changedGame = !prev || prev.id !== game.id;
    const prevTurn = prev ? prev.turn : -1;
    const prevAiError = prev ? prev.ai_error : null;
    state.game = game;
    if (game.ai_error && game.ai_error !== prevAiError) {
      toast(`AI 计算中断：${game.ai_error}`, "error", 6000);
    }
    if (changedGame) {
      state.view = "play";
      state.tab = "analysis";
      state.reviewPayload = null;
      state.reviewIndex = null;
      state.hintVisible = false;
      state.ownershipVisible = false;
      state.selectedCandidate = null;
      state.lastMoveCount = game.turn;
      state.commentCount = -1;
      if (game.level && game.level.id && game.level.id !== state.levelId) {
        state.levelId = game.level.id;
        if (state.levels.length) renderLevels();
      }
    }
    if (game.turn !== prevTurn && game.turn > state.lastMoveCount) {
      state.lastMoveCount = game.turn;
      state.hintVisible = false;
      state.selectedCandidate = null;
      if (game.last_move && game.last_move !== "pass") {
        const black = game.last_move_color === "B";
        playStoneSound(black);
        if (!state.reduceMotion) {
          state.boardAnim = { vertex: game.last_move, start: performance.now() };
          animateBoard();
        }
      }
    }
    if (prev && prev.ai_thinking && !game.ai_thinking && game.phase === "playing") {
      state.extraPolls = Math.max(state.extraPolls, 2);
    }
    try { localStorage.setItem("yixin_last_game", game.id); } catch (e) { /* ignore */ }
    state.extraPolls = Math.max(state.extraPolls, 0);
    renderAll();
    if (!initial) maybeShowResult();
    state.clockBase = Number(game.elapsed) || 0;
    state.clockAt = Date.now();
    updateClock();
    if (!state.initialLoaded) state.initialLoaded = true;
    if (changedGame) {
      state.pollFails = 0;
    }
    if (game.ai_thinking || (game.phase === "scoring" && game.scoring && game.scoring.estimating)) {
      schedulePoll(700);
    } else if (game.clock && game.clock.enabled && game.phase === "playing") {
      schedulePoll(1000);
    } else if (state.extraPolls > 0 && game.phase === "playing" && (!game.candidates || game.candidates.length === 0)) {
      state.extraPolls -= 1;
      schedulePoll(1200);
    }
  }

  function renderClocks(g) {
    const info = g && g.clock;
    const enabled = !!(info && info.enabled && g.phase === "playing");
    const ai = $("#ai-clock");
    const human = $("#human-clock");
    if (!enabled) {
      ai.classList.add("hidden");
      human.classList.add("hidden");
      return;
    }
    const aiColor = g.ai_color;
    const humanColor = g.player_color;
    const labelFor = (side) => {
      const main = Number(info[`${side}_main`] != null ? info[`${side}_main`] : info[side]) || 0;
      if (main > 0) return formatClock(main);
      if (Number(info.byo_yomi)) {
        const periods = info[`${side}_periods`] != null ? info[`${side}_periods`] : 0;
        return `${state.lang === "en" ? "Byo" : "读秒"} ${formatClock(info[`${side}_byo`] || 0)} ×${periods}`;
      }
      return formatClock(0);
    };
    ai.classList.remove("hidden");
    human.classList.remove("hidden");
    ai.textContent = labelFor(aiColor);
    human.textContent = labelFor(humanColor);
    ai.classList.toggle("active", info.to_move === aiColor);
    human.classList.toggle("active", info.to_move === humanColor);
    ai.classList.toggle("low", Number(info[aiColor]) <= 30);
    human.classList.toggle("low", Number(info[humanColor]) <= 30);
  }

  function schedulePoll(delay = 800) {
    clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(pollGame, delay);
  }

  async function pollGame() {
    if (!state.game) return;
    try {
      const game = await api(`/api/game/${state.game.id}`);
      state.pollFails = 0;
      applyGame(game);
      const thinking = game.ai_thinking || (game.phase === "scoring" && game.scoring && game.scoring.estimating);
      if (thinking) {
        schedulePoll(750);
      } else if (game.clock && game.clock.enabled && game.phase === "playing") {
        schedulePoll(1000);
      } else if (state.extraPolls > 0 && game.phase === "playing" && (!game.candidates || game.candidates.length === 0)) {
        state.extraPolls -= 1;
        schedulePoll(1300);
      }
    } catch (err) {
      state.pollFails += 1;
      if (state.pollFails < 5) schedulePoll(1600);
      else toast("与棋局服务失去连接，请刷新页面", "error", 5000);
    }
  }

  function maybeShowResult() {
    const g = state.game;
    if (!g || g.phase !== "ended" || !g.result || state.modalOpen) return;
    const key = `${g.id}-${g.result.text}-${g.turn}`;
    if (state.resultKey === key) return;
    state.resultKey = key;
    showResultModal(g);
  }

  // ==================== 渲染 ====================
  function renderAll() {
    if (!state.game) return;
    renderChrome();
    renderPlayers();
    renderWinrate();
    renderCandidates();
    renderComments();
    renderHint();
    renderScoring();
    renderReviewPanel();
    drawBoard();
    syncLang();
  }

  function currentPlayerColor() {
    return state.game ? state.game.player_color : "B";
  }

  function playerTurn() {
    const g = state.game;
    return !!g && g.phase === "playing" && g.to_move === g.player_color && !g.ai_thinking;
  }

  function aiTurn() {
    const g = state.game;
    return !!g && g.phase === "playing" && g.to_move === g.ai_color && !g.ai_thinking;
  }

  function renderChrome() {
    const g = state.game;
    if (!g) return;
    const level = g.level || {};
    const thinking = g.ai_thinking;
    const scoring = g.phase === "scoring";
    const ended = g.phase === "ended";
    const reviewMode = state.view === "review";

    $("#move-crumb").textContent = reviewMode && state.reviewPayload && state.reviewPayload.items
      ? `复盘 · 第 ${state.reviewPayload.items[state.reviewIndex ?? 0]?.move_no ?? 0} 手`
      : `第 ${g.turn} 手`;
    let phaseText = "等待落子";
    if (reviewMode) phaseText = "复盘研读";
    else if (thinking) phaseText = "AI 思考中";
    else if (scoring) phaseText = "数子确认";
    else if (ended) phaseText = g.result ? g.result.text : "对局结束";
    else if (g.to_move === g.player_color) phaseText = "轮到你落子";
    else phaseText = "等待 AI";
    $("#phase-crumb").textContent = phaseText;

    $("#strength-badge").innerHTML = `<span class="badge-dot"></span>${esc(strengthBadgeText())}`;
    $("#ai-name").textContent = `弈心 · ${level.name || "职业二段"}`;
    $("#ai-rank").textContent = level.short || "PRO 2D";
    $("#ai-sub").textContent = level.human ? "Human SL · 人类风格" : `主模型 · ${level.visits || 400} visits`;
    const humanColor = g.player_color === "B" ? "黑" : "白";
    $("#human-rank").textContent = `执${humanColor}`;
    $("#human-sub").textContent = thinking ? "AI 正在读局，稍安勿躁" :
      scoring ? "确认死活后结束对局" :
      ended ? "本局已结束" :
      g.to_move === g.player_color ? "轮到你落子" : "等待 AI 落子";

    $("#human-turn").className = `turn-indicator ${playerTurn() && !reviewMode ? "active" : ""}`;
    $("#human-turn").innerHTML = `<span></span>${playerTurn() && !reviewMode ? "轮到你" : "等待"}`;
    $("#ai-turn").className = `turn-indicator ${(thinking || aiTurn()) && !reviewMode ? "active" : ""}`;
    $("#ai-turn").innerHTML = `<span></span>${thinking ? "思考中" : (aiTurn() ? "落子" : "等待")}`;

    $("#ai-captures").textContent = String((g.captures && g.captures[g.ai_color]) || 0);
    $("#human-captures").textContent = String((g.captures && g.captures[g.player_color]) || 0);
    $("#ai-retry-btn").classList.toggle("hidden", !g.ai_error || g.phase !== "playing");
    $("#ai-sub").textContent = g.ai_error ? `计算中断：${g.ai_error}` :
      (level.human ? "Human SL · 人类风格" : `主模型 · ${level.visits || 400} visits`);
    const statMoves = $("#stat-moves");
    const statCaptures = $("#stat-captures");
    if (statMoves) statMoves.textContent = String(g.turn || 0);
    if (statCaptures) statCaptures.textContent = String(((g.captures && g.captures.B) || 0) + ((g.captures && g.captures.W) || 0));
    $("#board-badge").textContent = `${g.size} 路`;
    $("#board-badge").textContent = `${g.size} 路 · ${g.rules === "chinese" ? "中国规则" : "日本规则"}`;
    $("#thinking-pill").classList.toggle("hidden", !thinking);
    if (thinking) $("#thinking-text").textContent = ` AI 正在思考 · ${level.name || ""}`;
    $("#ai-strip").classList.toggle("active", thinking || aiTurn());
    $("#human-strip").classList.toggle("active", playerTurn() && !reviewMode);

    const disabled = reviewMode || state.busy;
    $("#hint-btn").disabled = disabled || !playerTurn();
    $("#estimate-btn").disabled = disabled || !(g.phase === "playing" || g.phase === "scoring");
    $("#undo-btn").disabled = disabled || !g.can_undo || g.phase !== "playing";
    $("#redo-btn").disabled = disabled || !g.can_redo || g.phase !== "playing";
    $("#pass-btn").disabled = disabled || !playerTurn();
    $("#resign-btn").disabled = disabled || g.phase !== "playing";
    $("#review-btn").disabled = reviewMode ? false : !(g.phase === "ended" && g.turn > 0) && !(g.review && g.review.status === "running");
    $("#sgf-btn").disabled = !g;
    $("#review-btn").innerHTML = reviewMode
      ? `<svg viewBox="0 0 24 24"><path d="M9 7 4 12l5 5"/><path d="M4 12h9a7 7 0 0 1 7 7"/></svg> 返回对局`
      : `<svg viewBox="0 0 24 24"><path d="M4 19V5M4 19h16"/><path d="m7 15 4-5 3 3 5-7"/></svg> 复盘`;
    $("#numbers-btn").classList.toggle("active", state.showNumbers);
    $("#flip-btn").classList.toggle("active", state.flip);
    $("#sound-btn").classList.toggle("active", state.sound);
    const board = $("#board");
    board.classList.toggle("disabled", reviewMode || !playerTurn() || g.phase !== "playing");
    $("#model-info").textContent = `KataGo ${state.engine && state.engine.version ? "v" + state.engine.version : ""} · b10c512${state.engine && state.engine.human_model ? " + Human SL" : ""}`;
    renderClocks(g);
    syncLang();
  }

  function renderPlayers() {
    // player strips are updated in renderChrome; kept for future per-player metadata
  }

  function renderWinrate() {
    const g = state.game;
    if (!g) return;
    let ev = g.eval || { winrate_black: 0.5, score_lead_black: 0, visits: 0 };
    if (state.hintVisible && g.hint && g.hint.eval) ev = g.hint.eval;
    const black = clamp(Number(ev.winrate_black) || 0, 0.002, 0.998);
    const white = 1 - black;
    $("#black-wr").textContent = pct(black);
    $("#white-wr").textContent = pct(white);
    $("#black-bar").style.width = `${black * 100}%`;
    $("#white-bar").style.width = `${white * 100}%`;
    const lead = Number(ev.score_lead_black) || 0;
    const leadEl = $("#lead-text");
    leadEl.className = `lead ${lead > 0.05 ? "black" : lead < -0.05 ? "white" : ""}`;
    leadEl.textContent = Math.abs(lead) < 0.05 ? "盘面均衡" : (lead > 0 ? `黑棋领先 ${lead.toFixed(1)} 目` : `白棋领先 ${Math.abs(lead).toFixed(1)} 目`);
    $("#visits-text").textContent = `${Number(ev.visits) || 0} visits`;
    $("#score-lead").textContent = fmtScore(lead);
    $("#score-note").textContent = "黑棋视角";
    $("#live-pill").classList.toggle("busy", !!g.ai_thinking || !!(g.scoring && g.scoring.estimating));
    $("#live-pill").innerHTML = (g.ai_thinking || (g.scoring && g.scoring.estimating))
      ? `<span></span>THINKING`
      : `<span></span>LIVE`;
  }

  function currentCandidates() {
    const g = state.game;
    if (!g) return [];
    if (state.hintVisible && g.hint && g.hint.candidates && g.hint.candidates.length) return g.hint.candidates;
    return g.candidates || [];
  }

  function renderCandidates() {
    const listEl = $("#candidate-list");
    const g = state.game;
    if (!g) return;
    const list = currentCandidates();
    const note = $("#candidate-note");
    if (!list.length) {
      note.textContent = g.ai_thinking ? "AI 正在搜索" : "等待 AI 分析";
      listEl.innerHTML = `<div class="empty-state">
        <div class="empty-mark">${g.ai_thinking ? "算" : "卦"}</div>
        <p>${g.ai_thinking ? "AI 正在计算候选点" : "暂无候选点"}</p>
        <span>${g.ai_thinking ? "强档位每手约 2–5 秒" : "落子后自动更新胜率与推荐"}</span>
      </div>`;
      return;
    }
    note.textContent = g.ai_thinking ? "AI 正在搜索" : "按胜率排序";
    listEl.innerHTML = list.map((c, i) => {
      const wr = Number(c.winrate) || 0;
      const lead = Number(c.score_lead) || 0;
      return `<button class="candidate-row ${i === 0 ? "top" : ""}" data-candidate="${i}">
        <span class="cand-rank">${String.fromCharCode(65 + i)}</span>
        <span class="cand-main">
          <span class="cand-line"><span class="cand-move">${esc(c.move === "pass" ? "停一手" : c.move)}</span>
            <span class="cand-wr">黑胜 ${pct(wr)}</span></span>
          <span class="cand-bar"><span style="width:${clamp(wr * 100, 2, 100)}%"></span></span>
          <span class="cand-meta">AI 目差 ${fmtScore(lead)} · ${Number(c.visits) || 0} visits</span>
        </span>
      </button>`;
    }).join("");
    $$("#candidate-list [data-candidate]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.dataset.candidate);
        state.selectedCandidate = state.selectedCandidate === idx ? null : idx;
        state.hintVisible = false;
        renderCandidates();
        renderHint();
        drawBoard();
      });
    });
    syncLang();
  }

  function commentLabel(tone) {
    return ({ ai: "AI 落子", good: "AI 点评", warn: "AI 提醒", bad: "AI 点评", result: "对局结果", system: "对局" })[tone] || "观察";
  }

  function renderComments() {
    const list = $("#comment-list");
    if (!state.game) return;
    const comments = state.game.commentary || [];
    if (comments.length === state.commentCount) return;
    state.commentCount = comments.length;
    if (!comments.length) {
      list.innerHTML = `<div class="empty-state"><div class="empty-mark">评</div><p>AI 点评将在这里出现</p><span>落子后自动生成</span></div>`;
      return;
    }
    list.innerHTML = comments.map((c) => `<div class="comment ${esc(c.tone || "info")}">
      <b>${esc(commentLabel(c.tone))}${c.move_no ? ` · 第 ${c.move_no} 手` : ""}</b>
      ${esc(c.text)}
    </div>`).join("");
    list.scrollTop = list.scrollHeight;
    syncLang();
  }

  function renderHint() {
    const g = state.game;
    if (!g) return;
    const hint = g.hint;
    const card = $("#hint-card");
    const legend = $("#hint-legend");
    if (!hint || !hint.candidates || !hint.candidates.length || !state.hintVisible) {
      card.classList.add("hidden");
      legend.classList.add("hidden");
      return;
    }
    const c = hint.candidates[0];
    const ev = hint.eval || {};
    card.classList.remove("hidden");
    $("#hint-move").textContent = c.move === "pass" ? "停一手" : c.move;
    $("#hint-text").textContent = `AI 推荐 ${c.move === "pass" ? "停一手" : c.move}，黑棋胜率 ${pct(c.winrate)}，${Number(ev.score_lead_black) >= 0 ? "领先" : "落后"} ${Math.abs(Number(ev.score_lead_black) || 0).toFixed(1)} 目。`;
    const top = hint.candidates.slice(0, 3);
    legend.classList.remove("hidden");
    legend.innerHTML = top.map((item, i) => `<span><b>${String.fromCharCode(65 + i)}</b>${esc(item.move === "pass" ? "停一手" : item.move)} ${pct(item.winrate)}</span>`).join("");
    syncLang();
  }

  function renderScoring() {
    const g = state.game;
    const bar = $("#scoring-bar");
    const isScoring = !!g && g.phase === "scoring" && state.view !== "review";
    document.body.classList.toggle("scoring", isScoring);
    if (!isScoring) {
      bar.classList.add("hidden");
      return;
    }
    bar.classList.remove("hidden");
    const sc = g.scoring || {};
    const score = sc.score || {};
    const estimating = !!sc.estimating;
    $("#scoring-title").textContent = estimating ? "AI 正在判断死活…" : (score.text || "等待数子");
    $("#scoring-detail").textContent = estimating
      ? "约需 2–5 秒，请稍候"
      : `黑 ${score.black_score ?? "—"} : 白 ${score.white_score ?? "—"} · 贴目 ${score.komi ?? g.komi} · 点击棋子标记死子`;
    $("#confirm-score-btn").disabled = estimating;
    $("#reestimate-btn").disabled = estimating;
    if (sc.error) $("#scoring-detail").textContent = `AI 判断失败，已按盘面计算：${sc.error}`;
    syncLang();
  }

  function setTab(name) {
    state.tab = name;
    $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    $("#panel-analysis").classList.toggle("hidden", name !== "analysis");
    $("#panel-comments").classList.toggle("hidden", name !== "comments");
    $("#panel-review").classList.toggle("hidden", name !== "review");
    if (name === "review") {
      if (state.reviewPayload && state.reviewPayload.status === "done") {
        state.view = "review";
      }
      renderReviewPanel();
      if (state.game && state.game.review && state.game.review.status === "running") pollReview();
      drawBoard();
    } else if (name === "analysis" || name === "comments") {
      if (state.view === "review") { state.view = "play"; drawBoard(); }
    }
    syncLang();
  }

  // ==================== 棋盘绘制 ====================
  const boardState = { w: 0, h: 0, dpr: 1, geom: null, raf: 0 };

  function setupBoard() {
    resizeBoard();
    const canvas = $("#board");
    const ctx = canvas.getContext("2d");
    boardState.ctx = ctx;
  }

  function resizeBoard() {
    const canvas = $("#board");
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.round(rect.width);
    const h = Math.round(rect.height);
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    boardState.w = w;
    boardState.h = h;
    boardState.dpr = dpr;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    boardState.ctx = ctx;
    drawBoard();
  }

  function parseBoard(flat, size) {
    if (!flat || flat.length !== size * size) return null;
    const rows = [];
    for (let y = 0; y < size; y++) {
      const row = [];
      for (let x = 0; x < size; x++) row.push(Number(flat[y * size + x]) || 0);
      rows.push(row);
    }
    return rows;
  }

  function displayData() {
    const g = state.game;
    if (!g) return null;
    if (state.view === "review" && state.reviewPayload && state.reviewPayload.items && state.reviewPayload.items.length) {
      const idx = clamp(state.reviewIndex ?? state.reviewPayload.items.length - 1, 0, state.reviewPayload.items.length - 1);
      const item = state.reviewPayload.items[idx];
      const board = parseBoard(item.board, g.size);
      if (board) {
        return { board, last: item.move, moves: g.moves.slice(0, item.move_no), size: g.size, review: item };
      }
    }
    return { board: g.board, last: g.last_move, moves: g.moves, size: g.size, review: null };
  }

  function drawBoard() {
    const canvas = $("#board");
    const ctx = boardState.ctx || canvas.getContext("2d");
    if (!ctx || !boardState.w) return;
    const data = displayData();
    if (!data) return;
    const { board, last, size } = data;
    const w = boardState.w;
    const h = boardState.h;
    const pad = Math.max(20, Math.min(w, h) * 0.062);
    const cell = (Math.min(w, h) - pad * 2) / (size - 1);
    const x0 = pad;
    const y0 = pad;
    const geom = { x0, y0, cell, size, pad, w, h };
    boardState.geom = geom;

    ctx.clearRect(0, 0, w, h);
    drawWood(ctx, w, h);
    drawGrid(ctx, geom);
    drawCoordinates(ctx, geom);

    // 地盘 / 归属
    if (state.ownershipVisible && state.game && state.game.ownership && state.view !== "review") {
      drawOwnership(ctx, geom, board, state.game.ownership);
    }
    if (state.game && state.game.phase === "scoring" && state.game.scoring && state.game.scoring.score && state.view !== "review") {
      drawScoringTerritory(ctx, geom, board, state.game.scoring.score);
    }

    // 棋子
    const moveMap = new Map();
    (data.moves || []).forEach((m) => { if (m.vertex && m.vertex !== "pass") moveMap.set(m.vertex, m.move_no); });
    const dead = new Set();
    if (state.game && state.game.phase === "scoring" && state.game.scoring && state.game.scoring.score) {
      (state.game.scoring.score.dead || []).forEach((v) => dead.add(v));
    }
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const v = board[y][x];
        if (!v) continue;
        const vertex = `${COLS[x]}${size - y}`;
        const pt = pointForVertex(vertex, geom);
        if (!pt) continue;
        const isLast = vertex === last && last !== "pass";
        const num = state.showNumbers ? moveMap.get(vertex) : null;
        drawStone(ctx, pt.px, pt.py, cell, v === STONE_BLACK ? "B" : "W", {
          last: isLast,
          number: num,
          dead: dead.has(vertex),
          scale: lastStoneScale(vertex),
        });
      }
    }

    // 悬停虚影
    if (state.hover && state.view !== "review" && state.game && state.game.phase === "playing" && playerTurn()) {
      const pt = pointForVertex(state.hover, geom);
      if (pt && board[pt.y] && board[pt.y][pt.x] === 0) {
        drawStone(ctx, pt.px, pt.py, cell, state.game.player_color, { ghost: true, alpha: 0.34 });
      }
    }

    // 提示 / 变化图
    if (state.game && state.view !== "review" && state.game.phase === "playing") {
      const cands = currentCandidates();
      if (state.selectedCandidate !== null && cands[state.selectedCandidate]) {
        drawPV(ctx, geom, board, cands[state.selectedCandidate], state.game.to_move);
      } else if (state.hintVisible && state.game.hint && state.game.hint.candidates) {
        drawHintMarkers(ctx, geom, board, state.game.hint.candidates.slice(0, 3));
      }
    }

    // 复盘标记
    if (data.review && data.review.move && data.review.move !== "pass") {
      const pt = pointForVertex(data.review.move, geom);
      if (pt) {
        ctx.save();
        ctx.strokeStyle = "rgba(184,76,61,.85)";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(pt.px, pt.py, cell * 0.44, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }
    }
  }

  function drawWood(ctx, w, h) {
    const grad = ctx.createLinearGradient(0, 0, w, h);
    grad.addColorStop(0, "#e2c28c");
    grad.addColorStop(0.42, "#d8b47a");
    grad.addColorStop(0.78, "#cfa46a");
    grad.addColorStop(1, "#c2935b");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, w, h);

    ctx.save();
    ctx.globalAlpha = 0.16;
    for (let i = 0; i < 38; i++) {
      const base = (i / 38) * h;
      const amp = 3 + (i % 5) * 1.6;
      ctx.beginPath();
      for (let x = 0; x <= w; x += 18) {
        const y = base + Math.sin(x * 0.011 + i * 1.7) * amp + Math.sin(x * 0.003 + i) * amp * 0.6;
        if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.strokeStyle = i % 4 === 0 ? "rgba(92,58,22,.32)" : "rgba(112,76,34,.18)";
      ctx.lineWidth = i % 9 === 0 ? 1.1 : 0.55;
      ctx.stroke();
    }
    ctx.restore();

    const vig = ctx.createRadialGradient(w * 0.46, h * 0.4, w * 0.08, w * 0.5, h * 0.5, w * 0.72);
    vig.addColorStop(0, "rgba(255,241,207,0.16)");
    vig.addColorStop(0.62, "rgba(255,241,207,0.02)");
    vig.addColorStop(1, "rgba(70,40,12,0.22)");
    ctx.fillStyle = vig;
    ctx.fillRect(0, 0, w, h);

    ctx.save();
    ctx.strokeStyle = "rgba(255,245,221,.18)";
    ctx.lineWidth = 1;
    ctx.strokeRect(3.5, 3.5, w - 7, h - 7);
    ctx.restore();
  }

  function drawGrid(ctx, geom) {
    const { x0, y0, cell, size } = geom;
    const span = (size - 1) * cell;
    ctx.save();
    ctx.strokeStyle = "rgba(52,33,11,.68)";
    ctx.lineWidth = Math.max(0.75, cell * 0.026);
    for (let i = 0; i < size; i++) {
      ctx.beginPath();
      ctx.moveTo(x0, y0 + i * cell);
      ctx.lineTo(x0 + span, y0 + i * cell);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(x0 + i * cell, y0);
      ctx.lineTo(x0 + i * cell, y0 + span);
      ctx.stroke();
    }
    ctx.strokeStyle = "rgba(47,29,9,.82)";
    ctx.lineWidth = Math.max(1, cell * 0.04);
    ctx.strokeRect(x0, y0, span, span);

    let stars;
    if (size === 19) {
      stars = [];
      for (const x of [3, 9, 15]) for (const y of [3, 9, 15]) stars.push([x, y]);
    } else if (size === 13) {
      stars = [[3, 3], [3, 9], [6, 6], [9, 3], [9, 9]];
    } else {
      stars = [[2, 2], [2, 6], [4, 4], [6, 2], [6, 6]];
    }
    ctx.fillStyle = "rgba(38,24,8,.82)";
    for (const [x, y] of stars) {
      ctx.beginPath();
      ctx.arc(x0 + x * cell, y0 + y * cell, Math.max(2, cell * 0.075), 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  function drawCoordinates(ctx, geom) {
    const { x0, y0, cell, size, pad } = geom;
    const span = (size - 1) * cell;
    const fs = clamp(cell * 0.27, 8, 11);
    ctx.save();
    ctx.fillStyle = "rgba(65,42,15,.62)";
    ctx.font = `500 ${fs}px "Cascadia Mono", "Microsoft YaHei UI", monospace`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (let x = 0; x < size; x++) {
      const px = x0 + x * cell;
      ctx.fillText(state.flip ? COLS[size - 1 - x] : COLS[x], px, y0 + span + cell * 0.72);
    }
    ctx.textAlign = "right";
    for (let y = 0; y < size; y++) {
      const py = y0 + y * cell;
      ctx.fillText(String(state.flip ? y + 1 : size - y), x0 - cell * 0.66, py);
    }
    // 一点点装饰性坐标角标
    ctx.fillStyle = "rgba(70,45,17,.22)";
    ctx.font = `400 ${fs * 0.8}px "Cascadia Mono", monospace`;
    ctx.textAlign = "left";
    ctx.fillText(state.lang === "en" ? "YIXIN" : "弈心", 7, pad - 8);
    ctx.restore();
  }

  function drawStone(ctx, px, py, cell, color, opts = {}) {
    const { last = false, number = null, dead = false, ghost = false, alpha = 1, scale = 1 } = opts;
    const r = cell * 0.455 * (scale || 1);
    if (r <= 0) return;
    ctx.save();
    ctx.globalAlpha = alpha;
    if (!ghost) {
      ctx.shadowColor = "rgba(24,15,5,.42)";
      ctx.shadowBlur = r * 0.55;
      ctx.shadowOffsetY = r * 0.24;
    }
    const grad = ctx.createRadialGradient(px - r * 0.34, py - r * 0.36, r * 0.1, px, py, r * 1.05);
    if (color === "B") {
      grad.addColorStop(0, "#72756d");
      grad.addColorStop(0.24, "#3d403b");
      grad.addColorStop(0.67, "#171a17");
      grad.addColorStop(1, "#070907");
    } else {
      grad.addColorStop(0, "#fffdf7");
      grad.addColorStop(0.34, "#f6f0e2");
      grad.addColorStop(0.78, "#ddd3bd");
      grad.addColorStop(1, "#b8ab91");
    }
    ctx.beginPath();
    ctx.arc(px, py, r, 0, Math.PI * 2);
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.shadowColor = "transparent";
    ctx.lineWidth = Math.max(0.7, r * 0.045);
    ctx.strokeStyle = color === "B" ? "rgba(255,255,255,.14)" : "rgba(76,56,27,.3)";
    ctx.stroke();
    if (!ghost) {
      ctx.beginPath();
      ctx.arc(px - r * 0.28, py - r * 0.32, r * 0.18, 0, Math.PI * 2);
      ctx.fillStyle = color === "B" ? "rgba(255,255,255,.08)" : "rgba(255,255,255,.4)";
      ctx.fill();
    }
    if (number) {
      ctx.fillStyle = color === "B" ? "#f0eadb" : "#4b3d27";
      ctx.font = `600 ${Math.max(8, r * 0.86)}px "Cascadia Mono", "Microsoft YaHei UI", monospace`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(String(number), px, py + r * 0.04);
    }
    if (last) {
      ctx.strokeStyle = "rgba(184,76,61,.92)";
      ctx.lineWidth = Math.max(1.3, r * 0.095);
      ctx.beginPath();
      ctx.arc(px, py, r * 0.82, 0, Math.PI * 2);
      ctx.stroke();
      if (!number) {
        ctx.beginPath();
        ctx.arc(px, py, r * 0.15, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(184,76,61,.95)";
        ctx.fill();
      }
    }
    if (dead) {
      ctx.strokeStyle = "rgba(184,76,61,.95)";
      ctx.lineWidth = Math.max(1.8, r * 0.12);
      ctx.beginPath();
      ctx.moveTo(px - r * 0.55, py - r * 0.55);
      ctx.lineTo(px + r * 0.55, py + r * 0.55);
      ctx.moveTo(px + r * 0.55, py - r * 0.55);
      ctx.lineTo(px - r * 0.55, py + r * 0.55);
      ctx.stroke();
      ctx.globalAlpha = alpha * 0.34;
      ctx.beginPath();
      ctx.arc(px, py, r, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(184,76,61,.55)";
      ctx.fill();
    }
    ctx.restore();
  }

  function drawOwnership(ctx, geom, board, ownership) {
    const { x0, y0, cell, size } = geom;
    if (!ownership || ownership.length !== size * size) return;
    ctx.save();
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        if (board[y][x] !== 0) continue;
        const v = Number(ownership[y * size + x]) || 0;
        const a = Math.abs(v);
        if (a < 0.12) continue;
        const s = cell * 0.42;
        const px = x0 + (state.flip ? size - 1 - x : x) * cell;
        const py = y0 + (state.flip ? size - 1 - y : y) * cell;
        ctx.fillStyle = v > 0 ? `rgba(38,72,49,${0.1 + a * 0.42})` : `rgba(247,239,219,${0.12 + a * 0.46})`;
        roundRect(ctx, px - s / 2, py - s / 2, s, s, s * 0.24);
        ctx.fill();
      }
    }
    ctx.restore();
  }

  function drawScoringTerritory(ctx, geom, board, score) {
    const { x0, y0, cell, size } = geom;
    const terr = score.territory;
    if (!terr) return;
    ctx.save();
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        if (board[y][x] !== 0) continue;
        const v = terr[y] && terr[y][x];
        if (!v) continue;
        const s = cell * 0.34;
        const px = x0 + (state.flip ? size - 1 - x : x) * cell;
        const py = y0 + (state.flip ? size - 1 - y : y) * cell;
        ctx.fillStyle = v === 1 ? "rgba(36,69,47,.48)" : "rgba(250,244,228,.62)";
        roundRect(ctx, px - s / 2, py - s / 2, s, s, s * 0.3);
        ctx.fill();
      }
    }
    ctx.restore();
  }

  function drawHintMarkers(ctx, geom, board, candidates) {
    const { x0, y0, cell, size } = geom;
    ctx.save();
    candidates.forEach((c, i) => {
      if (!c || c.move === "pass") return;
      const pt = pointForVertex(c.move, geom);
      if (!pt || (board[pt.y] && board[pt.y][pt.x] !== 0)) return;
      const r = cell * 0.36;
      ctx.beginPath();
      ctx.arc(pt.px, pt.py, r, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(14,18,14,.82)";
      ctx.fill();
      ctx.strokeStyle = i === 0 ? "rgba(240,217,168,.95)" : "rgba(210,171,99,.55)";
      ctx.lineWidth = i === 0 ? 1.6 : 1;
      ctx.stroke();
      ctx.fillStyle = i === 0 ? "#f0d9a8" : "#d2ab63";
      ctx.font = `600 ${Math.max(8, cell * 0.3)}px "Cascadia Mono", monospace`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(String.fromCharCode(65 + i), pt.px, pt.py + 0.5);
      ctx.font = `500 ${Math.max(6.5, cell * 0.18)}px "Cascadia Mono", monospace`;
      ctx.fillStyle = "rgba(240,233,218,.75)";
      ctx.fillText(pct(c.winrate), pt.px, pt.py + r + cell * 0.18);
    });
    ctx.restore();
  }

  function drawPV(ctx, geom, board, candidate, toMove) {
    if (!candidate || !candidate.pv || !candidate.pv.length) return;
    const { cell } = geom;
    ctx.save();
    let color = toMove;
    const seen = new Set();
    candidate.pv.slice(0, 9).forEach((mv, i) => {
      if (!mv || mv === "pass") { color = color === "B" ? "W" : "B"; return; }
      const pt = pointForVertex(mv, geom);
      if (!pt || (board[pt.y] && board[pt.y][pt.x] !== 0) || seen.has(mv)) { color = color === "B" ? "W" : "B"; return; }
      seen.add(mv);
      ctx.globalAlpha = 0.46 - i * 0.035;
      ctx.beginPath();
      ctx.arc(pt.px, pt.py, cell * 0.28, 0, Math.PI * 2);
      ctx.fillStyle = color === "B" ? "rgba(20,24,20,.9)" : "rgba(255,250,237,.92)";
      ctx.fill();
      ctx.strokeStyle = "rgba(210,171,99,.75)";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = color === "B" ? "#f0d9a8" : "#5d4c30";
      ctx.font = `600 ${Math.max(7, cell * 0.21)}px "Cascadia Mono", monospace`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(String(i + 1), pt.px, pt.py);
      color = color === "B" ? "W" : "B";
    });
    ctx.restore();
  }

  function roundRect(ctx, x, y, w, h, r) {
    const rr = Math.min(r, Math.abs(w) / 2, Math.abs(h) / 2);
    ctx.beginPath();
    ctx.moveTo(x + rr, y);
    ctx.arcTo(x + w, y, x + w, y + h, rr);
    ctx.arcTo(x + w, y + h, x, y + h, rr);
    ctx.arcTo(x, y + h, x, y, rr);
    ctx.arcTo(x, y, x + w, y, rr);
    ctx.closePath();
  }

  function pointForVertex(vertex, geom) {
    if (!vertex || vertex === "pass") return null;
    const x = COLS.indexOf(vertex[0]);
    const num = Number(vertex.slice(1));
    if (x < 0 || !num) return null;
    const y = geom.size - num;
    const sx = state.flip ? geom.size - 1 - x : x;
    const sy = state.flip ? geom.size - 1 - y : y;
    return { x, y, px: geom.x0 + sx * geom.cell, py: geom.y0 + sy * geom.cell };
  }

  function vertexFromEvent(e) {
    if (!boardState.geom) return null;
    const rect = $("#board").getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const { x0, y0, cell, size } = boardState.geom;
    const ix = Math.round((px - x0) / cell);
    const iy = Math.round((py - y0) / cell);
    if (ix < 0 || iy < 0 || ix >= size || iy >= size) return null;
    const dx = px - (x0 + ix * cell);
    const dy = py - (y0 + iy * cell);
    if (Math.hypot(dx, dy) > cell * 0.52) return null;
    const rx = state.flip ? size - 1 - ix : ix;
    const ry = state.flip ? size - 1 - iy : iy;
    return `${COLS[rx]}${size - ry}`;
  }

  function lastStoneScale(vertex) {
    if (state.reduceMotion) return 1;
    if (!state.boardAnim || state.boardAnim.vertex !== vertex) return 1;
    const t = clamp((performance.now() - state.boardAnim.start) / 190, 0, 1);
    if (t >= 1) { state.boardAnim = null; return 1; }
    const e = 1 - Math.pow(1 - t, 3);
    return 0.55 + 0.45 * e;
  }

  function animateBoard() {
    if (boardState.raf) return;
    const step = () => {
      boardState.raf = 0;
      drawBoard();
      if (state.boardAnim) boardState.raf = requestAnimationFrame(step);
    };
    boardState.raf = requestAnimationFrame(step);
  }

  function onBoardMove(e) {
    const v = vertexFromEvent(e);
    state.hover = v;
    const tip = $("#coords-tip");
    tip.textContent = v ? `${v}` : "";
    if (v !== state.coords) {
      state.coords = v;
      drawBoard();
    }
  }

  function onBoardClick(e) {
    if (!state.game) return;
    ensureAudio();
    const v = vertexFromEvent(e);
    if (!v) return;
    if (state.view === "review") { toast("复盘模式中，点击右侧棋谱切换手数"); return; }
    if (state.game.phase === "scoring") {
      if (state.game.scoring && state.game.scoring.estimating) { toast("AI 正在判断死活"); return; }
      toggleDead(v);
      return;
    }
    if (state.game.phase !== "playing") return;
    if (state.game.ai_thinking) { toast("AI 正在思考，请稍候"); return; }
    if (state.game.to_move !== state.game.player_color) { toast("还没有轮到你"); return; }
    const idx = COLS.indexOf(v[0]);
    const num = Number(v.slice(1));
    const y = state.game.size - num;
    if (state.game.board[y] && state.game.board[y][idx] !== 0) { toast("这里已经有棋子了"); return; }
    playMove(v);
  }

  // ==================== 落子操作 ====================
  async function playMove(vertex) {
    if (!state.game || state.busy) return;
    state.busy = true;
    renderChrome();
    try {
      const game = await api(`/api/game/${state.game.id}/move`, { body: { vertex } });
      state.extraPolls = 2;
      applyGame(game);
      schedulePoll(500);
    } catch (err) {
      toast(err.message || "落子失败", "error", 3500);
    } finally {
      state.busy = false;
      renderChrome();
    }
  }

  async function requestPass() {
    if (!state.game || !playerTurn() || state.busy) {
      if (state.game && state.game.phase === "scoring") return;
      toast("现在还不能停一手");
      return;
    }
    if (state.game.turn >= 1) {
      const ok = await confirmDialog({
        kicker: "停一手",
        title: "确认停一手？",
        message: "停一手表示你放弃本回合落子。若 AI 也停一手，将进入数子。",
        confirmText: "停一手",
      });
      if (!ok) return;
    }
    try {
      state.busy = true;
      const game = await api(`/api/game/${state.game.id}/pass`, { body: {} });
      state.extraPolls = 1;
      applyGame(game);
      schedulePoll(500);
    } catch (err) {
      toast(err.message || "停一手失败", "error");
    } finally {
      state.busy = false;
      renderChrome();
    }
  }

  async function retryAi() {
    if (!state.game || state.busy) return;
    try {
      state.busy = true;
      const game = await api(`/api/game/${state.game.id}/retry-ai`, { body: {} });
      applyGame(game);
      schedulePoll(700);
    } catch (err) {
      toast(err.message || "AI 重试失败", "error", 4000);
    } finally {
      state.busy = false;
      renderChrome();
    }
  }

  async function undoMove() {
    if (!state.game || state.busy) return;
    if (!state.game.can_undo) { toast("当前没有可悔的棋"); return; }
    try {
      state.busy = true;
      const game = await api(`/api/game/${state.game.id}/undo`, { body: {} });
      state.extraPolls = 0;
      state.resultKey = null;
      applyGame(game);
      toast("已悔棋，重新思考");
      schedulePoll(1000);
    } catch (err) {
      toast(err.message || "悔棋失败", "error");
    } finally {
      state.busy = false;
      renderChrome();
    }
  }

  async function redoMove() {
    if (!state.game || state.busy) return;
    if (!state.game.can_redo) { toast("当前没有可重做的棋"); return; }
    try {
      state.busy = true;
      const game = await api(`/api/game/${state.game.id}/redo`, { body: {} });
      applyGame(game);
      toast("已重做");
      schedulePoll(800);
    } catch (err) {
      toast(err.message || "重做失败", "error");
    } finally {
      state.busy = false;
      renderChrome();
    }
  }

  async function getHint() {
    if (!state.game || !playerTurn()) {
      if (state.game && state.game.ai_thinking) toast("AI 正在思考，请稍候");
      else toast("轮到你落子时才能请求提示");
      return;
    }
    const blocked = engineBlockedMessage();
    if (blocked) { toast(blocked.text, blocked.error ? "error" : "info", 3800); return; }
    const btn = $("#hint-btn");
    btn.disabled = true;
    const old = btn.innerHTML;
    btn.innerHTML = `<span class="ink-loader"><i></i><i></i><i></i></span> 研算中`;
    try {
      const hint = await api(`/api/game/${state.game.id}/hint`, { body: {}, timeout: 180000 });
      state.game.hint = hint;
      state.game.eval = hint.eval || state.game.eval;
      state.hintVisible = true;
      state.selectedCandidate = null;
      state.ownershipVisible = false;
      setTab("analysis");
      renderAll();
      const move = hint.recommended || (hint.candidates[0] && hint.candidates[0].move);
      toast(move === "pass" ? "AI 建议停一手" : `AI 推荐 ${move}`);
    } catch (err) {
      toast(err.message || "提示失败", "error", 4000);
    } finally {
      btn.innerHTML = old;
      renderChrome();
    }
  }

  async function getEstimate() {
    if (!state.game) return;
    if (state.ownershipVisible) {
      state.ownershipVisible = false;
      renderAll();
      toast("已关闭形势图");
      return;
    }
    const blocked = engineBlockedMessage();
    if (blocked) { toast(blocked.text, blocked.error ? "error" : "info", 3800); return; }
    const btn = $("#estimate-btn");
    btn.disabled = true;
    const old = btn.innerHTML;
    btn.innerHTML = `<span class="ink-loader"><i></i><i></i><i></i></span> 判断中`;
    try {
      const est = await api(`/api/game/${state.game.id}/estimate`, { body: {}, timeout: 120000 });
      state.game.ownership = est.ownership || [];
      state.game.eval = est.eval || state.game.eval;
      state.ownershipVisible = true;
      state.hintVisible = false;
      renderAll();
      const lead = Number(est.score_lead_black) || 0;
      toast(Math.abs(lead) < 0.05 ? "形势：盘面均衡" : (lead > 0 ? `形势：黑棋领先约 ${lead.toFixed(1)} 目` : `形势：白棋领先约 ${Math.abs(lead).toFixed(1)} 目`));
    } catch (err) {
      toast(err.message || "形势判断失败", "error", 3500);
    } finally {
      btn.innerHTML = old;
      renderChrome();
    }
  }

  async function toggleDead(vertex) {
    if (!state.game || state.busy) return;
    try {
      state.busy = true;
      const game = await api(`/api/game/${state.game.id}/score/toggle`, { body: { vertex } });
      applyGame(game);
    } catch (err) {
      toast(err.message || "标记失败", "error");
    } finally {
      state.busy = false;
      renderChrome();
    }
  }

  async function confirmScore() {
    if (!state.game) return;
    try {
      state.busy = true;
      const game = await api(`/api/game/${state.game.id}/score/confirm`, { body: {} });
      applyGame(game);
      state.resultKey = null;
      maybeShowResult();
    } catch (err) {
      toast(err.message || "确认数子失败", "error");
    } finally {
      state.busy = false;
      renderChrome();
    }
  }

  async function resumePlay() {
    if (!state.game) return;
    try {
      state.busy = true;
      const game = await api(`/api/game/${state.game.id}/score/resume`, { body: {} });
      state.extraPolls = 2;
      applyGame(game);
      schedulePoll(600);
    } catch (err) {
      toast(err.message || "继续对局失败", "error");
    } finally {
      state.busy = false;
      renderChrome();
    }
  }

  async function reestimateScore() {
    if (!state.game) return;
    try {
      state.busy = true;
      const game = await api(`/api/game/${state.game.id}/score/reestimate`, { body: {} });
      applyGame(game);
      schedulePoll(700);
    } catch (err) {
      toast(err.message || "重新数子失败", "error");
    } finally {
      state.busy = false;
      renderChrome();
    }
  }

  async function requestResign() {
    if (!state.game || state.game.phase !== "playing" || state.busy) return;
    const ok = await confirmDialog({
      kicker: "认输",
      title: "确认认输？",
      message: "本局将判定为你中盘负。结束后可以立即进入 AI 复盘。",
      confirmText: "认输",
      danger: true,
    });
    if (!ok) return;
    try {
      state.busy = true;
      const game = await api(`/api/game/${state.game.id}/resign`, { body: {} });
      applyGame(game);
      state.resultKey = null;
      maybeShowResult();
    } catch (err) {
      toast(err.message || "认输失败", "error");
    } finally {
      state.busy = false;
      renderChrome();
    }
  }

  async function downloadSgf() {
    if (!state.game) return;
    const btn = $("#sgf-btn");
    if (btn.disabled) return;
    const old = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="ink-loader"><i></i><i></i><i></i></span> 导出中`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const res = await fetch(`/api/game/${state.game.id}/sgf`, { signal: controller.signal, cache: "no-store" });
      if (!res.ok) {
        let message = `SGF 导出失败（${res.status}）`;
        try {
          const data = await res.json();
          if (data && data.error) message = data.error;
        } catch (e) { /* 非 JSON 错误页，保留状态码提示 */ }
        throw new Error(message);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `yixin-${state.game.id}.sgf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      toast("SGF 棋谱已开始下载");
    } catch (err) {
      if (err && err.name === "AbortError") toast("SGF 导出超时，本地服务没有响应", "error", 4500);
      else toast(err.message || "SGF 导出失败，请稍后重试", "error", 4500);
    } finally {
      clearTimeout(timer);
      btn.innerHTML = old;
      renderChrome();
    }
  }

  // ==================== 复盘 ====================
  async function downloadReviewReport() {
    if (!state.game) return;
    if (!state.reviewPayload || state.reviewPayload.status !== "done") {
      toast("复盘完成后才能导出报告");
      return;
    }
    try {
      const res = await fetch(`/api/game/${state.game.id}/review/report`);
      if (!res.ok) {
        let message = "导出报告失败";
        try { const data = await res.json(); message = data.error || message; } catch (e) { /* ignore */ }
        throw new Error(message);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `yixin-review-${state.game.id}.md`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      toast("复盘报告已开始下载");
    } catch (err) {
      toast(err.message || "导出复盘报告失败", "error", 4000);
    }
  }

  async function startReview() {
    if (!state.game) return;
    if (state.game.phase !== "ended") { toast("请先结束当前对局"); return; }
    if (state.game.review && state.game.review.status === "running") {
      setTab("review"); pollReview(); return;
    }
    try {
      closeModal();
      state.reviewPayload = null;
      state.reviewFetching = false;
      const game = await api(`/api/game/${state.game.id}/review`, { body: {} });
      state.game = game;
      setTab("review");
      renderAll();
      pollReview();
      toast("AI 复盘已开始");
    } catch (err) {
      toast(err.message || "复盘启动失败", "error", 4000);
    }
  }

  async function pollReview() {
    if (!state.game) return;
    clearTimeout(state.reviewPoll);
    try {
      const data = await api(`/api/game/${state.game.id}/review`);
      if (data.status === "running") {
        state.game.review = { ...(state.game.review || {}), ...data };
        renderReviewPanel();
        state.reviewPoll = setTimeout(pollReview, 900);
      } else if (data.status === "done") {
        state.reviewPayload = data;
        state.game.review = { ...(state.game.review || {}), status: "done", summary: data.summary };
        state.reviewIndex = (data.items && data.items.length) ? data.items.length - 1 : 0;
        if (data.items && data.items.length) {
          const last = data.items[data.items.length - 1];
          state.game.eval = {
            winrate_black: last.winrate_black,
            score_lead_black: last.score_lead_black,
            visits: data.visits || 0,
            best_move: last.best_move,
          };
        }
        state.view = "review";
        state.tab = "review";
        renderAll();
        setTab("review");
        toast(`复盘完成 · ${data.items ? data.items.length : 0} 手`);
      } else if (data.status === "error") {
        state.game.review = { ...(state.game.review || {}), ...data };
        renderReviewPanel();
        toast(`复盘失败：${data.error || "未知错误"}`, "error", 5000);
      }
    } catch (err) {
      toast(err.message || "复盘轮询失败", "error");
    }
    syncLang();
  }

  async function ensureReviewPayload() {
    if (state.reviewPayload || state.reviewFetching || !state.game) return;
    if (!state.game.review || state.game.review.status !== "done") return;
    state.reviewFetching = true;
    try {
      const data = await api(`/api/game/${state.game.id}/review`);
      if (data.status === "done") {
        state.reviewPayload = data;
        if (state.reviewIndex === null) state.reviewIndex = (data.items || []).length - 1;
        state.view = "review";
        renderAll();
      }
    } catch (e) { /* ignore */ }
    finally { state.reviewFetching = false; }
  }

  function renderReviewPanel() {
    const g = state.game;
    if (!g) return;
    const empty = $("#review-empty");
    const body = $("#review-body");
    const running = $("#review-running");
    const status = g.review && g.review.status;
    if (!g.review || status === "none") {
      empty.classList.remove("hidden");
      body.classList.add("hidden");
      running.classList.add("hidden");
      const btn = $("#start-review-inline");
      btn.disabled = g.phase !== "ended" || g.turn < 1;
      btn.textContent = g.phase === "ended" ? (g.turn > 0 ? "开始复盘" : "没有落子") : "终局后可复盘";
      syncLang();
      return;
    }
    if (status === "running") {
      empty.classList.add("hidden");
      body.classList.add("hidden");
      running.classList.remove("hidden");
      const progress = g.review.progress || 0;
      const total = g.review.total || (g.turn + 1);
      $("#review-running-text").textContent = `${progress} / ${total}`;
      $("#review-progress-bar").style.width = `${total ? (progress / total) * 100 : 0}%`;
      syncLang();
      return;
    }
    if (status === "error") {
      empty.classList.remove("hidden");
      body.classList.add("hidden");
      running.classList.add("hidden");
      const btn = $("#start-review-inline");
      btn.disabled = false;
      btn.textContent = "重新复盘";
      empty.querySelector("span").textContent = g.review.error || "复盘中断";
      syncLang();
      return;
    }
    if (status === "done") {
      empty.classList.add("hidden");
      running.classList.add("hidden");
      if (!state.reviewPayload) {
        body.classList.add("hidden");
        ensureReviewPayload();
        syncLang();
        return;
      }
      body.classList.remove("hidden");
      renderReviewBody(state.reviewPayload);
    }
    syncLang();
  }

  function renderReviewBody(data) {
    const summary = data.summary || {};
    const items = data.items || [];
    const mistakes = (summary.counts && (summary.counts["失误"] || 0) + (summary.counts["严重失误"] || 0)) || 0;
    $("#review-summary").innerHTML = `
      <div class="summary-cell"><span>平均胜率损失</span><b class="${(summary.avg_loss || 0) > 0.05 ? "gold" : "green"}">${((summary.avg_loss || 0) * 100).toFixed(1)}%</b></div>
      <div class="summary-cell"><span>前三选吻合</span><b class="green">${((summary.top3_rate || 0) * 100).toFixed(0)}%</b></div>
      <div class="summary-cell"><span>好棋</span><b>${(summary.counts && summary.counts["好棋"]) || 0}</b></div>
      <div class="summary-cell"><span>失误 / 严重</span><b class="${mistakes ? "gold" : ""}">${mistakes}</b></div>`;
    $("#review-progress").textContent = `${items.length} 手 · ${data.visits || 0} visits/手`;
    $("#review-list").innerHTML = items.map((item, i) => `
      <button class="review-item ${i === state.reviewIndex ? "active" : ""}" data-review-index="${i}">
        <span class="rv-no">${item.move_no}</span>
        <span class="rv-stone ${item.color}"></span>
        <span class="rv-move">${esc(item.move === "pass" ? "停一手" : item.move)}</span>
        <span class="badge-q ${esc(item.tone)}">${esc(item.quality)}</span>
        <span class="rv-loss">${(item.loss * 100).toFixed(1)}%</span>
      </button>`).join("");
    const item = items[state.reviewIndex];
    const detail = $("#review-detail");
    if (item) {
      detail.classList.remove("hidden");
      detail.innerHTML = `<h4>第 ${item.move_no} 手 · ${item.color === "B" ? "黑" : "白"} ${esc(item.move === "pass" ? "停一手" : item.move)} · ${esc(item.quality)}</h4>
        <p>黑棋胜率 ${pct(item.winrate_black)} · 胜率损失 ${(item.loss * 100).toFixed(1)}% · AI 首选 <b>${esc(item.best_move === "pass" ? "停一手" : item.best_move)}</b></p>
        <p class="pv">变化：${esc((item.pv || []).join(" → ") || "—")}</p>`;
    } else {
      detail.classList.add("hidden");
    }
    requestAnimationFrame(drawReviewChart);
    syncLang();
  }

  function selectReview(index) {
    if (!state.reviewPayload || !state.reviewPayload.items) return;
    state.reviewIndex = clamp(index, 0, state.reviewPayload.items.length - 1);
    state.view = "review";
    state.tab = "review";
    const item = state.reviewPayload.items[state.reviewIndex];
    if (item) {
      state.game.eval = {
        winrate_black: item.winrate_black,
        score_lead_black: item.score_lead_black,
        visits: state.reviewPayload.visits || 0,
        best_move: item.best_move,
      };
    }
    renderReviewPanel();
    renderChrome();
    renderWinrate();
    drawBoard();
  }

  function exitReview() {
    state.view = "play";
    setTab("analysis");
    renderAll();
  }

  function drawReviewChart() {
    const canvas = $("#review-chart");
    if (!canvas || !state.reviewPayload) return;
    const items = state.reviewPayload.items || [];
    if (!items.length) return;
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = Math.round(rect.height * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const w = rect.width;
    const h = rect.height;
    const pad = { l: 21, r: 7, t: 9, b: 14 };
    const cw = w - pad.l - pad.r;
    const ch = h - pad.t - pad.b;
    const n = items.length;
    const X = (i) => pad.l + (n <= 1 ? cw / 2 : (i / (n - 1)) * cw);
    const Y = (v) => pad.t + (1 - clamp(v, 0, 1)) * ch;
    state.chart = { X, Y, pad, cw, ch, n, w, h };

    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = "rgba(236,231,218,.07)";
    ctx.lineWidth = 1;
    for (const v of [0, 0.5, 1]) {
      ctx.beginPath();
      ctx.moveTo(pad.l, Y(v));
      ctx.lineTo(w - pad.r, Y(v));
      ctx.stroke();
    }
    ctx.fillStyle = "rgba(139,145,135,.6)";
    ctx.font = '7px "Cascadia Mono", monospace';
    ctx.textAlign = "right";
    ctx.fillText("100", pad.l - 4, Y(1) + 2);
    ctx.fillText("50", pad.l - 4, Y(0.5) + 2);
    ctx.fillText("0", pad.l - 4, Y(0) + 2);

    const grad = ctx.createLinearGradient(0, pad.t, 0, pad.t + ch);
    grad.addColorStop(0, "rgba(210,171,99,.22)");
    grad.addColorStop(1, "rgba(210,171,99,0)");
    ctx.beginPath();
    items.forEach((item, i) => {
      const x = X(i);
      const y = Y(item.winrate_black);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.lineTo(X(n - 1), pad.t + ch);
    ctx.lineTo(X(0), pad.t + ch);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.beginPath();
    items.forEach((item, i) => {
      const x = X(i);
      const y = Y(item.winrate_black);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = "rgba(210,171,99,.8)";
    ctx.lineWidth = 1.4;
    ctx.stroke();

    items.forEach((item, i) => {
      const x = X(i);
      const y = Y(item.winrate_black);
      const color = item.tone === "bad" ? "#c96859" : item.tone === "warn" ? "#d2ab63" : item.tone === "good" ? "#6f9c7f" : "rgba(232,224,204,.55)";
      ctx.beginPath();
      ctx.arc(x, y, i === state.reviewIndex ? 3 : 1.7, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      if (i === state.reviewIndex) {
        ctx.strokeStyle = "rgba(240,217,168,.9)";
        ctx.lineWidth = 1;
        ctx.stroke();
      }
    });
    if (state.chartHover !== null && state.chartHover !== undefined && items[state.chartHover]) {
      const item = items[state.chartHover];
      const x = X(state.chartHover);
      ctx.strokeStyle = "rgba(240,217,168,.25)";
      ctx.beginPath();
      ctx.moveTo(x, pad.t);
      ctx.lineTo(x, pad.t + ch);
      ctx.stroke();
    }
  }

  function onChartMove(e) {
    if (!state.chart || !state.reviewPayload) return;
    const rect = $("#review-chart").getBoundingClientRect();
    const x = e.clientX - rect.left;
    const { pad, cw, n } = state.chart;
    if (x < pad.l - 4 || x > pad.l + cw + 4) {
      $("#chart-tip").classList.add("hidden");
      return;
    }
    const idx = n <= 1 ? 0 : Math.round(((x - pad.l) / cw) * (n - 1));
    const item = state.reviewPayload.items[idx];
    if (!item) return;
    state.chartHover = idx;
    drawReviewChart();
    const tip = $("#chart-tip");
    tip.classList.remove("hidden");
    tip.style.left = `${clamp(e.clientX - rect.left, 34, rect.width - 34)}px`;
    tip.style.top = `${clamp(e.clientY - rect.top, 24, rect.height - 4)}px`;
    tip.textContent = `第${item.move_no}手 ${item.color}${item.move} · 黑胜 ${pct(item.winrate_black)} · ${item.quality}`;
  }

  function onChartClick(e) {
    if (!state.chart || !state.reviewPayload) return;
    const rect = $("#review-chart").getBoundingClientRect();
    const x = e.clientX - rect.left;
    const { pad, cw, n } = state.chart;
    if (n <= 0) return;
    const idx = n <= 1 ? 0 : Math.round(clamp((x - pad.l) / cw, 0, 1) * (n - 1));
    selectReview(idx);
  }

  // ==================== 弹窗 ====================
  function showModal(html, { wide = false, closable = true } = {}) {
    closeModal();
    state.modalOpen = true;
    const root = $("#modal-root");
    root.innerHTML = `<div class="modal-overlay"><div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true">${html}</div></div>`;
    if (state.lang === "en") localizeTree(root, true);
    const overlay = root.firstElementChild;
    overlay.addEventListener("click", (e) => {
      if (closable && e.target === overlay) closeModal();
    });
    return root.querySelector(".modal");
  }

  function closeModal() {
    const resolve = state.modalResolve;
    state.modalResolve = null;
    state.modalOpen = false;
    state.helpOpen = false;
    $("#modal-root").innerHTML = "";
    if (resolve) resolve(false);
  }

  function confirmDialog({ kicker = "确认", title, message, confirmText = "确认", danger = false }) {
    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.className = "modal-overlay confirm-overlay";
      overlay.innerHTML = `<div class="modal"><div class="modal-kicker">${esc(kicker)}</div>
        <h3>${esc(title)}</h3><p>${esc(message)}</p>
        <div class="modal-actions"><button class="ghost-btn" data-cancel>取消</button>
        <button class="primary-btn small" data-confirm>${esc(confirmText)}</button></div></div>`;
      document.body.appendChild(overlay);
      let done = false;
      const finish = (value) => {
        if (done) return;
        done = true;
        state.confirmOpen = false;
        state.confirmClose = null;
        overlay.remove();
        resolve(value);
      };
      state.confirmOpen = true;
      state.confirmClose = () => finish(false);
      overlay.querySelector("[data-cancel]").onclick = () => finish(false);
      overlay.querySelector("[data-confirm]").onclick = () => finish(true);
      overlay.addEventListener("click", (e) => { if (e.target === overlay) finish(false); });
    });
  }

  function showResultModal(g) {
    const r = g.result || {};
    const winnerChar = r.winner === "B" ? "黑" : r.winner === "W" ? "白" : "和";
    const sealChar = state.lang === "en" ? (r.winner === "B" ? "B" : r.winner === "W" ? "W" : "D") : winnerChar;
    const scoreText = r.score ? `${r.score.black_score} : ${r.score.white_score}` : (r.margin != null ? `${r.margin} 目` : "中盘");
    const modal = showModal(`<div class="modal-kicker">对局结束</div>
      <div class="result-hero">
        <div class="result-seal" data-i18n-skip>${sealChar}</div>
        <div><h3>${esc(r.text || "对局结束")}</h3><p>${esc(g.level ? g.level.name : "")} · ${g.size} 路 · ${g.rules === "chinese" ? "中国规则" : "日本规则"}</p></div>
      </div>
      <div class="result-stats">
        <div class="result-stat"><span>总手数</span><b>${g.turn}</b></div>
        <div class="result-stat"><span>比分</span><b>${esc(scoreText)}</b></div>
        <div class="result-stat"><span>提子</span><b>${(g.captures.B || 0) + (g.captures.W || 0)}</b></div>
      </div>
      <p>AI 会逐手分析胜率变化、失误与推荐变化。复盘的思考量会根据棋局长度自动配置。</p>
      <div class="modal-actions">
        <button class="ghost-btn" data-new>再来一局</button>
        <button class="primary-btn small" data-review>复盘本局</button>
      </div>`);
    $("[data-new]", modal).onclick = () => { closeModal(); createGame({ noConfirm: true }); };
    $("[data-review]", modal).onclick = () => { closeModal(); startReview(); };
  }

  function showHelp() {
    state.helpOpen = true;
    const modal = showModal(`<div class="modal-kicker">帮助</div>
      <h3>操作与快捷键</h3>
      <p class="help-lead">轮到你落子时，点击棋盘交叉点即可落子；鼠标悬停预览落点与坐标，触屏直接点交叉点。</p>
      <div class="help-keys" aria-label="键盘快捷键">
        <div class="help-key-row"><kbd>H</kbd><span><b>AI 提示</b>候选点 A/B/C、胜率、目差与变化图</span></div>
        <div class="help-key-row"><kbd>U</kbd><span><b>悔棋</b>撤回你和 AI 各一手</span></div>
        <div class="help-key-row"><kbd>Y</kbd><span><b>重做</b>恢复悔棋前的局面</span></div>
        <div class="help-key-row"><kbd>P</kbd><span><b>停一手</b>放弃本回合；双方停一手后进入数子</span></div>
        <div class="help-key-row"><kbd>E</kbd><span><b>形势</b>显示地盘归属与目差预测</span></div>
        <div class="help-key-row"><kbd>N</kbd><span><b>新对局</b>按当前设定重新开始</span></div>
        <div class="help-key-row"><kbd>R</kbd><span><b>复盘</b>终局后开始复盘；复盘时返回对局</span></div>
        <div class="help-key-row"><kbd>←</kbd><kbd>→</kbd><span><b>逐手</b>复盘时跳到上一手 / 下一手</span></div>
        <div class="help-key-row"><kbd>?</kbd><span><b>帮助</b>打开或关闭这个面板</span></div>
        <div class="help-key-row"><kbd>Esc</kbd><span><b>关闭</b>关掉当前弹窗</span></div>
      </div>
      <div class="help-grid">
        <div class="help-item"><b>数子</b><span>AI 先标出死子，点击棋子可增删，点「确认结果」结束对局。</span></div>
        <div class="help-item"><b>形势</b><span>点「形势」显示地盘归属与目差预测，再点一次关闭。</span></div>
        <div class="help-item"><b>复盘</b><span>终局后点「复盘」；点曲线或右侧逐手列表可跳到那一手。</span></div>
        <div class="help-item"><b>棋盘</b><span>右上角数字按钮显示手数；左侧可切换 9/13/19 路与规则。</span></div>
        <div class="help-item"><b>用时</b><span>对局设定可选 5–60 分钟主时间，并可选 30/60 秒 × 3 次读秒；时间用尽即超时判负。</span></div>
      </div>
      <p class="help-note">输入框聚焦时快捷键自动让位，不会误触发。所有计算都在本机 KataGo 完成。<span class="help-version">v${esc(state.boot && state.boot.version || "")}</span></p>
      <div class="modal-actions">
        <button class="ghost-btn" data-diagnostics>运行诊断</button>
        <button class="ghost-btn" data-quit>退出程序</button>
        <button class="primary-btn small" data-ok>知道了</button>
      </div>`, { wide: true });
    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "modal-x";
    closeBtn.setAttribute("aria-label", "关闭帮助");
    closeBtn.textContent = "×";
    closeBtn.addEventListener("click", closeModal);
    modal.appendChild(closeBtn);
    const okBtn = $("[data-ok]", modal);
    okBtn.onclick = () => closeModal();
    okBtn.focus();
    $("[data-diagnostics]", modal).onclick = showDiagnostics;
    $("[data-quit]", modal).onclick = async () => {
      if (!await confirmDialog({
        kicker: "退出",
        title: "退出弈心？",
        message: "退出后 AI 引擎和对局服务都会停止。已保存的棋谱仍保留在 data/games 中。",
        confirmText: "退出程序",
        danger: true,
      })) return;
      try { await api("/api/quit", { body: {} }); } catch (e) { /* server may close first */ }
      document.body.innerHTML = `<div style="height:100vh;display:grid;place-items:center;color:#8b9187;font-family:Microsoft YaHei UI,sans-serif;font-size:13px;letter-spacing:2px">弈心已退出，可以关闭此窗口</div>`;
    };
  }

  async function showDiagnostics() {
    const modal = showModal(`<div class="modal-kicker">本地自检</div><h3>运行诊断</h3>
      <div class="diag-list" id="diag-list"><div class="records-empty">正在检查引擎、模型与目录…</div></div>
      <div class="diag-meta" id="diag-meta"></div>
      <div class="modal-actions">
        <button class="ghost-btn" data-copy>复制诊断信息</button>
        <button class="primary-btn small" data-ok>关闭</button>
      </div>`, { wide: true });
    $("[data-ok]", modal).onclick = closeModal;
    let payload = null;
    try {
      payload = await api("/api/diagnostics", { timeout: 20000 });
      const icons = { ok: "✓", warn: "!", fail: "×" };
      $("#diag-list").innerHTML = (payload.checks || []).map((check) => `<div class="diag-row ${esc(check.level)}">
        <span class="diag-icon">${icons[check.level] || "·"}</span>
        <span class="diag-main"><b>${esc(check.label)}</b><span>${esc(check.detail)}</span></span>
      </div>`).join("") || `<div class="records-empty">没有诊断项目</div>`;
      $("#diag-meta").innerHTML = `KataGo ${esc(payload.engine && payload.engine.version || "—")} · ${esc(payload.engine && payload.engine.backend || "—")} · Python ${esc(payload.python || "")} · ${esc(payload.base || "")}`;
      syncLang();
    } catch (err) {
      $("#diag-list").innerHTML = `<div class="records-empty" style="color:var(--red)">${esc(err.message)}</div>`;
    }
    $("[data-copy]", modal).onclick = async () => {
      if (!payload) return;
      try {
        await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
        toast("诊断信息已复制");
      } catch (err) {
        toast("复制失败，请手动截图", "error");
      }
    };
  }

  async function showRecords() {
    const modal = showModal(`<div class="modal-kicker">本地棋谱</div><h3>对局记录</h3>
      <div class="records-toolbar"><span>棋谱只保存在本机 data/games</span>
        <button class="record-load" id="import-sgf-btn" type="button">导入 SGF</button>
        <input id="sgf-file-input" type="file" accept=".sgf,.sgfs,application/x-go-sgf,text/plain" style="display:none">
      </div>
      <div class="records-stats" id="records-stats"></div>
      <div class="record-tools">
        <input id="record-search" type="search" autocomplete="off" placeholder="搜索棋谱：段位 / 结果 / 日期">
        <select id="record-sort" aria-label="棋谱排序">
          <option value="newest">最新优先</option>
          <option value="oldest">最早优先</option>
          <option value="moves">手数最多</option>
          <option value="result">按结果</option>
        </select>
      </div>
      <div class="record-filters" id="record-filters">
        <button data-filter="all" class="active" type="button">全部</button>
        <button data-filter="B" type="button">黑胜</button>
        <button data-filter="W" type="button">白胜</button>
        <button data-filter="9" type="button">9 路</button>
        <button data-filter="13" type="button">13 路</button>
        <button data-filter="19" type="button">19 路</button>
      </div>
      <div class="record-batch">
        <label><input id="select-all-records" type="checkbox"> 全选当前</label>
        <span id="selected-count"></span>
        <button id="delete-selected-records" class="record-del" type="button" disabled>删除选中</button>
      </div>
      <div id="active-records-wrap">
        <div class="record-section-title"><span>未完成对局</span><span id="active-record-count"></span></div>
        <div class="records-list" id="active-records"><div class="records-empty">正在读取…</div></div>
      </div>
      <div class="record-section-title"><span>已归档棋谱</span><span id="record-count"></span></div>
      <div class="records-list" id="records-list"><div class="records-empty">正在读取…</div></div>
      <div class="modal-actions"><button class="ghost-btn" data-close>关闭</button></div>`, { wide: true });
    $("[data-close]", modal).onclick = closeModal;

    const fileInput = $("#sgf-file-input");
    $("#import-sgf-btn").onclick = () => fileInput.click();
    fileInput.onchange = async () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file) return;
      try {
        if (file.size > 900000) { toast("SGF 文件过大（上限约 900KB）", "error"); return; }
        const sgf = await file.text();
        if (!sgf.trim()) { toast("SGF 文件是空的", "error"); return; }
        if (state.game && state.game.turn > 0 && state.game.phase !== "ended") {
          const ok = await confirmDialog({
            kicker: "导入棋谱",
            title: "替换当前对局？",
            message: "导入 SGF 会关闭当前未完成的对局。当前棋局不会自动保存。",
            confirmText: "导入",
          });
          if (!ok) return;
        }
        const game = await api("/api/import", { body: { sgf }, timeout: 30000 });
        closeModal();
        state.reviewPayload = null;
        state.reviewIndex = null;
        applyGame(game, { initial: true });
        setTab("review");
        renderAll();
        toast(`已导入 ${game.turn} 手，可开始复盘`);
      } catch (err) {
        toast(err.message || "SGF 导入失败", "error", 5000);
      } finally {
        fileInput.value = "";
      }
    };

    try {
      const [data, stats] = await Promise.all([
        api("/api/games"),
        api("/api/stats").catch(() => null),
      ]);
      const active = data.active_games || [];
      const games = data.games || [];
      const renderStats = (st) => {
        if (st && st.total) {
          $("#records-stats").innerHTML = `<span>已归档 <b>${st.total}</b> 局</span>
            <span>黑胜 <b>${st.black_wins || 0}</b></span>
            <span>白胜 <b>${st.white_wins || 0}</b></span>
            <span>和棋 <b>${st.draws || 0}</b></span>
            <span>平均 <b>${st.avg_moves || 0}</b> 手</span>`;
        } else {
          $("#records-stats").innerHTML = `<span>暂无归档统计</span>`;
        }
        const levels = Object.entries((st && st.by_level) || {}).sort((a, b) => b[1] - a[1]);
        if (levels.length) {
          const extra = document.createElement("span");
          extra.innerHTML = `最常下 <b>${esc(levels[0][0])}</b> ${levels[0][1]} 局`;
          $("#records-stats").appendChild(extra);
        }
      };
      renderStats(stats);
      const activeWrap = $("#active-records-wrap");
      const activeList = $("#active-records");
      $("#active-record-count").textContent = active.length ? `${active.length} 局` : "";
      if (!active.length) {
        activeWrap.classList.add("hidden");
      } else {
        activeList.innerHTML = active.map((rec) => `<div class="record-row active">
          <div class="record-main"><b>${esc(rec.level || "对局")} · ${rec.size || 19} 路</b>
            <span>${rec.moves || 0} 手 · ${rec.phase === "scoring" ? "等待数子" : "进行中"}</span></div>
          <button class="record-load" data-resume="${esc(rec.id)}" type="button">继续</button>
          <button class="record-del" data-abandon="${esc(rec.id)}" type="button" title="放弃这局">放弃</button>
        </div>`).join("");
      }
      const list = $("#records-list");
      const searchInput = $("#record-search");
      const sortSelect = $("#record-sort");
      const selectAll = $("#select-all-records");
      const selectedCount = $("#selected-count");
      const deleteSelected = $("#delete-selected-records");
      const selected = new Set();
      let visibleFiles = [];
      let recordFilter = "all";
      const updateBatchBar = () => {
        const visibleSelected = visibleFiles.filter((f) => selected.has(f)).length;
        selectAll.checked = visibleFiles.length > 0 && visibleSelected === visibleFiles.length;
        selectAll.indeterminate = visibleSelected > 0 && visibleSelected < visibleFiles.length;
        selectedCount.textContent = selected.size ? `已选 ${selected.size} 局` : "";
        deleteSelected.disabled = selected.size === 0;
      };
      const showRecordDetail = (rec) => {
        if (!rec) return;
        const overlay = document.createElement("div");
        overlay.className = "modal-overlay detail-overlay";
        const created = (rec.created_at || "").replace("T", " ");
        const rules = rec.rules === "japanese" ? "日本规则" : "中国规则";
        const komi = rec.komi == null ? "—" : `${Number(rec.komi).toFixed(1)} 目`;
        const handicap = Number(rec.handicap) > 0 ? `${rec.handicap} 子` : "无";
        const time = Number(rec.time_limit) > 0 ? `${Math.round(Number(rec.time_limit) / 60)} 分钟` : "不限时";
        const byo = Number(rec.byo_yomi) > 0
          ? (state.lang === "en" ? `, Byo ${rec.byo_yomi}s ×3` : `，读秒 ${rec.byo_yomi} 秒 ×3`)
          : "";
        const color = rec.player_color === "W" ? "执白" : "执黑";
        overlay.innerHTML = `<div class="modal detail-modal" role="dialog" aria-modal="true">
          <div class="modal-kicker">棋谱详情</div>
          <h3>${esc(rec.level || "对局")} · ${rec.size || 19} 路</h3>
          <div class="detail-grid">
            <div><span>对局时间</span><b>${esc(created)}</b></div>
            <div><span>结果</span><b>${esc(rec.result || "进行中")}</b></div>
            <div><span>手数</span><b>${rec.moves || 0} 手</b></div>
            <div><span>规则</span><b>${rules}</b></div>
            <div><span>贴目</span><b>${komi}</b></div>
            <div><span>让子</span><b>${handicap}</b></div>
            <div><span>对局用时</span><b>${time}${byo}</b></div>
            <div><span>执子</span><b>${color}</b></div>
          </div>
          <div class="modal-actions">
            <button class="ghost-btn" data-close>关闭</button>
            <button class="record-del" data-detail-del>删除</button>
            <button class="record-load" data-detail-load>载入复盘</button>
          </div>
        </div>`;
        document.body.appendChild(overlay);
        const close = () => {
          overlay.remove();
          state.detailOpen = false;
          state.detailClose = null;
        };
        state.detailOpen = true;
        state.detailClose = close;
        overlay.querySelector("[data-close]").onclick = close;
        overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
        overlay.querySelector("[data-detail-load]").onclick = () => {
          close();
          const btn = Array.from($("#records-list").querySelectorAll("[data-load]")).find((b) => b.dataset.load === rec.file);
          if (btn) btn.click();
        };
        overlay.querySelector("[data-detail-del]").onclick = () => {
          close();
          const btn = Array.from($("#records-list").querySelectorAll("[data-del]")).find((b) => b.dataset.del === rec.file);
          if (btn) btn.click();
        };
      };
      const renderArchived = () => {
        let shown = games.filter((rec) => recordFilter === "all"
          || ((recordFilter === "B" || recordFilter === "W") && rec.winner === recordFilter)
          || String(rec.size) === recordFilter);
        const q = (searchInput.value || "").trim().toLowerCase();
        if (q) {
          shown = shown.filter((rec) => [rec.level, rec.result, rec.created_at, rec.size, rec.moves]
            .join(" ").toLowerCase().includes(q));
        }
        const sortBy = sortSelect.value;
        if (sortBy === "oldest") shown = shown.slice().sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
        else if (sortBy === "moves") shown = shown.slice().sort((a, b) => (b.moves || 0) - (a.moves || 0));
        else if (sortBy === "result") shown = shown.slice().sort((a, b) => String(a.result || "").localeCompare(String(b.result || "")));
        visibleFiles = shown.map((rec) => rec.file);
        $("#record-count").textContent = `${shown.length} / ${games.length} 局`;
        if (!shown.length) {
          list.innerHTML = `<div class="records-empty">没有符合条件的棋谱。</div>`;
        } else {
          list.innerHTML = shown.map((rec) => `<div class="record-row">
            <input type="checkbox" class="record-select" data-select="${esc(rec.file)}" ${selected.has(rec.file) ? "checked" : ""} aria-label="选择棋谱">
            <div class="record-main" data-detail="${esc(rec.file)}"><b>${esc(rec.level || "对局")} · ${rec.size || 19} 路</b><span>${esc((rec.created_at || "").replace("T", " "))} · ${rec.moves || 0} 手</span></div>
            <span class="record-result">${esc(rec.result || "进行中")}</span>
            <button class="record-load" data-load="${esc(rec.file)}" type="button">载入复盘</button>
            <button class="record-copy" data-copy-sgf="${esc(rec.file)}" type="button" title="复制 SGF 到剪贴板">复制</button>
            <button class="record-del" data-del="${esc(rec.file)}" type="button" title="删除棋谱">删除</button>
            <a class="record-dl" href="/api/records/${encodeURIComponent(rec.file)}/sgf">SGF</a>
          </div>`).join("");
        }
        updateBatchBar();
        syncLang();
      };
      searchInput.oninput = renderArchived;
      sortSelect.onchange = renderArchived;
      selectAll.onchange = () => {
        if (selectAll.checked) visibleFiles.forEach((f) => selected.add(f));
        else visibleFiles.forEach((f) => selected.delete(f));
        renderArchived();
      };
      deleteSelected.onclick = async () => {
        const files = Array.from(selected);
        if (!files.length) return;
        const ok = await confirmDialog({
          kicker: "批量删除",
          title: `删除选中的 ${files.length} 局棋谱？`,
          message: "选中的棋谱文件会从 data/games 中删除，无法恢复。",
          confirmText: "删除",
        });
        if (!ok) return;
        try {
          const res = await api("/api/records/delete", { body: { files } });
          const removed = new Set(res.deleted || []);
          for (let i = games.length - 1; i >= 0; i--) if (removed.has(games[i].file)) games.splice(i, 1);
          selected.clear();
          renderStats(res.stats);
          renderArchived();
          toast(`已删除 ${res.deleted ? res.deleted.length : 0} 局棋谱`);
        } catch (err) {
          toast(err.message || "批量删除失败", "error");
        }
      };
      list.addEventListener("change", (e) => {
        const cb = e.target.closest("[data-select]");
        if (!cb) return;
        if (cb.checked) selected.add(cb.dataset.select);
        else selected.delete(cb.dataset.select);
        updateBatchBar();
      });
      $("#record-filters").onclick = (e) => {
        const btn = e.target.closest("[data-filter]");
        if (!btn) return;
        recordFilter = btn.dataset.filter;
        $$("#record-filters [data-filter]").forEach((b) => b.classList.toggle("active", b === btn));
        renderArchived();
      };
      if (!games.length) {
        list.innerHTML = `<div class="records-empty">还没有已归档的棋谱。下完一局后会自动保存到这里。</div>`;
        $("#record-count").textContent = "";
      } else {
        renderArchived();
      }
      activeList.onclick = async (e) => {
        const abandon = e.target.closest("[data-abandon]");
        if (abandon) {
          const ok = await confirmDialog({
            kicker: "放弃对局",
            title: "确定放弃这局？",
            message: "这局不会归档，棋谱会被删除，且无法恢复。",
            confirmText: "放弃",
          });
          if (!ok) return;
          try {
            const wasCurrent = state.game && state.game.id === abandon.dataset.abandon;
            await api(`/api/game/${encodeURIComponent(abandon.dataset.abandon)}/abandon`, { body: {} });
            if (wasCurrent) {
              closeModal();
              const game = await createGame({ silent: true, noConfirm: true });
              applyGame(game, { initial: true });
            } else {
              closeModal();
              await showRecords();
            }
            toast("已放弃未完成对局");
          } catch (err) {
            toast(err.message || "放弃对局失败", "error");
          }
          return;
        }
        const btn = e.target.closest("[data-resume]");
        if (!btn) return;
        try {
          if (state.game && state.game.turn > 0 && state.game.phase !== "ended") {
            const ok = await confirmDialog({
              kicker: "继续对局",
              title: "切换到另一局？",
              message: "当前未完成的对局会保留在后台，可以稍后从「棋谱」再次进入。",
              confirmText: "继续",
            });
            if (!ok) return;
          }
          const game = await api(`/api/game/${encodeURIComponent(btn.dataset.resume)}`);
          closeModal();
          state.reviewPayload = null;
          state.reviewIndex = null;
          applyGame(game, { initial: true });
          toast("已继续未完成对局");
        } catch (err) {
          toast(err.message || "载入对局失败", "error");
        }
      };
      list.onclick = async (e) => {
        const detail = e.target.closest("[data-detail]");
        if (detail) {
          showRecordDetail(games.find((g) => g.file === detail.dataset.detail));
          return;
        }
        const copy = e.target.closest("[data-copy-sgf]");
        if (copy) {
          try {
            const res = await fetch(`/api/records/${encodeURIComponent(copy.dataset.copySgf)}/sgf`);
            if (!res.ok) throw new Error("读取 SGF 失败");
            const text = await res.text();
            await navigator.clipboard.writeText(text);
            toast("SGF 已复制");
          } catch (err) {
            toast(err.message || "复制失败，请直接用 SGF 下载", "error");
          }
          return;
        }
        const del = e.target.closest("[data-del]");
        if (del) {
          const ok = await confirmDialog({
            kicker: "删除棋谱",
            title: "确定删除这局棋谱？",
            message: "棋谱文件会从 data/games 中删除，无法恢复。",
            confirmText: "删除",
          });
          if (!ok) return;
          try {
            const res = await api(`/api/records/${encodeURIComponent(del.dataset.del)}/delete`, { body: {} });
            const idx = games.findIndex((g) => g.file === del.dataset.del);
            if (idx >= 0) games.splice(idx, 1);
            selected.delete(del.dataset.del);
            renderStats(res.stats);
            renderArchived();
            toast("棋谱已删除");
          } catch (err) {
            toast(err.message || "删除棋谱失败", "error");
          }
          return;
        }
        const btn = e.target.closest("[data-load]");
        if (!btn) return;
        try {
          const game = await api(`/api/records/${encodeURIComponent(btn.dataset.load)}/load`, { body: {} });
          closeModal();
          state.reviewPayload = null;
          state.reviewIndex = null;
          applyGame(game, { initial: true });
          if (game.turn > 0) {
            await startReview();
            toast("棋谱已载入，正在生成复盘");
          } else {
            toast("棋谱已载入，但这局没有落子，无法复盘");
          }
        } catch (err) {
          toast(err.message || "载入棋谱失败", "error");
        }
      };
      syncLang();
    } catch (err) {
      const message = `<div class="records-empty" style="color:var(--red)">${esc(err.message)}</div>`;
      $("#records-list").innerHTML = message;
      $("#active-records").innerHTML = message;
      syncLang();
    }
  }

  function engineNeedsAttention() {
    if (state.engineOffline) return true;
    return !!(state.engine && state.engine.error);
  }

  function engineBlockedMessage() {
    if (state.engineOffline) {
      return { error: true, text: "与本地服务失去连接，请确认启动窗口仍在运行" };
    }
    const engine = state.engine;
    if (!engine || engine.ready) return null;
    if (engine.error) {
      return { error: true, text: "AI 引擎异常，点左下角「重启引擎」后再试" };
    }
    return { error: false, text: "AI 引擎还在预热，通常需要 15–25 秒，请稍候" };
  }

  async function engineRetry() {
    if (state.engineRetryBusy) return;
    state.engineRetryBusy = true;
    renderEngine();
    try {
      if (state.engineOffline) {
        toast("正在重新连接本地服务…");
        await pollStatus();
      } else {
        await api("/api/engine/restart", { body: {}, timeout: 30000 });
        state.engineRestarting = true;
        state.engineFails = 0;
        toast("正在重启 AI 引擎，通常需要 15–25 秒");
        await pollStatus();
      }
    } catch (err) {
      const text = String((err && err.message) || "");
      if (/连接|服务|超时/.test(text)) state.engineOffline = true;
      toast(text || "重试失败，请确认启动窗口仍在运行", "error", 5000);
    } finally {
      state.engineRetryBusy = false;
      state.engineRestarting = false;
      renderEngine();
    }
  }

  // ==================== 引擎状态 ====================
  async function pollStatus() {
    clearTimeout(state.statusTimer);
    try {
      const data = await api("/api/status", { timeout: 8000 });
      const wasOffline = state.engineOffline;
      state.engineFails = 0;
      state.engineOffline = false;
      state.engine = data.engine || state.engine;
      renderEngine();
      if (wasOffline) toast("已重新连接本地服务");
      const delay = state.engine && state.engine.ready ? 20000 : 2200;
      state.statusTimer = setTimeout(pollStatus, delay);
    } catch (err) {
      state.engineFails += 1;
      if (state.engineFails >= 2) {
        if (!state.engineOffline) {
          state.engineOffline = true;
          toast("与本地服务失去连接，正在自动重连…", "error", 5000);
        }
        renderEngine();
        state.statusTimer = setTimeout(pollStatus, 4000);
      } else {
        state.statusTimer = setTimeout(pollStatus, 1500);
      }
    }
  }

  function renderEngine() {
    const engine = state.engine || {};
    const card = $("#engine-card");
    const dot = $("#engine-dot");
    const label = $("#engine-label");
    const detail = $("#engine-detail");
    const time = $("#engine-time");
    const progress = $("#engine-progress");
    const retry = $("#engine-retry-btn");
    if (!card || !retry) return;
    const offline = state.engineOffline;
    const failed = !offline && !!engine.error;
    const actionable = offline || failed;
    dot.className = "status-dot";
    card.classList.remove("ready", "error", "starting", "offline");
    progress.parentElement.classList.remove("indeterminate");
    retry.classList.add("hidden");
    retry.disabled = !!state.engineRetryBusy;
    if (offline) {
      dot.classList.add("error");
      card.classList.add("offline");
      label.textContent = "无法连接服务";
      detail.textContent = "请确认启动窗口仍在运行，程序正在自动重连";
      time.textContent = "离线";
      progress.style.width = "0%";
      retry.textContent = state.engineRetryBusy ? "连接中…" : "重新连接";
      retry.classList.remove("hidden");
      card.title = "与本地服务连接中断，点击重新连接";
    } else if (failed) {
      dot.classList.add("error");
      card.classList.add("error");
      label.textContent = "引擎异常";
      detail.textContent = engine.error;
      time.textContent = "点击重启";
      progress.style.width = "0%";
      retry.textContent = state.engineRetryBusy ? "重启中…" : "重启引擎";
      retry.classList.remove("hidden");
      card.title = engine.error;
    } else if (engine.ready) {
      dot.classList.add("ready");
      card.classList.add("ready");
      label.textContent = "引擎就绪";
      detail.textContent = `${engine.backend || "本地"} · ${engine.device || "GPU"}${engine.human_model ? " · Human SL" : ""}`;
      time.textContent = engine.startup_seconds ? `${engine.startup_seconds}s` : "在线";
      progress.style.width = "100%";
      card.title = "KataGo 已就绪";
    } else {
      dot.classList.add("starting");
      card.classList.add("starting");
      label.textContent = state.engineRestarting ? "引擎重启中" : "引擎预热中";
      detail.textContent = state.engineRestarting
        ? "正在重新加载 KataGo 与神经网络"
        : "KataGo · 加载 b10c512 神经网络，约 15–25 秒";
      time.textContent = "等待";
      progress.parentElement.classList.add("indeterminate");
      progress.style.width = "38%";
      card.title = "正在加载本地 AI，通常需要 15–25 秒";
    }
    card.tabIndex = actionable ? 0 : -1;
    if (actionable) card.setAttribute("role", "button");
    else card.removeAttribute("role");
    card.setAttribute("aria-label", `${label.textContent}：${detail.textContent}${actionable ? "。可点击重试" : ""}`);
    $("#model-info").textContent = `KataGo ${engine.version ? "v" + engine.version : ""} · b10c512${engine.human_model ? " + Human SL" : ""}`;
    if (state.game) renderChrome();
    syncLang();
  }

  // ==================== 其它 UI ====================
  function updateSoundButton() {
    $("#sound-btn").classList.toggle("active", state.sound);
    $("#sound-btn").title = state.sound ? "关闭落子音效" : "开启落子音效";
    syncLang();
  }

  function updateFlipButton() {
    $("#flip-btn").classList.toggle("active", state.flip);
    $("#flip-btn").title = state.flip ? "复位棋盘方向" : "翻转棋盘方向";
    $("#flip-btn").setAttribute("aria-label", $("#flip-btn").title);
    syncLang();
  }

  function updateNumbersButton() {
    $("#numbers-btn").classList.toggle("active", state.showNumbers);
    $("#numbers-btn").title = state.showNumbers ? "隐藏手数" : "显示手数";
    syncLang();
  }

  window.addEventListener("DOMContentLoaded", init);
  if (document.readyState !== "loading") init();
})();
