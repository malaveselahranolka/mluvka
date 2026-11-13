// Stránka Model: známka, rychlost, doporučená nastavení, porovnání, měření, technické údaje.
(function () {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const BARS = [6, 10, 14, 18, 14, 10, 6, 10, 14];
  const SPEED_NAME = { instantni: "Instantní", rychla: "Rychlá", pomala: "Pomalá" };
  const SPEED_BARS = { instantni: 9, rychla: 6, pomala: 3 };
  const BAD = /^[DF]/;
  const SCALE = [["A+", "≤ 3 %"], ["A", "≤ 4,5 %"], ["A-", "≤ 6 %"], ["B+", "≤ 7,5 %"], ["B", "≤ 9 %"], ["B-", "≤ 10,5 %"],
    ["C+", "≤ 12 %"], ["C", "≤ 14 %"], ["C-", "≤ 16 %"], ["D+", "≤ 18 %"], ["D", "≤ 21 %"], ["D-", "≤ 24 %"], ["F", "≤ 30 %"], ["F-", "> 30 %"]];

  const st = { data: null, mereni: null, poll: null, inflight: false, visible: false, rowError: {}, confirmId: null };
  const $ = (id) => document.getElementById(id);
  const api = (m, ...a) => window.MluvkaApp.apiCall(m, ...a);

  function mk(tag, cls = "", text = "") {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  }

  const pct = (w) => `${(w * 100).toFixed(1).replace(".", ",")} %`;
  const sec = (s) => `${Number(s).toFixed(1).replace(".", ",")} s`;
  const gradeWords = (z) => z.replace("+", " plus").replace("-", " mínus");

  function datum(iso) {
    const d = new Date(iso);
    return isNaN(d) ? "" : `${d.getDate()}. ${d.getMonth() + 1}. ${d.getFullYear()}`;
  }

  function setupName(s) {
    return `${s.nazev_modelu} · beam ${s.beam} · ${s.haiku ? "s úpravou Haiku" : "bez úpravy"}`;
  }

  function meter(nOn, size = 18, measuring = false) {
    const k = size / 18;
    const svg = document.createElementNS(SVG_NS, "svg");
    const w = Math.round((9 * 3 + 8 * 3) * k);
    svg.setAttribute("width", String(w));
    svg.setAttribute("height", String(size));
    svg.setAttribute("viewBox", `0 0 51 18`);
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", `mdl-meter${measuring ? " is-measuring" : ""}`);
    BARS.forEach((h, i) => {
      const r = document.createElementNS(SVG_NS, "rect");
      r.setAttribute("x", String(i * 6));
      r.setAttribute("y", String((18 - h) / 2));
      r.setAttribute("width", "3");
      r.setAttribute("height", String(h));
      r.setAttribute("rx", "1.5");
      if (i < nOn) r.setAttribute("class", "on");
      if (measuring) r.style.animationDelay = `${i * 120}ms`;
      svg.append(r);
    });
    return svg;
  }

  function speedEl(rych, small = false) {
    const box = mk("span", `mdl-speed${small ? " mdl-speed-sm" : ""}`);
    if (!rych) {
      box.append(meter(0, small ? 12 : 18), mk("span", "", "Rychlost nezměřená"));
      return box;
    }
    box.setAttribute("aria-label", `Rychlost: ${SPEED_NAME[rych].toLowerCase()}`);
    box.append(meter(SPEED_BARS[rych], small ? 12 : 18), mk("span", "", SPEED_NAME[rych]));
    return box;
  }

  function gradeEl(z, small = false) {
    const g = mk("span", `mdl-grade${small ? " mdl-grade-sm" : ""}`, z || "?");
    if (!z) g.classList.add("is-none");
    else if (BAD.test(z)) g.classList.add("is-bad");
    g.setAttribute("aria-label", z ? `Známka ${gradeWords(z)}` : "Bez známky");
    g.setAttribute("role", "img");
    return g;
  }

  function measureBtn(s, cls, label) {
    const b = mk("button", `btn ${cls}`, label);
    b.type = "button";
    const busy = st.mereni && st.mereni.bezi;
    b.disabled = Boolean(busy) || !s.pouzitelny;
    if (busy) b.title = "Probíhá jiné měření";
    else if (!s.pouzitelny) b.title = "Chybí API klíč pro Claude";
    b.addEventListener("click", () => void zmerit(s.id));
    return b;
  }

  function measuringBox(s) {
    const m = st.mereni;
    const box = mk("div", "mdl-measuring");
    const head = mk("div", "mdl-speed mdl-speed-sm");
    head.append(meter(9, 12, true), mk("span", "", m.celkem ? `Měřím… ${m.hotovo} / ${m.celkem}` : "Spouštím měření…"));
    const track = mk("div", "progress-track");
    const fill = mk("div", "progress-fill");
    fill.style.width = m.celkem ? `${Math.round((100 * m.hotovo) / m.celkem)}%` : "0%";
    track.append(fill);
    box.append(head, track);
    return box;
  }

  const isMeasuring = (s) => st.mereni && st.mereni.bezi && st.mereni.setup_id === s.id;

  // ---------- hero ----------
  function renderHero() {
    const host = $("mdl-hero");
    host.replaceChildren();
    const s = st.data.setupy.find((x) => x.aktivni);
    if (!s) return;
    const gw = mk("div", "mdl-grade-wrap");
    gw.append(gradeEl(s.znamka), mk("span", "mdl-caption", "Známka přesnosti"));
    const body = mk("div", "mdl-hero-body");
    const name = mk("h2", "mdl-setup-name", setupName(s));
    body.append(name);
    if (isMeasuring(s)) {
      body.append(measuringBox(s));
    } else if (!s.mereni) {
      body.append(mk("p", "mdl-facts", "Tohle nastavení ještě není změřené."), measureBtn(s, "btn-secondary", "Změřit"));
    } else {
      body.append(speedEl(s.rychlost));
      const m = s.mereni;
      const facts = [`Chybovost ${pct(s.prumer)}`];
      if (s.s_na_minutu != null) {
        let t = `1 minuta řeči za ${sec(s.s_na_minutu)}`;
        if (s.haiku && m.s_haiku_na_minutu != null) t += ` (z toho Haiku ${sec(m.s_haiku_na_minutu)})`;
        facts.push(t);
      }
      if (!m.cv) facts.push("měřeno jen na FLEURS");
      body.append(mk("p", "mdl-facts", facts.join(" · ")));
      const n = (m.cv ? m.cv.n : 0) + (m.fleurs ? m.fleurs.n : 0);
      const gpu = (m.stroj && m.stroj.gpu) || (st.data.stroj && st.data.stroj.gpu) || "";
      body.append(mk("p", "mdl-meta", `Změřeno ${datum(m.datum)}${gpu ? ` na ${gpu}` : ""} · ${n} vět`));
      if (s.s_na_minutu == null) body.append(measureBtn(s, "btn-secondary", "Změřit rychlost"));
    }
    if (st.rowError[s.id]) body.append(mk("p", "mdl-row-error", st.rowError[s.id]));
    host.append(gw, body);
  }

  // ---------- doporučení ----------
  const PRESET_DESC = {
    nejpresnejsi: (s) => `Nejméně chyb. ${s.haiku ? "Text uhladí Haiku." : "Bez úpravy přes Haiku."}`,
    nejrychlejsi: () => "Text je hned. Za cenu pár chyb navíc.",
    offline: () => "Nic neopouští počítač.",
  };

  function renderPresets() {
    const host = $("mdl-presets");
    host.replaceChildren();
    const rec = st.data.doporucene;
    if (!rec.length) {
      host.append(mk("p", "hint-line", "Zatím není nic změřené. Změř aspoň jedno nastavení níž."));
      return;
    }
    for (const r of rec) {
      const s = st.data.setupy.find((x) => x.id === r.setup_id);
      if (!s) continue;
      const card = mk("article", "card mdl-preset");
      const head = mk("div", "mdl-preset-head");
      head.append(gradeEl(s.znamka, true), mk("span", "mdl-preset-name", r.nazev));
      card.append(head, mk("p", "mdl-preset-desc", r.klice.map((k) => PRESET_DESC[k](s)).join(" ")), speedEl(s.rychlost, true),
        mk("span", "mdl-caption", setupName(s)));
      if (s.aktivni) {
        const pill = mk("span", "mdl-in-use", "✓ Používá se");
        card.append(pill);
      } else {
        const b = mk("button", "btn btn-secondary", "Použít");
        b.type = "button";
        b.addEventListener("click", () => void pouzit(s.id));
        card.append(b);
      }
      host.append(card);
    }
  }

  // ---------- tabulka ----------
  function cell(label, cls = "") {
    const td = mk("td", cls);
    if (label) td.dataset.label = label;
    return td;
  }

  function renderTable() {
    const body = $("mdl-table-body");
    body.replaceChildren();
    for (const s of st.data.setupy) {
      const tr = mk("tr", s.aktivni ? "is-active" : "");
      const c1 = cell("", "mdl-cell-name");
      c1.append(document.createTextNode(`${s.nazev_modelu} · beam ${s.beam}`));
      if (s.haiku) c1.append(mk("span", "mdl-pill", "Haiku"));
      if (s.aktivni) c1.append(mk("span", "mdl-pill", "Používá se"));
      tr.append(c1);
      if (isMeasuring(s)) {
        const td = cell("Měření");
        td.colSpan = 4;
        td.append(measuringBox(s));
        tr.append(td);
        body.append(tr);
        continue;
      }
      const c2 = cell("Známka");
      c2.append(gradeEl(s.znamka, true));
      const c3 = cell("Chybovost");
      if (s.prumer != null) {
        c3.append(mk("span", "mdl-strong", pct(s.prumer)));
        const m = s.mereni;
        const parts = [];
        if (m.cv) parts.push(`Common Voice ${pct(m.cv.wer).replace(" %", "")}`);
        if (m.fleurs) parts.push(`FLEURS ${pct(m.fleurs.wer).replace(" %", "")}`);
        c3.append(mk("span", "mdl-sub", parts.join(" · ")));
      } else c3.append(mk("span", "mdl-sub", "–"));
      const c4 = cell("1 min řeči");
      if (s.s_na_minutu != null) {
        c4.append(mk("span", "mdl-strong", sec(s.s_na_minutu)), speedEl(s.rychlost, true));
        if (s.haiku && s.mereni.s_haiku_na_minutu != null) c4.append(mk("span", "mdl-sub", `z toho Haiku ${sec(s.mereni.s_haiku_na_minutu)}`));
      } else c4.append(mk("span", "mdl-sub", "–"));
      const c5 = cell("", "");
      const acts = mk("div", "mdl-actions");
      if (st.confirmId === s.id) {
        acts.append(mk("span", "mdl-sub", "Opravdu odebrat?"));
        const yes = mk("button", "btn btn-tertiary", "Odebrat");
        yes.type = "button";
        yes.addEventListener("click", () => void odebrat(s.id));
        const no = mk("button", "btn btn-tertiary", "Zpět");
        no.type = "button";
        no.addEventListener("click", () => { st.confirmId = null; renderTable(); });
        acts.append(yes, no);
      } else {
        if (!s.mereni) acts.append(measureBtn(s, "btn-secondary", "Změřit"));
        if (!s.aktivni) {
          const use = mk("button", "btn btn-tertiary", "Použít");
          use.type = "button";
          use.disabled = !s.pouzitelny;
          if (!s.pouzitelny) use.title = "Chybí API klíč pro Claude";
          use.addEventListener("click", () => void pouzit(s.id));
          acts.append(use);
        }
        if (s.mereni) {
          const again = mk("button", "icon-btn", "↻");
          again.type = "button";
          again.setAttribute("aria-label", `Změřit znovu: ${setupName(s)}`);
          again.disabled = Boolean(st.mereni && st.mereni.bezi) || !s.pouzitelny;
          again.addEventListener("click", () => void zmerit(s.id));
          acts.append(again);
        }
        if (s.vlastni) {
          const del = mk("button", "icon-btn danger", "×");
          del.type = "button";
          del.setAttribute("aria-label", `Odebrat: ${setupName(s)}`);
          del.addEventListener("click", () => { st.confirmId = s.id; renderTable(); });
          acts.append(del);
        }
      }
      c5.append(acts);
      tr.append(c2, c3, c4, c5);
      if (st.rowError[s.id]) {
        const err = mk("span", "mdl-row-error", st.rowError[s.id]);
        c1.append(mk("br"), err);
      }
      body.append(tr);
    }
  }

  function renderAddForm() {
    const sel = $("mdl-add-model");
    const prev = sel.value;
    sel.replaceChildren();
    for (const m of st.data.modely) {
      const o = mk("option", "", m.nazev);
      o.value = m.soubor;
      sel.append(o);
    }
    if (prev) sel.value = prev;
    const sw = $("mdl-add-haiku");
    sw.disabled = !st.data.ma_klic;
    if (!st.data.ma_klic) sw.setAttribute("aria-checked", "false");
    $("mdl-add-haiku-hint").hidden = st.data.ma_klic;
    $("mdl-add-btn").disabled = Boolean(st.mereni && st.mereni.bezi);
  }

  // ---------- technické údaje ----------
  function renderTech() {
    const s = st.data.stroj || {};
    const dl = $("mdl-stroj");
    dl.replaceChildren();
    for (const [k, v] of [["Grafická karta", s.gpu], ["Procesor", s.cpu], ["Paměť", s.ram], ["Systém", s.os], ["Přepisový engine", s.engine]]) {
      dl.append(mk("dt", "", k), mk("dd", "", v || "–"));
    }
    const sc = $("mdl-scale");
    if (!sc.childElementCount) {
      for (const [z, t] of SCALE) {
        const it = mk("div", "mdl-scale-item");
        it.append(gradeEl(z, true), mk("span", "", t));
        sc.append(it);
      }
      const leg = $("mdl-speed-legend");
      for (const [k, t] of [["instantni", "≤ 2 s"], ["rychla", "≤ 10 s"], ["pomala", "> 10 s"]]) {
        const it = mk("span", "mdl-speed mdl-speed-sm");
        it.append(meter(SPEED_BARS[k], 12), mk("span", "", `${SPEED_NAME[k]} ${t}`));
        leg.append(it);
      }
    }
    const mb = $("mdl-modely-body");
    mb.replaceChildren();
    for (const m of st.data.modely) {
      const tr = mk("tr");
      for (const v of [m.nazev, m.soubor, m.velikost_mb != null ? `${String(m.velikost_mb).replace(".", ",")} MB` : "–", m.kvantizace, m.licence]) tr.append(mk("td", "", v));
      mb.append(tr);
    }
    const rb = $("mdl-raw-body");
    rb.replaceChildren();
    for (const r of st.data.surova) {
      const name = `${r.model} · beam ${r.beam}${r.haiku ? " · Haiku" : ""}`;
      for (const [w, n] of [[r.cv, "Common Voice"], [r.fleurs, "FLEURS"]]) {
        if (!w) continue;
        const row = mk("tr");
        for (const v of [datum(r.datum), name, n, String(w.n), pct(w.wer), r.s_na_minutu != null ? sec(r.s_na_minutu) : "–"]) row.append(mk("td", "", v));
        rb.append(row);
      }
    }
  }

  function renderAll() {
    if (!st.data) return;
    renderHero();
    renderPresets();
    renderTable();
    renderAddForm();
    renderTech();
  }

  function say(text) {
    const s = $("mdl-status");
    s.textContent = "";
    window.setTimeout(() => { s.textContent = text; }, 30);
  }

  // ---------- akce ----------
  async function load() {
    const res = await api("model_stav");
    if (!res.ok) {
      $("mdl-error").textContent = res.error || "Načtení stránky Model selhalo.";
      $("mdl-error").hidden = false;
      return;
    }
    $("mdl-error").hidden = true;
    st.data = res;
    st.mereni = res.mereni_bezi || st.mereni;
    renderAll();
    if (st.mereni && st.mereni.bezi) startPoll();
  }

  async function pouzit(id) {
    const res = await api("model_pouzit", id);
    if (!res.ok) { st.rowError[id] = res.error || "Nastavení se nepodařilo použít."; renderAll(); return; }
    delete st.rowError[id];
    say("Nastavení se používá. Přepne se při další diktaci.");
    await load();
  }

  async function zmerit(id) {
    delete st.rowError[id];
    const res = await api("model_zmerit", id);
    if (!res.ok) { st.rowError[id] = res.error || "Měření se nepodařilo spustit."; renderAll(); return; }
    st.mereni = { bezi: true, setup_id: id, hotovo: 0, celkem: 0 };
    renderAll();
    startPoll();
  }

  async function odebrat(id) {
    st.confirmId = null;
    const res = await api("model_odebrat", id);
    if (!res.ok) { st.rowError[id] = res.error || "Odebrání selhalo."; renderAll(); return; }
    await load();
  }

  async function pridat() {
    const err = $("mdl-add-error");
    err.textContent = "";
    const haiku = $("mdl-add-haiku").getAttribute("aria-checked") === "true";
    const res = await api("model_pridat", $("mdl-add-model").value, Number($("mdl-add-beam").value), haiku);
    if (!res.ok) { err.textContent = res.error || "Přidání selhalo."; return; }
    await load();
    await zmerit(res.setup.id);
  }

  function startPoll() {
    if (st.poll || !st.visible) return;
    st.poll = window.setInterval(async () => {
      if (st.inflight) return;
      st.inflight = true;
      try {
        const m = await api("model_mereni_stav");
        if (!m.ok) return;
        const was = st.mereni;
        st.mereni = m.stav;
        if (!m.stav || !m.stav.bezi) {
          stopPoll();
          if (m.stav && m.stav.vysledek === "chyba") st.rowError[m.stav.setup_id] = m.stav.chyba || "Měření selhalo.";
          else if (m.stav && m.stav.vysledek === "ok") { say("Změřeno"); window.MluvkaApp.showSnackbar("Změřeno"); }
          await load();
        } else if (was) {
          renderHero();
          renderTable();
        }
      } finally {
        st.inflight = false;
      }
    }, 1000);
  }

  function stopPoll() {
    if (st.poll) window.clearInterval(st.poll);
    st.poll = null;
  }

  function init() {
    $("mdl-add-btn").addEventListener("click", () => void pridat());
    $("mdl-add-haiku").addEventListener("click", (e) => {
      const b = e.currentTarget;
      if (b.disabled) return;
      b.setAttribute("aria-checked", b.getAttribute("aria-checked") === "true" ? "false" : "true");
    });
    for (const b of document.querySelectorAll("[data-goto-page]")) {
      b.addEventListener("click", () => window.MluvkaApp.switchPage(b.dataset.gotoPage));
    }
  }

  window.MluvkaModel = {
    show() { st.visible = true; void load(); },
    hide() { st.visible = false; stopPoll(); },
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
