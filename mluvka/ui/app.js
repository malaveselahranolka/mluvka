(() => {
  "use strict";

  const VK_META = {
    163: { keycap: "Pravý Ctrl", headline: "Drž pravý Ctrl a mluv", cmdHint: "Shift + pravý Ctrl = příkaz pro Claude" },
    161: { keycap: "Pravý Shift", headline: "Drž pravý Shift a mluv", cmdHint: "Levý Shift + pravý Shift = příkaz pro Claude" },
    20: { keycap: "Caps Lock", headline: "Drž Caps Lock a mluv", cmdHint: "Shift + Caps Lock = příkaz pro Claude" },
    120: { keycap: "F9", headline: "Drž F9 a mluv", cmdHint: "Shift + F9 = příkaz pro Claude" },
    255: { keycap: "Fn / 🌐", headline: "Drž Fn a mluv", cmdHint: "Shift + Fn = příkaz pro Claude" },
    165: { keycap: "Pravý Option", headline: "Drž pravý Option a mluv", cmdHint: "Shift + pravý Option = příkaz pro Claude" },
    92: { keycap: "Pravý Command", headline: "Drž pravý Command a mluv", cmdHint: "Shift + pravý Command = příkaz pro Claude" },
  };
  const MAC_VK_OPTIONS = [
    [255, "Fn / 🌐"],
    [165, "Pravý Option"],
    [92, "Pravý Command"],
    [163, "Pravý Ctrl"],
  ];
  const CZECH_DAYS = ["Ne", "Po", "Út", "St", "Čt", "Pá", "So"];
  const BAR_WEIGHTS = [0.35, 0.50, 0.70, 0.88, 1.00, 0.88, 0.70, 0.50, 0.35];
  const SVG_NS = "http://www.w3.org/2000/svg";

  const state = {
    data: null,
    activePage: "prehled",
    recentItems: [],
    historyItems: [],
    historyHasMore: false,
    historyQuery: "",
    corrections: [],
    snippets: [],
    pametItems: [],
    importPreview: { slova: [], opravy: [], existing_count: 0 },
    trenink: null,
    treninkIndex: 1,
    treninkRecording: false,
    treninkLastRecordedIndex: 0,
    treninkMeasRunning: false,
    treninkMeasDone: 0,
    treninkMeasTotal: 0,
    treninkMeasError: "",
    darLastPath: "",
    darLastFilename: "",
    diktatyItems: [],
    diktatyTotal: 0,
    diktatyVerifiedCount: 0,
    diktatyVerifiedS: 0,
    diktatyHasMore: false,
    diktatySeq: 0,
    diktatyInFlight: 0,
    diktatyLoading: false,
    diktatyUkladatZvuk: null,
    diktatOpenId: "",
    diktatDraft: "",
    diktatBusyId: "",
    diktatError: "",
    lastDiktatyRenderSig: "",
    treninkModels: null,
    treninkModelError: "",
    treninkModelsSeq: 0,
    treninkSelectingModel: false,
    lastModelsRenderKey: "",
    lastModelsStructKey: "",
    doladeni: null,
    doladeniInFlight: false,
    doladeniBusy: false,
    doladeniSeq: 0,
    lastDoladeniLiveAt: 0,
    lastDoladeniLiveMsg: "",
    pendingDoladeniLiveMsg: "",
    meterBars: [0, 0, 0, 0, 0, 0, 0, 0, 0],
    savedCorrectionsJson: "[]",
    savedSnippetsJson: "[]",
    lastWordsJson: null,
    lastRenderedPairsJson: null,
    lastRenderedSnippetsJson: null,
    lastRecentJson: null,
    lastMicSig: "",
    lastServerWebhook: "",
    webhookInvalid: false,
  };
  const blurFlushers = { corrections: null, snippets: null, webhook: null };

  function isEditingPairs() {
    return Boolean(
      timers.corrections
      || (document.activeElement && $("corrections-list").contains(document.activeElement))
      || state.corrections.some(([a]) => !a.trim())
    );
  }

  function isEditingSnippets() {
    return Boolean(
      timers.snippets
      || (document.activeElement && $("snippets-list").contains(document.activeElement))
      || state.snippets.some(([a]) => !a.trim())
    );
  }
  const timers = {
    snackFade: null,
    snackHide: null,
    historySearch: null,
    corrections: null,
    snippets: null,
    webhook: null,
    poll: null,
    levelPoll: null,
    measPoll: null,
    doladeniPoll: null,
    doladeniLive: null,
  };

  const $ = (id) => document.getElementById(id);

  function mk(tag, cls = "", text = "") {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }

  function waitForApi() {
    if (window.pywebview && window.pywebview.api) return Promise.resolve(window.pywebview.api);
    return new Promise((resolve) => {
      const done = () => { if (window.pywebview && window.pywebview.api) resolve(window.pywebview.api); };
      window.addEventListener("pywebviewready", done, { once: true });
      const check = setInterval(() => {
        if (window.pywebview && window.pywebview.api) { clearInterval(check); resolve(window.pywebview.api); }
      }, 25);
    });
  }

  async function apiCall(method, ...args) {
    try {
      const api = await waitForApi();
      const res = await api[method](...args);
      return res && typeof res === "object" ? res : { ok: true, value: res };
    } catch (err) {
      return { ok: false, error: (err && err.message) || "Chyba komunikace s aplikací." };
    }
  }

  function createIconSvg(kind) {
    const svg = document.createElementNS(SVG_NS, "svg");
    const sz = kind === "close" ? "12" : "16";
    for (const [k, v] of [["width", sz], ["height", sz], ["viewBox", "0 0 24 24"], ["fill", "none"],
      ["stroke", "currentColor"], ["stroke-width", "1.75"], ["stroke-linecap", "round"],
      ["stroke-linejoin", "round"], ["aria-hidden", "true"]]) {
      svg.setAttribute(k, v);
    }
    let specs;
    if (kind === "copy") {
      specs = [["rect", { x: 9, y: 9, width: 13, height: 13, rx: 2 }], ["path", { d: "M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" }]];
    } else if (kind === "pencil") {
      specs = [["path", { d: "M12 20h9" }], ["path", { d: "M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" }]];
    } else if (kind === "trash") {
      specs = [
        ["polyline", { points: "3 6 5 6 21 6" }],
        ["path", { d: "M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" }],
        ["path", { d: "M10 11v6M14 11v6" }],
        ["path", { d: "M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" }],
      ];
    } else {
      specs = [["line", { x1: 18, y1: 6, x2: 6, y2: 18 }], ["line", { x1: 6, y1: 6, x2: 18, y2: 18 }]];
    }
    for (const [tag, attrs] of specs) {
      const el = document.createElementNS(SVG_NS, tag);
      for (const [ak, av] of Object.entries(attrs)) el.setAttribute(ak, String(av));
      svg.appendChild(el);
    }
    return svg;
  }

  function showSnackbar(text, { isError = false } = {}) {
    const el = $("snackbar");
    clearTimeout(timers.snackFade);
    clearTimeout(timers.snackHide);
    el.textContent = text;
    el.classList.toggle("error", Boolean(isError));
    el.classList.remove("visible");
    el.hidden = false;
    void el.offsetWidth;
    el.classList.add("visible");
    timers.snackFade = setTimeout(() => {
      el.classList.remove("visible");
      timers.snackHide = setTimeout(() => { el.hidden = true; }, 150);
    }, isError ? 4000 : 1600);
  }

  function setInlineResult(el, text, kind = "") {
    el.textContent = text || "";
    el.classList.remove("ok", "error");
    if (kind) el.classList.add(kind);
  }

  function setSwitchState(btn, checked) {
    btn.setAttribute("aria-checked", checked ? "true" : "false");
  }

  function scheduleDebounced(key, fn, delay = 400) {
    clearTimeout(timers[key]);
    timers[key] = setTimeout(() => { timers[key] = null; void fn(); }, delay);
  }

  function formatInt(n) { return Number(n || 0).toLocaleString("cs-CZ"); }

  function formatSavedDuration(seconds) {
    const totalMin = Math.max(0, Math.round(Number(seconds || 0) / 60));
    if (totalMin < 60) return `${totalMin} min`;
    const hours = Math.floor(totalMin / 60);
    const mins = totalMin % 60;
    return mins > 0 ? `${hours} h ${mins} min` : `${hours} h`;
  }

  function parseDateSafe(iso) {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function formatDayHeading(d) {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const target = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const diff = Math.round((today - target) / 86400000);
    if (diff === 0) return "Dnes";
    if (diff === 1) return "Včera";
    return `${CZECH_DAYS[d.getDay()]} ${d.getDate()}. ${d.getMonth() + 1}.`;
  }

  function formatTime(iso) {
    const d = parseDateSafe(iso);
    if (!d) return "—";
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }

  function formatShortDate(iso) {
    const d = parseDateSafe(iso);
    if (!d) return "";
    return `${d.getDate()}. ${d.getMonth() + 1}.`;
  }

  function entryText(entry) {
    const opr = entry && entry.opraveno ? String(entry.opraveno).trim() : "";
    return opr || String((entry && entry.text) || "");
  }

  function entryMatches(a, b) {
    if (!a || !b) return false;
    if (a.id && b.id) return String(a.id) === String(b.id);
    if (a.id || b.id) return false;
    return String(a.cas || "") === String(b.cas || "") && String(a.text || "") === String(b.text || "");
  }

  function removeOneEntry(list, target) {
    const idx = list.findIndex((x) => entryMatches(x, target));
    if (idx < 0) return list;
    return list.slice(0, idx).concat(list.slice(idx + 1));
  }

  function makeIconBtn(cls, label, iconKind, onClick) {
    const btn = mk("button", cls);
    btn.type = "button";
    btn.setAttribute("aria-label", label);
    btn.title = label;
    btn.appendChild(createIconSvg(iconKind));
    btn.addEventListener("click", onClick);
    return btn;
  }

  function buildSuggestionBanner(pair, container) {
    const [z, na] = pair;
    const box = mk("div", "history-suggestion");
    box.appendChild(mk("span", "", `Přidat do Oprav: „${z}“ → „${na}“?`));
    const btns = mk("div", "history-suggestion-actions");
    const addBtn = mk("button", "btn btn-secondary btn-edit-sm", "Přidat");
    addBtn.type = "button";
    addBtn.addEventListener("click", async () => {
      addBtn.disabled = true;
      const res = await apiCall("add_oprava", z, na);
      if (!res.ok) {
        addBtn.disabled = false;
        showSnackbar(res.error || "Přidání opravy selhalo.", { isError: true });
        return;
      }
      if (Array.isArray(res.pamet)) state.pametItems = res.pamet;
      state.data = res;
      renderAll({ skipPairsRender: false, skipSnippetsRender: isEditingSnippets() });
      renderPamet();
      box.remove();
      showSnackbar("Přidáno do Oprav");
    });
    const noBtn = mk("button", "btn btn-tertiary btn-edit-sm", "Ne");
    noBtn.type = "button";
    noBtn.addEventListener("click", () => box.remove());
    btns.append(addBtn, noBtn);
    box.appendChild(btns);
    container.appendChild(box);
  }

  function makeHistoryRow(entry) {
    const row = mk("div", "history-row");
    const head = mk("div", "history-row-head");
    const toggleBtn = mk("button", "history-toggle");
    toggleBtn.type = "button";
    toggleBtn.setAttribute("aria-expanded", "false");

    const renderMetaAndText = () => {
      toggleBtn.textContent = "";
      const meta = mk("div", "history-meta");
      meta.appendChild(mk("span", "history-time", formatTime(entry.cas)));
      if (entry.aplikace) meta.appendChild(mk("span", "app-chip", entry.aplikace));
      if (entry.opraveno && String(entry.opraveno).trim()) {
        meta.appendChild(mk("span", "opraveno-chip", "opraveno"));
      }
      toggleBtn.append(meta, mk("div", "history-text", entryText(entry)));
    };
    renderMetaAndText();

    toggleBtn.addEventListener("click", () => {
      const expanded = row.classList.toggle("expanded");
      toggleBtn.setAttribute("aria-expanded", expanded ? "true" : "false");
    });

    const suggestionsWrap = mk("div", "history-suggestions-wrap");

    const openEditor = () => {
      if (row.querySelector(".history-edit-box")) return;
      toggleBtn.hidden = true;
      actions.hidden = true;
      const box = mk("div", "history-edit-box");
      const ta = mk("textarea", "textarea history-edit-textarea");
      ta.rows = 2;
      ta.setAttribute("aria-label", "Upravit text přepisu");
      ta.value = entryText(entry);
      const actRow = mk("div", "history-edit-actions");
      const saveBtn = mk("button", "btn btn-primary btn-edit-sm", "Uložit");
      saveBtn.type = "button";
      const cancelBtn = mk("button", "btn btn-secondary btn-edit-sm", "Zrušit");
      cancelBtn.type = "button";

      const closeEditor = () => {
        box.remove();
        toggleBtn.hidden = false;
        actions.hidden = false;
      };

      const submitEdit = async () => {
        const trimmed = ta.value.trim();
        if (!trimmed) { showSnackbar("Text nesmí být prázdný.", { isError: true }); return; }
        saveBtn.disabled = true;
        cancelBtn.disabled = true;
        const res = await apiCall(
          "edit_history",
          entry.id || "",
          trimmed,
          entry.cas || "",
          entry.id ? null : (entry.text || "")
        );
        if (!res.ok) {
          saveBtn.disabled = false;
          cancelBtn.disabled = false;
          showSnackbar(res.error || "Uložení opravy selhalo.", { isError: true });
          return;
        }
        if (res.entry) Object.assign(entry, res.entry);
        const recMatch = state.recentItems.find((x) => entryMatches(x, entry));
        if (recMatch && res.entry) Object.assign(recMatch, res.entry);
        const histMatch = state.historyItems.find((x) => entryMatches(x, entry));
        if (histMatch && res.entry) Object.assign(histMatch, res.entry);
        if (res.stats && state.data) { state.data.stats = res.stats; renderOverview(); }
        if (Array.isArray(res.pamet)) { state.pametItems = res.pamet; renderPamet(); }
        renderMetaAndText();
        state.lastRecentJson = null;
        renderRecent();
        closeEditor();
        suggestionsWrap.textContent = "";
        if (Array.isArray(res.suggestions) && res.suggestions.length > 0) {
          res.suggestions.forEach((pair) => buildSuggestionBanner(pair, suggestionsWrap));
        }
        showSnackbar("Uloženo");
      };

      ta.addEventListener("input", () => autoResizeTextarea(ta));
      ta.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape") { ev.preventDefault(); closeEditor(); }
        else if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") { ev.preventDefault(); void submitEdit(); }
      });
      cancelBtn.addEventListener("click", closeEditor);
      saveBtn.addEventListener("click", () => { void submitEdit(); });

      actRow.append(saveBtn, cancelBtn);
      box.append(ta, actRow);
      row.insertBefore(box, suggestionsWrap);
      autoResizeTextarea(ta);
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
    };

    const actions = mk("div", "history-actions");
    actions.append(
      makeIconBtn("icon-btn", "Upravit", "pencil", openEditor),
      makeIconBtn("icon-btn", "Kopírovat", "copy", async () => {
        const res = await apiCall("copy_text", entryText(entry));
        if (res.ok) showSnackbar("Zkopírováno");
        else showSnackbar(res.error || "Kopírování selhalo.", { isError: true });
      }),
      makeIconBtn("icon-btn danger", "Smazat", "trash", async () => {
        const res = await apiCall(
          "delete_history",
          entry.id || "",
          entry.cas || "",
          entry.id ? null : (entry.text || "")
        );
        if (!res.ok) { showSnackbar(res.error || "Smazání záznamu selhalo.", { isError: true }); return; }
        state.diktatySeq += 1;
        if (res.stats && state.data) { state.data.stats = res.stats; renderOverview(); }
        state.recentItems = removeOneEntry(state.recentItems, entry);
        state.historyItems = removeOneEntry(state.historyItems, entry);
        if (entry && entry.id) {
          const delId = String(entry.id);
          const idxD = state.diktatyItems.findIndex((it) => String(it.id || "") === delId);
          if (idxD >= 0) {
            const removedItem = state.diktatyItems[idxD];
            state.diktatyItems = state.diktatyItems.slice(0, idxD).concat(state.diktatyItems.slice(idxD + 1));
            state.diktatyTotal = Math.max(0, state.diktatyTotal - 1);
            if (removedItem.overeno) {
              state.diktatyVerifiedCount = Math.max(0, state.diktatyVerifiedCount - 1);
              state.diktatyVerifiedS = Math.max(0, state.diktatyVerifiedS - Number(removedItem.audio_s || 0));
            }
            if (state.diktatOpenId === delId) {
              state.diktatOpenId = "";
              state.diktatDraft = "";
              state.diktatError = "";
            }
            state.lastDiktatyRenderSig = "";
            renderDiktaty();
          }
        }
        state.lastRecentJson = null;
        renderRecent();
        renderHistoryList();
        if (state.recentItems.length < 5) void loadRecent();
        showSnackbar("Smazáno");
      })
    );
    head.append(toggleBtn, actions);
    row.append(head, suggestionsWrap);
    return row;
  }

  function renderSidebarStatus() {
    if (!state.data) return;
    $("config-invalid-bar").hidden = !Boolean(state.data.config_invalid);
    const running = Boolean(state.data.app_running);
    $("status-chip").classList.toggle("running", running);
    $("status-text").textContent = running ? "Mluvka běží" : "Mluvka neběží";
    $("start-app-btn").hidden = running;
  }

  function renderOverview() {
    if (!state.data) return;
    const cfg = state.data.config || {};
    const meta = VK_META[Number(cfg.klavesa_vk || 163)] || VK_META[163];
    $("hero-headline").textContent = meta.headline;
    $("hero-keycap").textContent = meta.keycap;
    $("hero-cmd-hint").textContent = meta.cmdHint;

    const st = state.data.stats || {};
    $("stat-words").textContent = formatInt(st.words || 0);
    $("stat-count").textContent = formatInt(st.count || 0);
    $("stat-saved").textContent = formatSavedDuration(st.saved_s || 0);
    const wpmEl = $("stat-wpm");
    wpmEl.textContent = "";
    wpmEl.append(document.createTextNode(`${formatInt(Math.round(st.wpm || 0))} `), mk("span", "stat-unit", "slov/min"));
  }

  function emptyDictateMessage() {
    const plat = state.data && state.data.platform;
    if (plat !== "darwin") return "Zatím žádné diktáty. Drž pravý Ctrl a mluv.";
    const vk = Number(((state.data && state.data.config) || {}).klavesa_vk || 255);
    const meta = VK_META[vk] || VK_META[255];
    return `Zatím žádné diktáty. ${meta.headline}.`;
  }

  function renderRecent() {
    const emptyMsg = state.recentItems.length ? "" : emptyDictateMessage();
    const sig = JSON.stringify({ items: state.recentItems, empty: emptyMsg });
    if (sig === state.lastRecentJson) return;
    state.lastRecentJson = sig;
    const wrap = $("recent-list");
    wrap.textContent = "";
    if (!state.recentItems.length) {
      wrap.appendChild(mk("div", "empty-state", emptyMsg));
      return;
    }
    state.recentItems.forEach((item) => wrap.appendChild(makeHistoryRow(item)));
  }

  function renderHistoryList() {
    const container = $("history-container");
    container.textContent = "";
    $("history-more-wrap").hidden = !state.historyHasMore;

    if (!state.historyItems.length) {
      const card = mk("div", "card");
      card.appendChild(mk("div", "empty-state flush", state.historyQuery
        ? "Žádný diktát neodpovídá hledání."
        : emptyDictateMessage()));
      container.appendChild(card);
      return;
    }

    const groups = [];
    const byKey = new Map();
    for (const item of state.historyItems) {
      const d = parseDateSafe(item.cas);
      const key = d ? `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}` : "other";
      let grp = byKey.get(key);
      if (!grp) {
        grp = { label: d ? formatDayHeading(d) : "Neznámé datum", items: [] };
        byKey.set(key, grp);
        groups.push(grp);
      }
      grp.items.push(item);
    }
    for (const grp of groups) {
      const sec = mk("div", "history-group");
      sec.appendChild(mk("h2", "history-day-title", grp.label));
      const card = mk("div", "card card-list");
      grp.items.forEach((item) => card.appendChild(makeHistoryRow(item)));
      sec.appendChild(card);
      container.appendChild(sec);
    }
  }
    function makeBoundInput(value, ariaLabel, placeholder, onInput) {
    const inp = mk("input", "input");
    inp.type = "text";
    inp.value = value || "";
    inp.setAttribute("aria-label", ariaLabel);
    if (placeholder) inp.placeholder = placeholder;
    inp.addEventListener("input", () => onInput(inp.value));
    return inp;
  }

  function renderCorrectionRows() {
    const list = $("corrections-list");
    const head = $("corrections-head");
    list.textContent = "";
    head.hidden = state.corrections.length === 0;
    if (!state.corrections.length) {
      list.appendChild(mk("div", "empty-state flush", "Zatím žádná oprava."));
      return;
    }
    state.corrections.forEach((pair, idx) => {
      const row = mk("div", "pair-row");
      const fromInp = makeBoundInput(pair[0], "Když uslyším", "Např. klód", (val) => {
        state.corrections[idx][0] = val;
        scheduleDebounced("corrections", () => flushCorrections());
      });
      const toInp = makeBoundInput(pair[1], "Napiš", "Např. Claude", (val) => {
        state.corrections[idx][1] = val;
        scheduleDebounced("corrections", () => flushCorrections());
      });
      const delBtn = makeIconBtn("icon-btn danger", "Odebrat opravu", "trash", async () => {
        state.corrections.splice(idx, 1);
        renderCorrectionRows();
        await flushCorrections(true);
      });
      row.append(fromInp, toInp, delBtn);
      list.appendChild(row);
    });
  }

  function autoResizeTextarea(ta) {
    ta.style.height = "auto";
    ta.style.height = `${Math.max(54, Math.min(ta.scrollHeight, 180))}px`;
  }

  function renderSnippetCards() {
    const list = $("snippets-list");
    list.textContent = "";
    if (!state.snippets.length) {
      const card = mk("div", "card");
      card.appendChild(mk("div", "empty-state flush", "Zatím žádná zkratka."));
      list.appendChild(card);
      return;
    }
    state.snippets.forEach((pair, idx) => {
      const card = mk("div", "card snippet-card");
      const top = mk("div", "snippet-head");
      const trigWrap = mk("div", "snippet-trigger-wrap");
      const triggerInp = makeBoundInput(pair[0], "Když řeknu", "Když řeknu (např. můj podpis)", (val) => {
        state.snippets[idx][0] = val;
        scheduleDebounced("snippets", () => flushSnippets());
      });
      const delBtn = makeIconBtn("icon-btn danger", "Smazat zkratku", "trash", async () => {
        state.snippets.splice(idx, 1);
        renderSnippetCards();
        await flushSnippets(true);
      });
      trigWrap.appendChild(triggerInp);
      top.append(trigWrap, delBtn);

      const textArea = mk("textarea", "textarea");
      textArea.rows = 2;
      textArea.value = pair[1] || "";
      textArea.setAttribute("aria-label", "Vložený text");
      textArea.placeholder = "Vložený text…";
      textArea.addEventListener("input", () => {
        autoResizeTextarea(textArea);
        state.snippets[idx][1] = textArea.value;
        scheduleDebounced("snippets", () => flushSnippets());
      });

      card.append(top, textArea);
      list.appendChild(card);
      autoResizeTextarea(textArea);
    });
  }

  function renderImportPreview() {
    const prev = state.importPreview || { slova: [], opravy: [], existing_count: 0 };
    const slova = prev.slova || [];
    const opravy = prev.opravy || [];
    const existing = Number(prev.existing_count || 0);
    const wrap = $("import-preview");
    if (!slova.length && !opravy.length && existing === 0) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    $("import-preview-count").textContent = `Nových slov: ${slova.length}, oprav: ${opravy.length} (${existing} už máš)`;

    const wordsWrap = $("import-preview-words");
    wordsWrap.textContent = "";
    slova.forEach((w, idx) => {
      const chip = mk("span", "chip");
      chip.setAttribute("role", "listitem");
      chip.appendChild(mk("span", "", w));
      chip.appendChild(makeIconBtn("chip-remove", `Odebrat ${w}`, "close", () => {
        state.importPreview.slova.splice(idx, 1);
        renderImportPreview();
      }));
      wordsWrap.appendChild(chip);
    });

    const pairsWrap = $("import-preview-pairs");
    pairsWrap.textContent = "";
    opravy.forEach((pair, idx) => {
      const item = mk("div", "import-pair-item");
      item.appendChild(mk("span", "pamet-pair-text", `${pair[0]} → ${pair[1]}`));
      item.appendChild(makeIconBtn("icon-btn", `Odebrat opravu ${pair[0]}`, "close", () => {
        state.importPreview.opravy.splice(idx, 1);
        renderImportPreview();
      }));
      pairsWrap.appendChild(item);
    });

    $("import-apply-btn").disabled = slova.length === 0 && opravy.length === 0;
  }

  function renderPamet() {
    const items = Array.isArray(state.pametItems) ? state.pametItems : [];
    const list = $("pamet-list");
    const footer = $("pamet-footer");
    list.textContent = "";
    footer.hidden = items.length === 0;
    if (!items.length) {
      list.appendChild(mk("div", "empty-state flush", "Zatím žádné opravy. Když v Historii opravíš přepis, Mluvka si chybu zapamatuje."));
      return;
    }
    const stack = mk("div", "pamet-list-stack");
    for (const item of items) {
      const row = mk("div", "pamet-row");
      row.appendChild(mk("span", "pamet-pair-text", `${item.z} → ${item.na}`));
      const meta = mk("div", "pamet-meta");
      meta.appendChild(mk("span", "pamet-count", `×${Number(item.count || 1)}`));
      const dStr = formatShortDate(item.cas);
      if (dStr) meta.appendChild(mk("span", "pamet-date", dStr));
      if (item.in_slovnik) {
        meta.appendChild(mk("span", "pamet-in-dict", "v Opravách"));
      } else if (Number(item.count || 0) >= 2) {
        const addBtn = mk("button", "btn btn-secondary btn-edit-sm", "Přidat do Oprav");
        addBtn.type = "button";
        addBtn.addEventListener("click", async () => {
          addBtn.disabled = true;
          const res = await apiCall("add_oprava", item.z, item.na);
          if (!res.ok) {
            addBtn.disabled = false;
            showSnackbar(res.error || "Přidání opravy selhalo.", { isError: true });
            return;
          }
          if (Array.isArray(res.pamet)) state.pametItems = res.pamet;
          state.data = res;
          renderAll({ skipPairsRender: false, skipSnippetsRender: isEditingSnippets() });
          renderPamet();
          showSnackbar("Přidáno do Oprav");
        });
        meta.appendChild(addBtn);
      }
      row.appendChild(meta);
      stack.appendChild(row);
    }
    list.appendChild(stack);
  }

  function renderDictionary(skipPairsRender) {
    if (!state.data) return;
    const cfg = state.data.config || {};
    const dict = cfg.slovnik || {};
    const words = Array.isArray(dict.slova) ? dict.slova : [];
    const wordsJson = JSON.stringify(words);
    if (wordsJson !== state.lastWordsJson) {
      state.lastWordsJson = wordsJson;
      const list = $("words-list");
      list.textContent = "";
      if (!words.length) {
        list.appendChild(mk("div", "empty-state flush", "Zatím žádná slova."));
      } else {
        for (const w of words) {
          const chip = mk("span", "chip");
          chip.setAttribute("role", "listitem");
          chip.appendChild(mk("span", "", w));
          chip.appendChild(makeIconBtn("chip-remove", `Odebrat ${w}`, "close", async () => {
            const next = (state.data.config.slovnik.slova || []).filter((x) => x !== w);
            await savePatch({ slovnik: { slova: next } });
          }));
          list.appendChild(chip);
        }
      }
    }

    setSwitchState($("toggle-dict-prompt"), Boolean(cfg.slovnik_do_promptu));

    if (!skipPairsRender) {
      const pairs = Array.isArray(dict.opravy)
        ? dict.opravy.map((p) => [String((p && p[0]) || ""), String((p && p[1]) || "")])
        : [];
      const pairsJson = JSON.stringify(pairs);
      if (pairsJson !== state.lastRenderedPairsJson) {
        state.lastRenderedPairsJson = pairsJson;
        state.corrections = pairs;
        state.savedCorrectionsJson = pairsJson;
        renderCorrectionRows();
      }
    }
    renderImportPreview();
    renderPamet();
  }

  function renderSnippets(skipRender) {
    if (!state.data || skipRender) return;
    const raw = (state.data.config && state.data.config.zkratky) || [];
    const pairs = Array.isArray(raw)
      ? raw.map((p) => [String((p && p[0]) || ""), String((p && p[1]) || "")])
      : [];
    const json = JSON.stringify(pairs);
    if (json !== state.lastRenderedSnippetsJson) {
      state.lastRenderedSnippetsJson = json;
      state.snippets = pairs;
      state.savedSnippetsJson = json;
      renderSnippetCards();
    }
  }

  function renderAi() {
    if (!state.data) return;
    const cfg = state.data.config || {};
    setSwitchState($("toggle-ai-cleanup"), Boolean(cfg.ai_uprava));

    const badge = $("api-key-badge");
    const isSet = Boolean(state.data.api_key_set);
    const tail = state.data.api_key_tail || "";
    badge.classList.toggle("ok", isSet);
    if (isSet) {
      badge.textContent = tail ? `Klíč je uložený (…${tail})` : "Klíč je uložený";
    } else if (state.data.env_key) {
      badge.textContent = "Klíč z prostředí (ANTHROPIC_API_KEY)";
    } else {
      badge.textContent = "Klíč není uložený";
    }
    $("remove-api-key-btn").hidden = !isSet;
    $("test-api-key-btn").disabled = !(isSet || state.data.env_key);
  }

  function renderSettings() {
    if (!state.data) return;
    const cfg = state.data.config || {};
    const isMac = state.data.platform === "darwin";
    const selVk = $("sel-vk");
    if (isMac && selVk.dataset.macInit !== "1") {
      selVk.dataset.macInit = "1";
      selVk.textContent = "";
      for (const [vkVal, label] of MAC_VK_OPTIONS) {
        const opt = document.createElement("option");
        opt.value = String(vkVal);
        opt.textContent = label;
        selVk.appendChild(opt);
      }
    }
    selVk.value = String(cfg.klavesa_vk || (isMac ? 255 : 163));

    const rawDevices = Array.isArray(state.data.devices) ? state.data.devices : [];
    const curMic = cfg.mikrofon == null ? "" : String(cfg.mikrofon);
    const sig = JSON.stringify({ rawDevices, curMic });
    if (sig !== state.lastMicSig) {
      state.lastMicSig = sig;
      const sel = $("sel-mic");
      sel.textContent = "";
      const defOpt = document.createElement("option");
      defOpt.value = "";
      defOpt.textContent = "Výchozí (systém)";
      sel.appendChild(defOpt);
      let seen = curMic === "";
      for (const dev of rawDevices) {
        const name = typeof dev === "string" ? dev : String((dev && dev.name) || "");
        if (!name) continue;
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = (typeof dev === "object" && dev && dev.default) ? `${name} (výchozí)` : name;
        if (name === curMic) seen = true;
        sel.appendChild(opt);
      }
      if (!seen && curMic) {
        const miss = document.createElement("option");
        miss.value = curMic;
        miss.textContent = `${curMic} (nedostupný)`;
        sel.appendChild(miss);
      }
      sel.value = curMic;
    }

    setSwitchState($("toggle-sounds"), Boolean(cfg.zvuky));
    const learnBtn = $("toggle-learn-edits");
    const sledAvail = state.data.sledovani_available !== false;
    learnBtn.disabled = !sledAvail;
    setSwitchState(learnBtn, sledAvail ? (cfg.uceni_z_oprav !== false) : false);
    const descLearn = $("desc-learn-edits");
    if (descLearn && !sledAvail) {
      descLearn.textContent = "Učení oprav v jiných aplikacích je na macOS zatím nedostupné.";
    }
    const descAuto = $("desc-autostart");
    if (descAuto && isMac) {
      descAuto.textContent = "Automaticky spustí Mluvku na pozadí po přihlášení do macOS.";
    }
    setSwitchState($("toggle-autostart"), Boolean(cfg.autostart));

    const srvHook = String(cfg.kanal_webhook || "");
    const hookInp = $("input-webhook");
    if (document.activeElement !== hookInp && !timers.webhook && srvHook !== state.lastServerWebhook) {
      state.lastServerWebhook = srvHook;
      hookInp.value = srvHook;
      setInlineResult($("webhook-error"), "");
      state.webhookInvalid = false;
    }

    setSwitchState($("toggle-channel"), Boolean(cfg.kanal_zapnuto));
    setSwitchState($("toggle-history"), Boolean(cfg.historie));
    $("sel-history-days").value = String(cfg.historie_dny || 30);
  }

  function renderPrivacy() {
    if (!state.data) return;
    const cfg = state.data.config || {};
    const hasKey = Boolean(state.data.api_key_set || state.data.env_key);
    const claudeEl = $("privacy-claude-status");
    if (hasKey) {
      claudeEl.textContent = cfg.ai_uprava
        ? "Klíč uložen · automatické uhlazení zapnuto (api.anthropic.com)"
        : "Klíč uložen · jen při Shift + klávesa (api.anthropic.com)";
    } else {
      claudeEl.textContent = "Vypnuto (žádný klíč)";
    }
    const chanEl = $("privacy-channel-status");
    let hookHost = "";
    if (cfg.kanal_webhook) {
      try { hookHost = new URL(cfg.kanal_webhook).hostname; } catch (_err) { hookHost = ""; }
    }
    chanEl.textContent = (cfg.kanal_zapnuto && cfg.kanal_webhook)
      ? `Zapnuto (${hookHost || cfg.kanal_webhook})`
      : "Vypnuto";
    const allowed = Array.isArray(state.data.allowed_hosts) && state.data.allowed_hosts.length
      ? state.data.allowed_hosts.join(", ")
      : "nic";
    $("netguard-line").textContent = `Síťová pojistka povoluje jen: ${allowed}`;
  }

    function czechZ(n) {
    const s = String(n);
    return (Number(n) === 100 || s.startsWith("7")) ? "ze" : "z";
  }

  function formatTimer(sec) {
    const s = Math.max(0, Math.floor(Number(sec || 0)));
    const m = Math.floor(s / 60);
    const rem = s % 60;
    return `${m}:${String(rem).padStart(2, "0")}`;
  }

  function renderMeter(level) {
    const svg = $("trenink-meter");
    if (!svg) return;
    const rects = svg.children;
    const lv = Math.max(0, Math.min(1, Number(level || 0)));
    for (let i = 0; i < rects.length && i < BAR_WEIGHTS.length; i++) {
      const target = lv * BAR_WEIGHTS[i];
      state.meterBars[i] = state.meterBars[i] * 0.35 + target * 0.65;
      const h = Math.max(3, Math.min(18, 3 + 15 * state.meterBars[i]));
      const y = (18 - h) / 2;
      rects[i].setAttribute("y", y.toFixed(1));
      rects[i].setAttribute("height", h.toFixed(1));
    }
  }

  function stopLevelPolling() {
    clearInterval(timers.levelPoll);
    timers.levelPoll = null;
    state.meterBars = [0, 0, 0, 0, 0, 0, 0, 0, 0];
    renderMeter(0);
  }

  function startLevelPolling() {
    stopLevelPolling();
    let inFlight = false;
    timers.levelPoll = setInterval(async () => {
      if (!state.treninkRecording) { stopLevelPolling(); return; }
      if (inFlight) return;
      inFlight = true;
      const res = await apiCall("get_level");
      inFlight = false;
      if (!res.ok) return;
      renderMeter(res.level || 0);
      $("trenink-timer").textContent = formatTimer(res.elapsed_s || 0);
      if (!res.recording && state.treninkRecording) {
        state.treninkRecording = false;
        stopLevelPolling();
        renderTrenink();
      }
    }, 50);
  }

  function stopMeasPolling() {
    clearInterval(timers.measPoll);
    timers.measPoll = null;
  }

  function startMeasPolling() {
    stopMeasPolling();
    timers.measPoll = setInterval(async () => {
      const res = await apiCall("zmerit_stav");
      if (!res.ok) return;
      state.treninkMeasRunning = Boolean(res.running);
      state.treninkMeasDone = Number(res.done || 0);
      state.treninkMeasTotal = Number(res.total || 0);
      state.treninkMeasError = res.error ? String(res.error) : "";
      if (!res.running) {
        stopMeasPolling();
        if (res.result && state.trenink) state.trenink.measurement = res.result;
      }
      renderTrenink();
    }, 350);
  }

  function renderDiffTokens(parent, tokens, fallback) {
    parent.textContent = "";
    if (!Array.isArray(tokens) || !tokens.length) {
      parent.textContent = fallback || "";
      return;
    }
    tokens.forEach((tok, idx) => {
      if (idx > 0) parent.appendChild(document.createTextNode(" "));
      if (tok.ok) {
        parent.appendChild(document.createTextNode(tok.word || ""));
      } else {
        parent.appendChild(mk("span", "diff-err", tok.word || ""));
      }
    });
  }

  function renderTrenink() {
    const cfg = (state.data && state.data.config) || {};
    setSwitchState($("toggle-save-dictation"), Boolean(cfg.trenink_ukladat_zvuk));

    const tr = state.trenink;
    const total = tr ? Number(tr.total || 0) : 0;
    const sentences = tr && Array.isArray(tr.sentences) ? tr.sentences : [];
    const recorded = (tr && tr.recorded) || {};
    const recCount = tr ? Number(tr.recorded_count || 0) : 0;

    state.treninkIndex = total > 0 ? Math.max(1, Math.min(total, state.treninkIndex || 1)) : 1;
    const idx = state.treninkIndex;
    const key = String(idx).padStart(3, "0");
    const hasTake = Boolean(recorded[key]);

    $("trenink-progress-label").textContent = total > 0 ? `Věta ${idx} ${czechZ(total)} ${total}` : "Žádné věty";
    $("trenink-recorded-count").textContent = total > 0 ? `Nahráno: ${recCount}` : "";
    $("trenink-progress-fill").style.width = `${total > 0 ? Math.round((recCount / total) * 100) : 0}%`;
    $("trenink-sentence").textContent = (total > 0 && sentences[idx - 1]) ? sentences[idx - 1] : "—";

    const recBtn = $("trenink-record-btn");
    const playBtn = $("trenink-play-btn");
    const skipBtn = $("trenink-skip-btn");
    const reBtn = $("trenink-rerecord-btn");
    const liveBox = $("trenink-live-box");

    if (state.treninkRecording) {
      liveBox.hidden = false;
      recBtn.textContent = "Hotovo";
      recBtn.disabled = false;
      playBtn.disabled = true;
      skipBtn.disabled = true;
      reBtn.hidden = true;
    } else {
      liveBox.hidden = true;
      recBtn.textContent = "Nahrát";
      recBtn.disabled = total === 0;
      playBtn.disabled = !hasTake;
      skipBtn.disabled = total <= 1;
      reBtn.hidden = !(state.treninkLastRecordedIndex >= 1 && state.treninkLastRecordedIndex <= total);
    }

    const measBtn = $("trenink-measure-btn");
    const measHint = $("trenink-measure-hint");
    if (state.treninkMeasRunning) {
      measBtn.disabled = true;
      measBtn.textContent = `Měřím… ${state.treninkMeasDone} / ${state.treninkMeasTotal}`;
      measHint.hidden = true;
    } else if (recCount < 10) {
      measBtn.disabled = true;
      measBtn.textContent = "Změřit";
      measHint.hidden = false;
      measHint.textContent = "Nahraj aspoň 10 vět";
    } else if (state.doladeni && state.doladeni.stav === "bezi") {
      measBtn.disabled = true;
      measBtn.textContent = "Změřit";
      measHint.hidden = false;
      measHint.textContent = "Probíhá doladění modelu";
    } else {
      measBtn.disabled = false;
      measBtn.textContent = "Změřit";
      measHint.hidden = true;
    }
    setInlineResult($("trenink-measure-error"), state.treninkMeasError, state.treninkMeasError ? "error" : "");

    const meas = tr && tr.measurement;
    const werBox = $("trenink-wer-box");
    if (meas && Number(meas.n || 0) > 0) {
      werBox.hidden = false;
      const pct = (Number(meas.wer || 0) * 100).toFixed(1).replace(".", ",");
      $("trenink-wer-val").textContent = `${pct} %`;
      $("trenink-wer-label").textContent = `chyb ve slovech (WER) na ${meas.n} větách`;
      const worst = Array.isArray(meas.worst) ? meas.worst.slice(0, 5) : [];
      const worstWrap = $("trenink-worst-wrap");
      const worstList = $("trenink-worst-list");
      worstList.textContent = "";
      worstWrap.hidden = worst.length === 0;
      for (const item of worst) {
        const row = mk("div", "worst-row");
        const refEl = mk("div", "worst-ref");
        renderDiffTokens(refEl, item.ref_diff, item.ref);
        const hypEl = mk("div", "worst-hyp");
        renderDiffTokens(hypEl, item.hyp_diff, item.hyp);
        row.append(refEl, hypEl);
        worstList.appendChild(row);
      }
    } else {
      werBox.hidden = true;
    }

    const darWrap = $("dar-result-wrap");
    if (state.darLastPath) {
      darWrap.hidden = false;
      $("dar-result-text").textContent = `Balíček je ve složce Dokumenty: ${state.darLastFilename}`;
    } else {
      darWrap.hidden = true;
    }

    if (!state.diktatOpenId && !state.diktatBusyId) {
      renderDiktaty();
    } else {
      updateDiktatyCounts();
    }
    renderTreninkModels();
    renderDoladeni();
  }

  function formatDiktatyDate(iso) {
    const d = parseDateSafe(iso);
    if (!d) return "—";
    return `${formatDayHeading(d)} ${formatTime(iso)}`;
  }

  function formatDiktatyLength(sec) {
    const s = Math.max(0, Number(sec || 0));
    if (s < 60) return `${s.toFixed(1).replace(".", ",")} s`;
    const totalSec = Math.round(s);
    const mins = Math.floor(totalSec / 60);
    const rem = totalSec % 60;
    return rem > 0 ? `${mins} min ${rem} s` : `${mins} min`;
  }

  function formatDiktatyMinutes(seconds) {
    const mins = Math.max(0, Number(seconds || 0)) / 60;
    if (mins === 0 || Math.abs(mins - Math.round(mins)) < 0.05) {
      return String(Math.round(mins));
    }
    return mins.toFixed(1).replace(".", ",");
  }

  function updateDiktatyCounts() {
    const el = $("diktaty-counts");
    if (!el) return;
    const minText = formatDiktatyMinutes(state.diktatyVerifiedS);
    const nextText = `Ověřeno ${state.diktatyVerifiedCount} z ${state.diktatyTotal} · ${minText} min`;
    if (el.textContent !== nextText) el.textContent = nextText;
  }

  function renderDiktaty({ focusTextarea = false, focusToggleId = "", focusFallback = false } = {}) {
    const listEl = $("diktaty-list");
    const emptyEl = $("diktaty-empty");
    const moreWrap = $("diktaty-more-wrap");
    const moreBtn = $("diktaty-more-btn");
    if (!listEl || !emptyEl || !moreWrap) return;

    const cfg = state.data && state.data.config ? state.data.config : null;
    const saveOn = state.diktatyUkladatZvuk !== null
      ? Boolean(state.diktatyUkladatZvuk)
      : Boolean(cfg && cfg.trenink_ukladat_zvuk);
    updateDiktatyCounts();

    const sig = JSON.stringify({
      saveOn,
      total: state.diktatyTotal,
      vc: state.diktatyVerifiedCount,
      vs: state.diktatyVerifiedS,
      hasMore: state.diktatyHasMore,
      loading: state.diktatyLoading,
      openId: state.diktatOpenId,
      busyId: state.diktatBusyId,
      err: state.diktatError,
      items: state.diktatyItems.map((it) => [it.id, it.overeno, it.overeno_text, it.prepis]),
    });
    if (!focusTextarea && !focusToggleId && !focusFallback && sig === state.lastDiktatyRenderSig) return;
    state.lastDiktatyRenderSig = sig;

    const items = state.diktatyItems;
    if (items.length === 0) {
      listEl.textContent = "";
      emptyEl.hidden = false;
      emptyEl.textContent = saveOn
        ? "Zatím žádný uložený diktát."
        : "Zapni Ukládat zvuk diktátů. Diktáty se pak objeví tady.";
      moreWrap.hidden = true;
      if (focusFallback && $("toggle-save-dictation")) {
        requestAnimationFrame(() => $("toggle-save-dictation").focus());
      }
      return;
    }

    emptyEl.hidden = true;
    moreWrap.hidden = !state.diktatyHasMore;
    if (moreBtn) moreBtn.disabled = Boolean(state.diktatBusyId || state.diktatyLoading);

    listEl.textContent = "";
    let focusTarget = null;
    for (let idx = 0; idx < items.length; idx += 1) {
      const item = items[idx];
      const itemId = String(item.id || "");
      const isOpen = state.diktatOpenId === itemId;
      const isBusy = state.diktatBusyId === itemId;
      const isVerified = Boolean(item.overeno);

      const rowWrap = mk("div", isOpen ? "diktat-item is-open" : "diktat-item");
      const head = mk("div", "diktat-row");

      const toggleBtn = mk("button", "diktat-toggle");
      toggleBtn.type = "button";
      toggleBtn.setAttribute("aria-expanded", isOpen ? "true" : "false");

      const topLine = mk("div", "diktat-top");
      topLine.appendChild(
        mk("span", "diktat-meta", `${formatDiktatyDate(item.cas)} · ${formatDiktatyLength(item.audio_s)}`),
      );
      topLine.appendChild(
        mk("span", isVerified ? "diktat-chip is-verified" : "diktat-chip is-unverified", isVerified ? "Ověřeno" : "Neověřeno"),
      );

      const previewText = (isVerified && item.overeno_text) ? String(item.overeno_text) : String(item.prepis || "");
      const previewEl = mk("div", "diktat-preview", previewText);
      toggleBtn.append(topLine, previewEl);

      toggleBtn.addEventListener("click", () => {
        if (state.diktatBusyId) return;
        if (state.diktatOpenId === itemId) {
          state.diktatOpenId = "";
          state.diktatDraft = "";
          state.diktatError = "";
          renderDiktaty({ focusToggleId: itemId });
          return;
        }
        state.diktatOpenId = itemId;
        state.diktatDraft = previewText;
        state.diktatError = "";
        renderDiktaty({ focusTextarea: true });
      });

      const playBtn = mk("button", "btn btn-secondary diktat-play-btn", "Přehrát");
      playBtn.type = "button";
      playBtn.setAttribute("aria-label", `Přehrát diktát (${formatDiktatyDate(item.cas)})`);
      playBtn.disabled = Boolean(state.diktatBusyId);
      playBtn.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        const res = await apiCall("prehrat_diktat", itemId);
        if (!res.ok) showSnackbar(res.error || "Přehrání selhalo.", { isError: true });
      });

      head.append(toggleBtn, playBtn);
      rowWrap.appendChild(head);
      if (focusToggleId && focusToggleId === itemId) {
        focusTarget = toggleBtn;
      }

      if (isOpen) {
        const editor = mk("div", "diktat-editor");
        const taId = `diktat-textarea-${idx}`;
        const helpLabel = mk(
          "label",
          "diktat-help",
          "Přepiš přesně, co jsi řekl, včetně ‚ehm‘ a opakování. Neopravuj styl, jen chyby přepisu.",
        );
        helpLabel.setAttribute("for", taId);

        const ta = mk("textarea", "textarea diktat-textarea");
        ta.id = taId;
        ta.rows = 3;
        ta.maxLength = 5000;
        ta.disabled = isBusy;
        ta.value = state.diktatDraft;
        ta.addEventListener("input", () => {
          state.diktatDraft = ta.value;
          if (state.diktatError && ta.value.trim()) {
            state.diktatError = "";
            setInlineResult(errEl, "");
          }
        });

        const errEl = mk("div", "inline-result");
        errEl.setAttribute("role", "status");
        errEl.setAttribute("aria-live", "polite");
        if (state.diktatError) {
          setInlineResult(errEl, state.diktatError, "error");
        }

        const handleSave = async () => {
          const val = ta.value;
          if (!val.trim()) {
            state.diktatError = "Přepis nesmí být prázdný.";
            setInlineResult(errEl, state.diktatError, "error");
            ta.focus();
            return;
          }
          if (val.length > 5000) {
            state.diktatError = "Přepis může mít nejvýše 5000 znaků.";
            setInlineResult(errEl, state.diktatError, "error");
            ta.focus();
            return;
          }
          state.diktatySeq += 1;
          state.diktatBusyId = itemId;
          state.diktatError = "";
          renderDiktaty();
          const res = await apiCall("overit_diktat", itemId, val);
          state.diktatBusyId = "";
          if (!res.ok) {
            state.diktatError = res.error || "Uložení ověřeného přepisu selhalo.";
            renderDiktaty({ focusTextarea: true });
            return;
          }
          item.overeno = true;
          item.overeno_text = (res.entry && res.entry.overeno_text) ? String(res.entry.overeno_text) : val.trim();
          state.diktatyVerifiedCount = Number(res.verified_count ?? state.diktatyVerifiedCount);
          state.diktatyTotal = Number(res.total ?? state.diktatyTotal);
          state.diktatyVerifiedS = Number(res.verified_s ?? state.diktatyVerifiedS);
          state.diktatOpenId = "";
          state.diktatDraft = "";
          state.diktatError = "";
          state.lastDiktatyRenderSig = "";
          renderDiktaty({ focusToggleId: itemId });
          showSnackbar("Ověřeno");
        };

        editor.addEventListener("keydown", (ev) => {
          if (ev.key === "Escape") {
            ev.preventDefault();
            ev.stopPropagation();
            if (state.diktatBusyId) return;
            state.diktatOpenId = "";
            state.diktatDraft = "";
            state.diktatError = "";
            renderDiktaty({ focusToggleId: itemId });
          }
        });
        ta.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
            ev.preventDefault();
            if (!state.diktatBusyId) void handleSave();
          }
        });

        const actions = mk("div", "diktat-editor-actions");
        const saveBtn = mk("button", "btn btn-primary diktat-save-btn", "Uložit jako ověřené");
        saveBtn.type = "button";
        saveBtn.disabled = isBusy;
        saveBtn.addEventListener("click", () => { void handleSave(); });

        const delBtn = mk("button", "btn btn-destructive diktat-delete-btn", "Smazat nahrávku");
        delBtn.type = "button";
        delBtn.disabled = isBusy;
        delBtn.addEventListener("click", async () => {
          state.diktatySeq += 1;
          state.diktatBusyId = itemId;
          state.diktatError = "";
          renderDiktaty();
          const res = await apiCall("smazat_diktat_zvuk", itemId);
          state.diktatBusyId = "";
          if (!res.ok) {
            state.diktatError = res.error || "Smazání nahrávky selhalo.";
            renderDiktaty({ focusTextarea: true });
            return;
          }
          const nextItem = state.diktatyItems[idx + 1] || state.diktatyItems[idx - 1] || null;
          const nextFocusId = nextItem ? String(nextItem.id || "") : "";
          state.diktatyItems = state.diktatyItems.filter((it) => String(it.id || "") !== itemId);
          state.diktatyTotal = Number(res.total ?? state.diktatyItems.length);
          state.diktatyVerifiedCount = Number(res.verified_count ?? state.diktatyVerifiedCount);
          state.diktatyVerifiedS = Number(res.verified_s ?? state.diktatyVerifiedS);
          state.diktatOpenId = "";
          state.diktatDraft = "";
          state.diktatError = "";
          state.lastDiktatyRenderSig = "";
          const focusOpts = { focusToggleId: nextFocusId, focusFallback: !nextFocusId };
          renderDiktaty(focusOpts);
          if (state.diktatyHasMore && state.diktatyItems.length < 50) {
            void loadDiktaty(true, focusOpts);
          }
          showSnackbar("Nahrávka smazána");
        });

        actions.append(saveBtn, delBtn);

        let rawRefBox = null;
        if (isVerified && item.prepis && item.prepis !== previewText) {
          rawRefBox = mk("div", "diktat-raw-box");
          rawRefBox.appendChild(mk("span", "diktat-raw-text", `Původní přepis: ${item.prepis}`));
          const restoreBtn = mk("button", "link-btn diktat-reset-raw-btn", "Načíst původní přepis");
          restoreBtn.type = "button";
          restoreBtn.disabled = isBusy;
          restoreBtn.addEventListener("click", () => {
            ta.value = String(item.prepis || "");
            state.diktatDraft = ta.value;
            state.diktatError = "";
            setInlineResult(errEl, "");
            ta.focus();
          });
          rawRefBox.appendChild(restoreBtn);
        }

        editor.append(helpLabel, ta);
        if (rawRefBox) editor.appendChild(rawRefBox);
        editor.append(actions, errEl);
        rowWrap.appendChild(editor);
        if (focusTextarea) focusTarget = ta;
      }

      listEl.appendChild(rowWrap);
    }
    if (focusTarget) {
      requestAnimationFrame(() => focusTarget.focus());
    }
  }

  const DOLADENI_PHASE_LABELS = {
    priprava: "Příprava nahrávek",
    doladeni: "Doladění modelu",
    prevod: "Převod modelu",
    mereni: "Měření přesnosti",
  };

  function formatDoladeniWer(val) {
    if (typeof val !== "number" || !Number.isFinite(val)) return "–";
    return `${(val * 100).toFixed(1).replace(".", ",")} %`;
  }

  function formatDoladeniLoss(val) {
    if (typeof val !== "number" || !Number.isFinite(val)) return "";
    return Number(val).toFixed(3).replace(".", ",");
  }

  function formatDoladeniMeta(zacatek, konec) {
    const startMs = zacatek ? Date.parse(zacatek) : NaN;
    const endMs = konec ? Date.parse(konec) : NaN;
    const parts = [];
    const dateRef = Number.isFinite(endMs) ? new Date(endMs) : (Number.isFinite(startMs) ? new Date(startMs) : null);
    if (dateRef) {
      parts.push(`${dateRef.getDate()}. ${dateRef.getMonth() + 1}. ${dateRef.getFullYear()}`);
    }
    if (Number.isFinite(startMs) && Number.isFinite(endMs) && endMs >= startMs) {
      const sec = Math.round((endMs - startMs) / 1000);
      if (sec < 60) {
        parts.push("trvalo méně než minutu");
      } else {
        const mins = Math.max(1, Math.round(sec / 60));
        parts.push(`trvalo ${mins} min`);
      }
    }
    return parts.join(" · ");
  }

  function clearDoladeniLiveTimer() {
    clearTimeout(timers.doladeniLive);
    timers.doladeniLive = null;
    state.pendingDoladeniLiveMsg = "";
  }

  function updateDoladeniAriaLive(msg, { important = false } = {}) {
    if (state.activePage !== "trenink") return;
    const el = $("doladeni-aria-status");
    if (!el || !msg) return;
    if (!important && state.pendingDoladeniLiveMsg) {
      clearDoladeniLiveTimer();
    }
    if (msg === state.lastDoladeniLiveMsg) return;
    const now = Date.now();
    const elapsed = state.lastDoladeniLiveAt > 0 ? (now - state.lastDoladeniLiveAt) : Infinity;
    if (elapsed < 5000) {
      if (important && msg !== state.pendingDoladeniLiveMsg) {
        clearDoladeniLiveTimer();
        state.pendingDoladeniLiveMsg = msg;
        const delayMs = Math.max(50, 5000 - elapsed);
        timers.doladeniLive = setTimeout(() => {
          timers.doladeniLive = null;
          const pending = state.pendingDoladeniLiveMsg;
          state.pendingDoladeniLiveMsg = "";
          if (pending && state.activePage === "trenink") {
            updateDoladeniAriaLive(pending, { important: false });
          }
        }, delayMs);
      }
      return;
    }
    clearDoladeniLiveTimer();
    el.textContent = msg;
    state.lastDoladeniLiveMsg = msg;
    state.lastDoladeniLiveAt = now;
  }

  function stopDoladeniPolling() {
    clearInterval(timers.doladeniPoll);
    timers.doladeniPoll = null;
  }

  function syncDoladeniPolling() {
    const isRunning = Boolean(state.doladeni && state.doladeni.stav === "bezi");
    if (isRunning && state.activePage === "trenink") {
      if (!timers.doladeniPoll) {
        timers.doladeniPoll = setInterval(() => {
          if (document.hidden || state.activePage !== "trenink") return;
          void loadDoladeni();
        }, 2000);
      }
    } else {
      stopDoladeniPolling();
    }
  }

  function renderDoladeni() {
    const idleBox = $("doladeni-state-idle");
    const runBox = $("doladeni-state-running");
    const doneBox = $("doladeni-state-done");
    const errBox = $("doladeni-state-error");
    const stopBox = $("doladeni-state-stopped");
    if (!idleBox) return;

    for (const el of [idleBox, runBox, doneBox, errBox, stopBox]) {
      el.hidden = true;
    }

    const d = state.doladeni;
    const stav = (d && d.stav) ? String(d.stav) : "nic";

    if (stav === "nepodporovano" || (state.data && state.data.doladeni_available === false)) {
      idleBox.hidden = false;
      $("doladeni-idle-text").textContent = (d && d.chyba) || "Doladění modelu je zatím jen pro Windows s NVIDIA GPU";
      $("doladeni-start-btn").disabled = true;
      $("doladeni-start-btn").hidden = true;
      return;
    }

    $("doladeni-start-btn").hidden = false;
    if (stav === "nic") {
      idleBox.hidden = false;
      const nahrano = Number(d ? d.nahrano : (state.trenink ? state.trenink.recorded_count : 0)) || 0;
      const potreba = Number(d && d.potreba ? d.potreba : 50);
      const odhad = Number(d && d.odhad_min ? d.odhad_min : 8);
      if (nahrano < potreba) {
        $("doladeni-idle-text").textContent = `Nahraj aspoň ${potreba} vět. Zatím máš ${nahrano}.`;
        $("doladeni-start-btn").disabled = true;
      } else {
        $("doladeni-idle-text").textContent = `Potrvá asi ${odhad} minut. Mluvka mezitím normálně funguje.`;
        $("doladeni-start-btn").disabled = state.doladeniBusy || state.treninkMeasRunning;
      }
      return;
    }

    if (stav === "bezi") {
      runBox.hidden = false;
      const faze = String(d.faze || "priprava");
      const phaseText = DOLADENI_PHASE_LABELS[faze] || "Doladění modelu";
      $("doladeni-phase-label").textContent = phaseText;

      const krok = Number(d.krok || 0);
      const celkem = Number(d.celkem || 0);
      const track = $("doladeni-progress-track");
      const fill = $("doladeni-progress-fill");
      let progressLine = "";

      if (faze === "doladeni" && celkem > 0) {
        track.classList.remove("is-indeterminate");
        const pct = Math.max(0, Math.min(100, (krok / celkem) * 100));
        fill.style.width = `${pct.toFixed(1)}%`;
        progressLine = `Krok ${krok} z ${celkem}`;
        if (d.eta_s !== null && d.eta_s !== undefined && Number(d.eta_s) > 0) {
          const etaMin = Math.max(1, Math.ceil(Number(d.eta_s) / 60));
          progressLine += ` · zbývá asi ${etaMin} min`;
        }
      } else {
        track.classList.add("is-indeterminate");
        fill.style.width = "";
        if (faze === "priprava") progressLine = "Připravuji nahrané věty…";
        else if (faze === "doladeni") progressLine = "Spouštím trénink na GPU…";
        else if (faze === "prevod") progressLine = "Převádím a kvantizuji model…";
        else if (faze === "mereni") progressLine = "Měřím přesnost před a po doladění…";
      }
      $("doladeni-progress-text").textContent = progressLine;

      const lossEl = $("doladeni-loss-text");
      const lossStr = formatDoladeniLoss(d.loss);
      if (lossStr) {
        lossEl.hidden = false;
        lossEl.textContent = `Ztráta (loss): ${lossStr}`;
      } else {
        lossEl.hidden = true;
        lossEl.textContent = "";
      }

      $("doladeni-stop-btn").disabled = state.doladeniBusy;

      const lines = Array.isArray(d.log_tail) ? d.log_tail.slice(-8) : [];
      const logText = lines.length ? lines.join("\n") : "Čekám na výpis…";
      const logEl = $("doladeni-log");
      if (logEl.textContent !== logText) logEl.textContent = logText;

      updateDoladeniAriaLive(`${phaseText}. ${progressLine}`);
      return;
    }

    if (stav === "hotovo") {
      doneBox.hidden = false;
      const v = (d.vysledek && typeof d.vysledek === "object") ? d.vysledek : {};
      const pred = v.pred || {};
      const po = v.po || {};
      const nVety = Number(v.n_vety || 0);
      $("doladeni-row-vety-label").textContent = nVety > 0 ? `Tvůj hlas (${nVety} vět)` : "Tvůj hlas";
      $("doladeni-pred-vety").textContent = formatDoladeniWer(pred.vety_test);
      $("doladeni-po-vety").textContent = formatDoladeniWer(po.vety_test);
      $("doladeni-pred-fleurs").textContent = formatDoladeniWer(pred.fleurs);
      $("doladeni-po-fleurs").textContent = formatDoladeniWer(po.fleurs);

      const lepsi = Boolean(v.lepsi);
      const activeSoubor = (state.data && state.data.config && state.data.config.model_soubor)
        ? String(state.data.config.model_soubor)
        : "";
      const alreadyUsing = Boolean(lepsi && v.model_soubor && activeSoubor === v.model_soubor);
      const useBtn = $("doladeni-use-btn");
      const activeNote = $("doladeni-active-note");
      const rerunBtn = $("doladeni-rerun-btn");

      if (lepsi) {
        $("doladeni-verdict-text").textContent = "Doladěný model ti rozumí lépe.";
        useBtn.hidden = alreadyUsing;
        useBtn.disabled = state.doladeniBusy;
        activeNote.hidden = !alreadyUsing;
        rerunBtn.hidden = !alreadyUsing;
      } else {
        $("doladeni-verdict-text").textContent = "Doladění přesnost nezlepšilo. Zůstává současný model.";
        useBtn.hidden = true;
        activeNote.hidden = true;
        rerunBtn.hidden = false;
      }
      rerunBtn.disabled = state.doladeniBusy || state.treninkMeasRunning;

      const metaEl = $("doladeni-done-meta");
      const metaStr = formatDoladeniMeta(d.zacatek, d.konec);
      metaEl.hidden = !metaStr;
      metaEl.textContent = metaStr;

      updateDoladeniAriaLive(`Doladění hotovo. ${$("doladeni-verdict-text").textContent}`, { important: true });
      return;
    }

    if (stav === "chyba") {
      errBox.hidden = false;
      const errMsg = String(d.chyba || "Doladění selhalo.");
      $("doladeni-error-text").textContent = errMsg;
      $("doladeni-retry-btn").disabled = state.doladeniBusy || state.treninkMeasRunning;
      const lines = Array.isArray(d.log_tail) ? d.log_tail.slice(-8) : [];
      $("doladeni-error-details").hidden = lines.length === 0;
      $("doladeni-error-log").textContent = lines.join("\n");
      updateDoladeniAriaLive(`Chyba doladění: ${errMsg}`, { important: true });
      return;
    }

    if (stav === "zastaveno") {
      stopBox.hidden = false;
      $("doladeni-stopped-rerun-btn").disabled = state.doladeniBusy || state.treninkMeasRunning;
      updateDoladeniAriaLive("Doladění zastaveno.", { important: true });
    }
  }

  async function loadDoladeni() {
    if (state.doladeniInFlight || state.doladeniBusy) return;
    state.doladeniInFlight = true;
    const seq = ++state.doladeniSeq;
    const prevStav = state.doladeni ? state.doladeni.stav : "";
    let res = null;
    try {
      res = await apiCall("doladeni_stav");
    } finally {
      state.doladeniInFlight = false;
    }
    if (!res || !res.ok) return;
    if (seq !== state.doladeniSeq || state.doladeniBusy) return;
    state.doladeni = res;
    renderTrenink();
    syncDoladeniPolling();
    if (res.stav === "hotovo" && prevStav && prevStav !== "hotovo") {
      void loadTreninkModels();
    }
  }

  async function startDoladeni() {
    if (state.doladeniBusy) return;
    clearDoladeniLiveTimer();
    state.doladeniBusy = true;
    const seq = ++state.doladeniSeq;
    renderDoladeni();
    let res = null;
    try {
      res = await apiCall("doladeni_spustit");
    } finally {
      state.doladeniBusy = false;
    }
    if (!res || !res.ok) {
      showSnackbar((res && res.error) || "Doladění se nepodařilo spustit.", { isError: true });
      await loadDoladeni();
      return;
    }
    if (seq === state.doladeniSeq) {
      state.doladeni = res;
    }
    renderTrenink();
    syncDoladeniPolling();
  }

  async function stopDoladeni() {
    if (state.doladeniBusy) return;
    state.doladeniBusy = true;
    const seq = ++state.doladeniSeq;
    renderDoladeni();
    let res = null;
    try {
      res = await apiCall("doladeni_zastavit");
    } finally {
      state.doladeniBusy = false;
    }
    if (!res || !res.ok) {
      showSnackbar((res && res.error) || "Doladění se nepodařilo zastavit.", { isError: true });
      await loadDoladeni();
      return;
    }
    if (seq === state.doladeniSeq) {
      state.doladeni = res;
    }
    renderTrenink();
    syncDoladeniPolling();
  }

  async function useDoladeniModel() {
    if (state.doladeniBusy) return;
    state.doladeniBusy = true;
    ++state.doladeniSeq;
    renderDoladeni();
    let res = null;
    try {
      res = await apiCall("doladeni_pouzit");
    } finally {
      state.doladeniBusy = false;
    }
    if (!res || !res.ok) {
      showSnackbar((res && res.error) || "Model se nepodařilo aktivovat.", { isError: true });
      renderDoladeni();
      return;
    }
    state.treninkModels = res;
    if (res.config && state.data) {
      state.data = { ...state.data, config: res.config };
    }
    renderTrenink();
    showSnackbar("Doladěný model nastaven");
  }

  function renderTreninkModels() {
    const listEl = $("trenink-models-list");
    if (!listEl) return;  // výběr modelu se přesunul na stránku Model
    const emptyEl = $("trenink-models-empty");
    const statusEl = $("trenink-model-status");
    const modelsData = state.treninkModels;
    const models = (modelsData && Array.isArray(modelsData.models)) ? modelsData.models : [];

    if (state.treninkModelError) {
      statusEl.hidden = false;
      setInlineResult(statusEl, state.treninkModelError, "error");
    } else {
      statusEl.hidden = true;
      setInlineResult(statusEl, "", "");
    }

    if (models.length === 0) {
      listEl.textContent = "";
      listEl.hidden = true;
      emptyEl.hidden = false;
      state.lastModelsRenderKey = "";
      state.lastModelsStructKey = "";
      return;
    }

    emptyEl.hidden = true;
    listEl.hidden = false;
    const key = JSON.stringify(models.map((m) => [m.soubor, m.nazev, Boolean(m.vybrany), m.wer_text || ""]));
    if (key === state.lastModelsRenderKey) return;
    state.lastModelsRenderKey = key;

    const structKey = JSON.stringify(models.map((m) => [m.soubor, m.nazev, m.wer_text || ""]));
    if (structKey === state.lastModelsStructKey && listEl.children.length === models.length) {
      models.forEach((item, i) => {
        const label = listEl.children[i];
        const selected = Boolean(item.vybrany);
        label.classList.toggle("is-selected", selected);
        const radio = label.querySelector('input[type="radio"]');
        if (radio && radio.checked !== selected) {
          radio.checked = selected;
        }
      });
      return;
    }
    state.lastModelsStructKey = structKey;

    listEl.textContent = "";
    for (const item of models) {
      const label = mk("label", item.vybrany ? "model-radio-option is-selected" : "model-radio-option");
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "osobni-model";
      radio.className = "model-radio-input";
      radio.value = String(item.soubor || "");
      radio.checked = Boolean(item.vybrany);

      const body = mk("span", "model-radio-body");
      const nameEl = mk("span", "model-radio-name", String(item.nazev || item.soubor || ""));
      body.appendChild(nameEl);
      if (item.wer_text) {
        body.appendChild(mk("span", "model-radio-wer", String(item.wer_text)));
      }
      label.append(radio, body);
      listEl.appendChild(label);
    }
  }

  async function selectTreninkModel(soubor) {
    if (!soubor) return;
    const seq = ++state.treninkModelsSeq;
    state.treninkSelectingModel = true;
    state.treninkModelError = "";
    try {
      if (state.treninkModels && Array.isArray(state.treninkModels.models)) {
        state.treninkModels = {
          ...state.treninkModels,
          aktivni: soubor,
          models: state.treninkModels.models.map((m) => ({ ...m, vybrany: m.soubor === soubor })),
        };
        renderTreninkModels();
      }
      const res = await apiCall("vybrat_model", soubor);
      if (seq !== state.treninkModelsSeq) return;
      if (!res.ok) {
        state.treninkModelError = res.error || "Model se nepodařilo přepnout.";
        await loadTreninkModels();
        return;
      }
      state.treninkModels = res;
      if (res.config && state.data) {
        state.data = { ...state.data, config: res.config };
      }
      renderTreninkModels();
      renderDoladeni();
    } finally {
      if (seq === state.treninkModelsSeq) {
        state.treninkSelectingModel = false;
      }
    }
  }

  function renderAll({ skipPairsRender = false, skipSnippetsRender = false } = {}) {
    if (!state.data) return;
    renderSidebarStatus();
    renderOverview();
    renderDictionary(skipPairsRender);
    renderSnippets(skipSnippetsRender);
    renderTrenink();
    renderAi();
    renderSettings();
    renderPrivacy();
  }

  async function savePatch(patch) {
    const res = await apiCall("save_settings", patch);
    if (!res.ok) {
      if (state.data) {
        const refreshed = await apiCall("get_state");
        if (refreshed.ok) state.data = refreshed;
      }
      renderAll({ skipPairsRender: isEditingPairs(), skipSnippetsRender: isEditingSnippets() });
      showSnackbar(res.error || "Uložení se nepodařilo.", { isError: true });
      return false;
    }
    state.data = res;
    if (res.config && Object.prototype.hasOwnProperty.call(res.config, "trenink_ukladat_zvuk")) {
      state.diktatyUkladatZvuk = Boolean(res.config.trenink_ukladat_zvuk);
    }
    if (patch && Object.prototype.hasOwnProperty.call(patch, "trenink_ukladat_zvuk")) {
      state.diktatySeq += 1;
    }
    renderAll({ skipPairsRender: isEditingPairs(), skipSnippetsRender: isEditingSnippets() });
    if (patch && Object.prototype.hasOwnProperty.call(patch, "slovnik")) void loadPamet();
    return true;
  }

  async function flushCorrections(force = false) {
    clearTimeout(timers.corrections);
    timers.corrections = null;
    const cleanPairs = [];
    for (const [k, v] of state.corrections) {
      const a = String(k || "").trim();
      const b = String(v || "").trim();
      if (a) cleanPairs.push([a, b]);
    }
    const json = JSON.stringify(cleanPairs);
    if (!force && json === state.savedCorrectionsJson) return;
    state.savedCorrectionsJson = json;
    state.lastRenderedPairsJson = json;
    await savePatch({ slovnik: { opravy: cleanPairs } });
  }
  blurFlushers.corrections = flushCorrections;

  async function flushSnippets(force = false) {
    clearTimeout(timers.snippets);
    timers.snippets = null;
    const cleanPairs = [];
    for (const [k, v] of state.snippets) {
      const a = String(k || "").trim();
      if (a) cleanPairs.push([a, String(v || "")]);
    }
    const json = JSON.stringify(cleanPairs);
    if (!force && json === state.savedSnippetsJson) return;
    state.savedSnippetsJson = json;
    state.lastRenderedSnippetsJson = json;
    await savePatch({ zkratky: cleanPairs });
  }
  blurFlushers.snippets = flushSnippets;

  async function flushWebhook() {
    clearTimeout(timers.webhook);
    timers.webhook = null;
    const raw = $("input-webhook").value.trim();
    if (raw && !raw.startsWith("https://")) {
      state.webhookInvalid = true;
      setInlineResult($("webhook-error"), "Adresa webhooku musí začínat https://.", "error");
      return false;
    }
    state.webhookInvalid = false;
    setInlineResult($("webhook-error"), "");
    if (raw === state.lastServerWebhook) return true;
    const ok = await savePatch({ kanal_webhook: raw });
    if (ok) state.lastServerWebhook = raw;
    return ok;
  }
  blurFlushers.webhook = flushWebhook;

  async function loadRecent() {
    const res = await apiCall("get_history", "", 0, 5);
    if (!res.ok) return;
    state.recentItems = Array.isArray(res.items) ? res.items : [];
    renderRecent();
  }

  async function loadHistory(reset = false) {
    const offset = reset ? 0 : state.historyItems.length;
    const res = await apiCall("get_history", state.historyQuery, offset, 100);
    if (!res.ok) { showSnackbar(res.error || "Historii se nepodařilo načíst.", { isError: true }); return; }
    const batch = Array.isArray(res.items) ? res.items : [];
    state.historyItems = reset ? batch : state.historyItems.concat(batch);
    state.historyHasMore = Boolean(res.has_more);
    renderHistoryList();
  }

  async function loadPamet() {
    const res = await apiCall("get_pamet");
    if (!res.ok) return;
    state.pametItems = Array.isArray(res.items) ? res.items : [];
    renderPamet();
  }

  async function loadTreninkModels() {
    const seq = ++state.treninkModelsSeq;
    const res = await apiCall("modely");
    if (!res.ok || seq !== state.treninkModelsSeq) return;
    state.treninkModels = res;
    renderTreninkModels();
  }

  async function loadDiktaty(reset = false, renderOpts = {}) {
    if (!reset && state.diktatyLoading) return;
    const seq = ++state.diktatySeq;
    const offset = reset ? 0 : state.diktatyItems.length;
    const limit = reset ? Math.min(500, Math.max(50, state.diktatyItems.length)) : 50;
    state.diktatyInFlight += 1;
    state.diktatyLoading = true;
    const moreBtn = $("diktaty-more-btn");
    if (moreBtn) moreBtn.disabled = true;
    let res;
    try {
      res = await apiCall("diktaty_k_overeni", offset, limit);
    } finally {
      state.diktatyInFlight = Math.max(0, state.diktatyInFlight - 1);
      state.diktatyLoading = state.diktatyInFlight > 0;
    }
    if (seq !== state.diktatySeq || !res || !res.ok) {
      if (moreBtn && !state.diktatyLoading && !state.diktatBusyId) moreBtn.disabled = false;
      return;
    }
    if (Object.prototype.hasOwnProperty.call(res, "ukladat_zvuk")) {
      state.diktatyUkladatZvuk = Boolean(res.ukladat_zvuk);
    }
    state.diktatyTotal = Number(res.total || 0);
    state.diktatyVerifiedCount = Number(res.verified_count || 0);
    state.diktatyVerifiedS = Number(res.verified_s || 0);
    state.diktatyHasMore = Boolean(res.has_more);
    if (state.diktatOpenId || state.diktatBusyId) {
      updateDiktatyCounts();
      if (moreBtn && !state.diktatyLoading && !state.diktatBusyId) moreBtn.disabled = false;
      return;
    }
    const batch = Array.isArray(res.items) ? res.items : [];
    if (reset) {
      state.diktatyItems = batch;
    } else {
      const seen = new Set(state.diktatyItems.map((it) => String(it.id || "")));
      const next = state.diktatyItems.slice();
      for (const it of batch) {
        const uid = String((it && it.id) || "");
        if (uid && !seen.has(uid)) {
          seen.add(uid);
          next.push(it);
        }
      }
      state.diktatyItems = next;
    }
    state.lastDiktatyRenderSig = "";
    renderDiktaty(renderOpts);
  }

  async function loadTrenink({ jumpToNext = false } = {}) {
    const seq = ++state.treninkModelsSeq;
    const [res, modelsRes] = await Promise.all([
      apiCall("get_trenink_state"),
      apiCall("modely"),
      loadDiktaty(true),
    ]);
    if (modelsRes && modelsRes.ok && seq === state.treninkModelsSeq) {
      state.treninkModels = modelsRes;
    }
    if (!res.ok) {
      renderTreninkModels();
      return;
    }
    state.trenink = res;
    if (jumpToNext || !state.treninkIndex) {
      state.treninkIndex = Number(res.next_index || 1);
    }
    const m = res.measuring;
    if (m && typeof m === "object") {
      state.treninkMeasRunning = Boolean(m.running);
      state.treninkMeasDone = Number(m.done || 0);
      state.treninkMeasTotal = Number(m.total || 0);
      state.treninkMeasError = m.error ? String(m.error) : "";
      if (m.running && !timers.measPoll) startMeasPolling();
    }
    renderTrenink();
    if (state.activePage === "trenink") {
      void loadDoladeni();
    } else {
      syncDoladeniPolling();
    }
  }

  async function cancelTreninkRecording() {
    if (!state.treninkRecording) return;
    state.treninkRecording = false;
    stopLevelPolling();
    await apiCall("trenink_cancel");
    renderTrenink();
  }

  function switchPage(page) {
    if (!page || page === state.activePage) return;
    if (state.activePage === "trenink") {
      if (state.treninkRecording) void cancelTreninkRecording();
      stopDoladeniPolling();
      clearDoladeniLiveTimer();
    }
    if (state.activePage === "model" && window.MluvkaModel) window.MluvkaModel.hide();
    state.activePage = page;
    for (const btn of document.querySelectorAll(".nav-item")) {
      const isCurrent = btn.dataset.page === page;
      btn.classList.toggle("active", isCurrent);
      if (isCurrent) btn.setAttribute("aria-current", "page");
      else btn.removeAttribute("aria-current");
    }
    for (const sec of document.querySelectorAll(".page")) {
      sec.hidden = sec.id !== `page-${page}`;
    }
    $("content-area").scrollTop = 0;
    if (page === "historie") void loadHistory(true);
    else if (page === "prehled") void loadRecent();
    else if (page === "slovnik") void loadPamet();
    else if (page === "trenink") void loadTrenink({ jumpToNext: !state.trenink });
    else if (page === "model" && window.MluvkaModel) window.MluvkaModel.show();
  }

  window.MluvkaApp = { apiCall, switchPage, showSnackbar: (t) => showSnackbar(t) };

  function toggleDialog(id, open) {
    const dlg = $(id);
    if (!dlg) return;
    if (open) { if (!dlg.open && dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", ""); }
    else { if (dlg.open && dlg.close) dlg.close(); else dlg.removeAttribute("open"); }
  }

  function updateDarSubmitState() {
    const ready = $("dar-chk-1").checked && $("dar-chk-2").checked && $("dar-chk-3").checked;
    $("dar-submit-btn").disabled = !ready;
  }

  function toggleDarDialog(open) {
    if (open) {
      $("dar-chk-1").checked = false;
      $("dar-chk-2").checked = false;
      $("dar-chk-3").checked = false;
      $("dar-chk-diktaty").checked = false;
      setInlineResult($("dar-dialog-error"), "");
      $("dar-submit-btn").textContent = "Vytvořit balíček";
      updateDarSubmitState();
    }
    toggleDialog("dar-dialog", open);
  }

  function bindSwitch(id, onToggle) {
    $(id).addEventListener("click", () => {
      const next = $(id).getAttribute("aria-checked") !== "true";
      void onToggle(next);
    });
  }

  async function runWithBusy(btn, resultEl, busyLabel, idleLabel, method, okText) {
    btn.disabled = true;
    btn.textContent = busyLabel;
    if (resultEl) setInlineResult(resultEl, "");
    const res = await apiCall(method);
    btn.disabled = false;
    btn.textContent = idleLabel;
    if (resultEl) setInlineResult(resultEl, res.ok ? (okText || "Hotovo.") : (res.error || "Akce selhala."), res.ok ? "ok" : "error");
    return res;
  }

  function bindEvents() {
    const navBtns = Array.from(document.querySelectorAll(".nav-item"));
    navBtns.forEach((btn, idx) => {
      btn.addEventListener("click", () => switchPage(btn.dataset.page));
      btn.addEventListener("keydown", (ev) => {
        if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
          ev.preventDefault();
          const next = navBtns[(idx + (ev.key === "ArrowDown" ? 1 : -1) + navBtns.length) % navBtns.length];
          next.focus();
          switchPage(next.dataset.page);
        } else if (ev.key === "Home" || ev.key === "End") {
          ev.preventDefault();
          const target = navBtns[ev.key === "Home" ? 0 : navBtns.length - 1];
          target.focus();
          switchPage(target.dataset.page);
        }
      });
    });

    document.addEventListener("keydown", (ev) => {
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "f") {
        ev.preventDefault();
        switchPage("historie");
        $("history-search").focus();
      } else if (ev.key === "Escape") {
        if (state.activePage === "trenink" && state.treninkRecording) {
          ev.preventDefault();
          void cancelTreninkRecording();
        } else if ($("dar-dialog").open || $("dar-dialog").hasAttribute("open")) {
          toggleDarDialog(false);
        } else if ($("confirm-dialog").open || $("confirm-dialog").hasAttribute("open")) {
          toggleDialog("confirm-dialog", false);
        }
      }
    });

    $("start-app-btn").addEventListener("click", async () => {
      const btn = $("start-app-btn");
      btn.disabled = true;
      const res = await apiCall("start_app");
      btn.disabled = false;
      if (res.ok) {
        if (state.data) state.data.app_running = true;
        renderSidebarStatus();
        showSnackbar("Mluvka spuštěna");
      } else showSnackbar(res.error || "Spuštění se nepodařilo.", { isError: true });
    });

    $("history-search").addEventListener("input", () => {
      state.historyQuery = $("history-search").value;
      scheduleDebounced("historySearch", () => loadHistory(true), 220);
    });

    $("history-more-btn").addEventListener("click", () => { void loadHistory(false); });
    $("clear-history-btn").addEventListener("click", () => toggleDialog("confirm-dialog", true));
    $("privacy-clear-btn").addEventListener("click", () => toggleDialog("confirm-dialog", true));
    $("confirm-cancel-btn").addEventListener("click", () => toggleDialog("confirm-dialog", false));
    $("confirm-dialog").addEventListener("click", (ev) => {
      if (ev.target === $("confirm-dialog")) toggleDialog("confirm-dialog", false);
    });
    $("confirm-delete-btn").addEventListener("click", async () => {
      toggleDialog("confirm-dialog", false);
      const res = await apiCall("clear_history");
      if (!res.ok) { showSnackbar(res.error || "Smazání historie selhalo.", { isError: true }); return; }
      state.diktatySeq += 1;
      state.recentItems = [];
      state.historyItems = [];
      state.historyHasMore = false;
      state.lastRecentJson = null;
      state.diktatyItems = [];
      state.diktatyTotal = 0;
      state.diktatyVerifiedCount = 0;
      state.diktatyVerifiedS = 0;
      state.diktatyHasMore = false;
      state.diktatOpenId = "";
      state.diktatDraft = "";
      state.diktatError = "";
      state.lastDiktatyRenderSig = "";
      renderDiktaty();
      if (res.stats && state.data) state.data.stats = res.stats;
      renderOverview();
      renderRecent();
      renderHistoryList();
      showSnackbar("Historie smazána");
    });

    $("copy-import-prompt-btn").addEventListener("click", async () => {
      const res = await apiCall("copy_slovnik_prompt");
      if (res.ok) showSnackbar("Prompt zkopírován");
      else showSnackbar(res.error || "Kopírování selhalo.", { isError: true });
    });

    $("import-load-btn").addEventListener("click", async () => {
      setInlineResult($("import-error"), "");
      const res = await apiCall("preview_slovnik_import", $("import-json-input").value);
      if (!res.ok) {
        state.importPreview = { slova: [], opravy: [], existing_count: 0 };
        renderImportPreview();
        setInlineResult($("import-error"), res.error || "Odpověď se nepodařilo přečíst. Zkontroluj, že je to JSON ve tvaru z promptu.", "error");
        return;
      }
      state.importPreview = {
        slova: res.slova || [],
        opravy: res.opravy || [],
        existing_count: Number(res.existing_count || 0),
      };
      renderImportPreview();
      // Tlačítko „Přidat vše“ je nad náhledem; posuň ho do zorného pole, ať je jasné, že import ještě není hotový.
      $("import-preview").scrollIntoView({ block: "start" });
      $("import-apply-btn").focus({ preventScroll: true });
    });

    $("import-apply-btn").addEventListener("click", async () => {
      const btn = $("import-apply-btn");
      btn.disabled = true;
      const res = await apiCall("apply_slovnik_import", {
        slova: state.importPreview.slova || [],
        opravy: state.importPreview.opravy || [],
      });
      if (!res.ok) {
        btn.disabled = false;
        showSnackbar(res.error || "Import slovníku selhal.", { isError: true });
        return;
      }
      state.importPreview = { slova: [], opravy: [], existing_count: 0 };
      $("import-json-input").value = "";
      setInlineResult($("import-error"), "");
      if (Array.isArray(res.pamet)) state.pametItems = res.pamet;
      state.data = res;
      renderAll({ skipPairsRender: false, skipSnippetsRender: isEditingSnippets() });
      showSnackbar(`Přidáno slov: ${res.added_slova || 0}, oprav: ${res.added_opravy || 0}`);
    });

    const addWord = async () => {
      const w = $("word-input").value.trim();
      if (!w || !state.data) return;
      const cur = Array.isArray(state.data.config.slovnik.slova) ? state.data.config.slovnik.slova : [];
      if (cur.some((x) => x.toLowerCase() === w.toLowerCase())) {
        $("word-input").value = "";
        showSnackbar("Slovo už ve slovníku je");
        return;
      }
      $("word-input").value = "";
      await savePatch({ slovnik: { slova: [...cur, w] } });
    };
    $("add-word-btn").addEventListener("click", () => { void addWord(); });
    $("word-input").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); void addWord(); }
    });
    bindSwitch("toggle-dict-prompt", (next) => savePatch({ slovnik_do_promptu: next }));

    $("add-correction-btn").addEventListener("click", () => {
      state.corrections.push(["", ""]);
      renderCorrectionRows();
      const inputs = $("corrections-list").querySelectorAll("input");
      if (inputs.length >= 2) inputs[inputs.length - 2].focus();
    });
    $("corrections-list").addEventListener("focusout", () => {
      setTimeout(() => {
        if (!$("corrections-list").contains(document.activeElement) && timers.corrections) {
          void flushCorrections();
        }
      }, 0);
    });

    $("add-snippet-btn").addEventListener("click", () => {
      state.snippets.push(["", ""]);
      renderSnippetCards();
      const inputs = $("snippets-list").querySelectorAll("input");
      if (inputs.length >= 1) inputs[inputs.length - 1].focus();
    });
    $("snippets-list").addEventListener("focusout", () => {
      setTimeout(() => {
        if (!$("snippets-list").contains(document.activeElement) && timers.snippets) {
          void flushSnippets();
        }
      }, 0);
    });

    $("clear-pamet-btn").addEventListener("click", async () => {
      const res = await apiCall("clear_pamet");
      if (!res.ok) { showSnackbar(res.error || "Vymazání paměti selhalo.", { isError: true }); return; }
      state.pametItems = [];
      renderPamet();
      showSnackbar("Paměť chyb vymazána");
    });

    $("trenink-record-btn").addEventListener("click", async () => {
      if (state.treninkRecording) {
        const savedIdx = state.treninkIndex;
        state.treninkRecording = false;
        stopLevelPolling();
        $("trenink-record-btn").disabled = true;
        const res = await apiCall("trenink_stop");
        if (!res.ok) {
          renderTrenink();
          showSnackbar(res.error || "Uložení nahrávky selhalo.", { isError: true });
          return;
        }
        state.treninkLastRecordedIndex = savedIdx;
        if (state.trenink && res.trenink) {
          state.trenink.recorded = res.trenink.recorded || state.trenink.recorded;
          state.trenink.recorded_count = Number(res.trenink.recorded_count || 0);
          state.trenink.next_index = Number(res.trenink.next_index || savedIdx);
        }
        const total = state.trenink ? Number(state.trenink.total || 0) : 0;
        if (total > 1 && savedIdx < total) state.treninkIndex = savedIdx + 1;
        else if (total > 1 && res.trenink && Number(res.trenink.next_index || 0) >= 1) state.treninkIndex = Number(res.trenink.next_index);
        renderTrenink();
        if (state.doladeni && state.doladeni.stav === "nic") void loadDoladeni();
        showSnackbar("Uloženo");
      } else {
        $("trenink-record-btn").disabled = true;
        const res = await apiCall("trenink_start", state.treninkIndex);
        if (!res.ok) {
          renderTrenink();
          showSnackbar(res.error || "Nahrávání se nepodařilo spustit.", { isError: true });
          return;
        }
        state.treninkRecording = true;
        $("trenink-timer").textContent = "0:00";
        renderTrenink();
        startLevelPolling();
      }
    });

    $("trenink-play-btn").addEventListener("click", async () => {
      const res = await apiCall("trenink_play", state.treninkIndex);
      if (!res.ok) showSnackbar(res.error || "Přehrání selhalo.", { isError: true });
    });

    $("trenink-skip-btn").addEventListener("click", () => {
      const total = state.trenink ? Number(state.trenink.total || 0) : 0;
      if (total <= 1) return;
      state.treninkIndex = (state.treninkIndex % total) + 1;
      renderTrenink();
    });

    $("trenink-rerecord-btn").addEventListener("click", async () => {
      if (!state.treninkLastRecordedIndex || state.treninkRecording) return;
      state.treninkIndex = state.treninkLastRecordedIndex;
      $("trenink-record-btn").disabled = true;
      const res = await apiCall("trenink_start", state.treninkIndex);
      if (!res.ok) {
        renderTrenink();
        showSnackbar(res.error || "Nahrávání se nepodařilo spustit.", { isError: true });
        return;
      }
      state.treninkRecording = true;
      $("trenink-timer").textContent = "0:00";
      renderTrenink();
      startLevelPolling();
    });

    $("trenink-measure-btn").addEventListener("click", async () => {
      state.treninkMeasError = "";
      const res = await apiCall("zmerit");
      if (!res.ok) {
        state.treninkMeasError = res.error || "Měření se nepodařilo spustit.";
        renderTrenink();
        return;
      }
      state.treninkMeasRunning = true;
      state.treninkMeasDone = Number(res.done || 0);
      state.treninkMeasTotal = Number(res.total || (state.trenink && state.trenink.recorded_count) || 0);
      renderTrenink();
      startMeasPolling();
    });

    bindSwitch("toggle-save-dictation", (next) => savePatch({ trenink_ukladat_zvuk: next }));
    $("diktaty-more-btn").addEventListener("click", () => { void loadDiktaty(false); });

    $("open-dar-btn").addEventListener("click", () => toggleDarDialog(true));
    $("dar-cancel-btn").addEventListener("click", () => toggleDarDialog(false));
    $("dar-dialog").addEventListener("click", (ev) => {
      if (ev.target === $("dar-dialog")) toggleDarDialog(false);
    });
    for (const id of ["dar-chk-1", "dar-chk-2", "dar-chk-3"]) {
      $(id).addEventListener("change", updateDarSubmitState);
    }
    $("dar-submit-btn").addEventListener("click", async () => {
      const souhlasy = [$("dar-chk-1").checked, $("dar-chk-2").checked, $("dar-chk-3").checked];
      const includeDiktaty = $("dar-chk-diktaty").checked;
      const btn = $("dar-submit-btn");
      btn.disabled = true;
      btn.textContent = "Vytvářím…";
      setInlineResult($("dar-dialog-error"), "");
      const res = await apiCall("vytvor_dar", includeDiktaty, souhlasy);
      btn.textContent = "Vytvořit balíček";
      updateDarSubmitState();
      if (!res.ok) {
        setInlineResult($("dar-dialog-error"), res.error || "Balíček se nepodařilo vytvořit.", "error");
        return;
      }
      state.darLastPath = res.path || "";
      state.darLastFilename = res.filename || "";
      toggleDarDialog(false);
      renderTrenink();
      showSnackbar("Balíček vytvořen");
    });

    $("open-dar-folder-btn").addEventListener("click", async () => {
      if (!state.darLastPath) return;
      const res = await apiCall("otevrit_slozku", state.darLastPath);
      if (!res.ok) showSnackbar(res.error || "Složku se nepodařilo otevřít.", { isError: true });
    });

    bindSwitch("toggle-ai-cleanup", (next) => savePatch({ ai_uprava: next }));
    const saveApiKey = async () => {
      const inp = $("api-key-input");
      const val = inp.value.trim();
      const resEl = $("api-key-result");
      if (!val) { setInlineResult(resEl, "Zadej API klíč.", "error"); return; }
      const res = await apiCall("set_api_key", val);
      if (!res.ok) {
        const refreshed = await apiCall("get_state");
        if (refreshed.ok) state.data = refreshed;
        renderSidebarStatus();
        setInlineResult(resEl, res.error || "Uložení klíče selhalo.", "error");
        return;
      }
      inp.value = "";
      state.data = res;
      renderAll({ skipPairsRender: isEditingPairs(), skipSnippetsRender: isEditingSnippets() });
      setInlineResult(resEl, "API klíč uložen.", "ok");
    };
    $("save-api-key-btn").addEventListener("click", () => { void saveApiKey(); });
    $("api-key-input").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); void saveApiKey(); }
    });
    $("test-api-key-btn").addEventListener("click", () => {
      void runWithBusy($("test-api-key-btn"), $("api-key-result"), "Ověřuji…", "Ověřit", "test_api_key", "Klíč funguje.");
    });
    $("remove-api-key-btn").addEventListener("click", async () => {
      const res = await apiCall("set_api_key", "");
      if (!res.ok) {
        const refreshed = await apiCall("get_state");
        if (refreshed.ok) state.data = refreshed;
        renderSidebarStatus();
        setInlineResult($("api-key-result"), res.error || "Odebrání selhalo.", "error");
        return;
      }
      state.data = res;
      renderAll({ skipPairsRender: isEditingPairs(), skipSnippetsRender: isEditingSnippets() });
      setInlineResult($("api-key-result"), "API klíč odebrán.");
    });
    $("open-console-btn").addEventListener("click", () => { void apiCall("open_url", "https://console.anthropic.com"); });

    $("sel-vk").addEventListener("change", () => { void savePatch({ klavesa_vk: Number($("sel-vk").value) }); });
    $("sel-mic").addEventListener("change", () => { void savePatch({ mikrofon: $("sel-mic").value || "" }); });
    bindSwitch("toggle-sounds", (next) => savePatch({ zvuky: next }));
    bindSwitch("toggle-learn-edits", (next) => savePatch({ uceni_z_oprav: next }));
    bindSwitch("toggle-autostart", (next) => savePatch({ autostart: next }));
    $("input-webhook").addEventListener("input", () => { scheduleDebounced("webhook", flushWebhook); });
    $("input-webhook").addEventListener("focusout", () => { if (timers.webhook) void flushWebhook(); });
    bindSwitch("toggle-channel", (next) => savePatch({ kanal_zapnuto: next }));
    $("test-webhook-btn").addEventListener("click", async () => {
      if (!(await flushWebhook())) return;
      await runWithBusy($("test-webhook-btn"), $("webhook-test-result"), "Posílám…", "Poslat test", "test_webhook", "Zpráva odeslána.");
    });
    bindSwitch("toggle-history", (next) => savePatch({ historie: next }));
    $("sel-history-days").addEventListener("change", () => { void savePatch({ historie_dny: Number($("sel-history-days").value) }); });
    $("open-data-btn").addEventListener("click", () => { void apiCall("open_data_folder"); });
    $("doladeni-start-btn").addEventListener("click", () => { void startDoladeni(); });
    $("doladeni-stop-btn").addEventListener("click", () => { void stopDoladeni(); });
    $("doladeni-use-btn").addEventListener("click", () => { void useDoladeniModel(); });
    $("doladeni-rerun-btn").addEventListener("click", () => { void startDoladeni(); });
    $("doladeni-retry-btn").addEventListener("click", () => { void startDoladeni(); });
    $("doladeni-stopped-rerun-btn").addEventListener("click", () => { void startDoladeni(); });

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && state.activePage === "trenink") {
        void loadDoladeni();
      }
    });

    const treninkModelsList = $("trenink-models-list");
    if (treninkModelsList) {
      treninkModelsList.addEventListener("change", (e) => {
        const tgt = e.target;
        if (tgt && tgt.matches('input[name="osobni-model"]')) {
          void selectTreninkModel(tgt.value);
        }
      });
    }
  }

  async function refreshStatusTick() {
    if (document.hidden) return;
    const res = await apiCall("get_state");
    if (!res.ok) return;
    state.data = res;
    if (res.config && Object.prototype.hasOwnProperty.call(res.config, "trenink_ukladat_zvuk")) {
      state.diktatyUkladatZvuk = Boolean(res.config.trenink_ukladat_zvuk);
    }
    renderAll({ skipPairsRender: isEditingPairs(), skipSnippetsRender: isEditingSnippets() });
    if (state.activePage === "prehled") void loadRecent();
    else if (state.activePage === "trenink") {
      if (!state.treninkSelectingModel) void loadTreninkModels();
      if (!state.diktatOpenId && !state.diktatBusyId && !state.diktatyLoading) void loadDiktaty(true);
    }
  }

  async function init() {
    bindEvents();
    const res = await apiCall("get_state");
    if (res.ok) {
      state.data = res;
      renderAll();
    } else {
      showSnackbar(res.error || "Nepodařilo se načíst nastavení.", { isError: true });
    }
    await loadRecent();
    await loadPamet();
    await loadTrenink({ jumpToNext: true });
    timers.poll = setInterval(() => { void refreshStatusTick(); }, 3000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => { void init(); }, { once: true });
  } else {
    void init();
  }
})();
