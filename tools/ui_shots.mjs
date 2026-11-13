import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
// PLAYWRIGHT_CORE = cesta k playwright-core/index.mjs, když není nainstalovaný v projektu
const { chromium } = await import(process.env.PLAYWRIGHT_CORE ? pathToFileURL(process.env.PLAYWRIGHT_CORE).href : "playwright-core");

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const uiDir = path.join(repoRoot, "mluvka", "ui");
const stubPath = path.join(repoRoot, "tools", "ui_stub.js");
const shotsDir = path.join(repoRoot, ".ai", "work", "shots_okno");

fs.mkdirSync(shotsDir, { recursive: true });

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
};

function startDevServer() {
  const htmlRaw = fs.readFileSync(path.join(uiDir, "index.html"), "utf-8");
  const injectedHtml = htmlRaw.replace(
    '<script src="app.js"></script>',
    '<script src="/ui_stub.js"></script>\n  <script src="app.js"></script>'
  );

  const server = http.createServer((req, res) => {
    const reqUrl = new URL(req.url, "http://127.0.0.1");
    if (reqUrl.pathname === "/" || reqUrl.pathname === "/index.html") {
      res.writeHead(200, { "Content-Type": MIME[".html"] });
      res.end(injectedHtml);
      return;
    }
    if (reqUrl.pathname === "/favicon.ico") {
      res.writeHead(204);
      res.end();
      return;
    }
    if (reqUrl.pathname === "/ui_stub.js") {
      res.writeHead(200, { "Content-Type": MIME[".js"] });
      res.end(fs.readFileSync(stubPath));
      return;
    }
    const clean = path.normalize(reqUrl.pathname.replace(/^\/+/, ""));
    const target = path.join(uiDir, clean);
    if (!target.startsWith(uiDir) || !fs.existsSync(target)) {
      res.writeHead(404);
      res.end("Not found");
      return;
    }
    const ext = path.extname(target);
    res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
    res.end(fs.readFileSync(target));
  });

  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({ server, baseUrl: `http://127.0.0.1:${port}/` });
    });
  });
}

const PAGES = ["prehled", "historie", "slovnik", "zkratky", "trenink", "ai", "nastaveni", "soukromi"];

async function checkNoHorizontalOverflow(page, label) {
  const overflow = await page.evaluate(() => {
    const rootOverflow = document.documentElement.scrollWidth > window.innerWidth;
    const content = document.getElementById("content-area");
    const contentOverflow = content ? content.scrollWidth > content.clientWidth + 1 : false;
    return {
      rootOverflow,
      contentOverflow,
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
      cScroll: content ? content.scrollWidth : 0,
      cClient: content ? content.clientWidth : 0,
    };
  });
  if (overflow.rootOverflow || overflow.contentOverflow) {
    throw new Error(`Horizontal overflow on ${label}: ${JSON.stringify(overflow)}`);
  }
}

async function run() {
  const { server, baseUrl } = await startDevServer();
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const errors = [];

  try {
    const context = await browser.newContext({ viewport: { width: 1040, height: 700 }, deviceScaleFactor: 1 });
    const page = await context.newPage();
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
    });
    page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));

    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await page.waitForSelector("#recent-list .history-row");

    // 7 pages x 2 themes at 1040x700
    for (const theme of ["light", "dark"]) {
      await page.emulateMedia({ colorScheme: theme });
      for (let i = 0; i < PAGES.length; i++) {
        const p = PAGES[i];
        await page.click(`.nav-item[data-page="${p}"]`);
        await page.waitForTimeout(80);
        await checkNoHorizontalOverflow(page, `${p} (${theme}, 1040x700)`);
        const num = String(i + 1).padStart(2, "0");
        await page.screenshot({ path: path.join(shotsDir, `${num}_${p}_${theme}.png`) });
      }
    }

    // 860x560 Prehled + Historie in light and dark
    await page.setViewportSize({ width: 860, height: 560 });
    for (const theme of ["light", "dark"]) {
      await page.emulateMedia({ colorScheme: theme });
      for (const p of ["prehled", "historie"]) {
        await page.click(`.nav-item[data-page="${p}"]`);
        await page.waitForTimeout(80);
        await checkNoHorizontalOverflow(page, `${p} (${theme}, 860x560)`);
        await page.screenshot({ path: path.join(shotsDir, `860_${p}_${theme}.png`) });
      }
    }

    // Focus ring + row expand + confirm dialog + interactive check at 1040x700
    await page.setViewportSize({ width: 1040, height: 700 });
    await page.emulateMedia({ colorScheme: "light" });
    await page.click('.nav-item[data-page="prehled"]');
    await page.waitForTimeout(60);
    await page.keyboard.press("Tab");
    await page.keyboard.press("Tab");
    await page.keyboard.press("Tab");
    await page.screenshot({ path: path.join(shotsDir, "extra_focus_ring_light.png") });

    // Test Ctrl+F shortcut -> Historie search focused
    await page.keyboard.press("Control+f");
    await page.waitForTimeout(60);
    const activeId = await page.evaluate(() => document.activeElement?.id);
    if (activeId !== "history-search") {
      throw new Error(`Ctrl+F did not focus history-search, focused: ${activeId}`);
    }

    // Test history row click expands without throwing & copy button triggers snackbar
    const beforeExpanded = await page.evaluate(() => {
      const r = document.querySelector("#history-container .history-row");
      return r ? r.classList.contains("expanded") : null;
    });
    await page.click("#history-container .history-row .history-toggle");
    const afterExpanded = await page.evaluate(() => {
      const r = document.querySelector("#history-container .history-row");
      return r ? r.classList.contains("expanded") : null;
    });
    if (beforeExpanded !== false || afterExpanded !== true) {
      throw new Error(`History row expand failed: before=${beforeExpanded}, after=${afterExpanded}`);
    }

    // Dialog screenshot (dark)
    await page.emulateMedia({ colorScheme: "dark" });
    await page.click("#clear-history-btn");
    await page.waitForTimeout(80);
    await page.screenshot({ path: path.join(shotsDir, "extra_dialog_dark.png") });
    await page.keyboard.press("Escape");

    // Round-2 behavioral checks across 3 s poll & settings mutations
    await page.emulateMedia({ colorScheme: "light" });
    await page.click('.nav-item[data-page="prehled"]');
    await page.waitForSelector("#recent-list .history-row .history-toggle");
    await page.click("#recent-list .history-row .history-toggle");
    await page.waitForTimeout(3200);
    const recentStillExpanded = await page.evaluate(() => {
      const r = document.querySelector("#recent-list .history-row");
      return r ? r.classList.contains("expanded") : false;
    });
    if (!recentStillExpanded) {
      throw new Error("Přehled recent row lost .expanded state across 3 s poll");
    }

    await page.click('.nav-item[data-page="zkratky"]');
    await page.click("#add-snippet-btn");
    const snippetAreas = page.locator("#snippets-list textarea.textarea");
    await snippetAreas.last().fill("Draft tělo zkratky");
    await page.click('.nav-item[data-page="nastaveni"]');
    await page.click("#toggle-sounds");
    await page.waitForTimeout(100);
    await page.click('.nav-item[data-page="zkratky"]');
    const draftVal = await page.locator("#snippets-list textarea.textarea").last().inputValue();
    if (draftVal !== "Draft tělo zkratky") {
      throw new Error(`Zkratky blank-trigger draft lost after settings toggle: ${JSON.stringify(draftVal)}`);
    }

    await page.click('.nav-item[data-page="nastaveni"]');
    await page.fill("#input-webhook", "http://bad.example.com");
    await page.locator("#input-webhook").blur();
    await page.waitForTimeout(3200);
    const webhookState = await page.evaluate(() => ({
      val: document.getElementById("input-webhook")?.value || "",
      err: document.getElementById("webhook-error")?.textContent?.trim() || "",
    }));
    if (webhookState.val !== "http://bad.example.com" || !webhookState.err.includes("https://")) {
      throw new Error(`Webhook error/value lost across 3 s poll: ${JSON.stringify(webhookState)}`);
    }
    await page.fill("#input-webhook", "https://hooks.slack.com/services/T000/B000/XXX");
    await page.locator("#input-webhook").blur();
    await page.waitForTimeout(150);
    await page.click("#toggle-sounds");
    const restoredErr = await page.evaluate(() => document.getElementById("webhook-error")?.textContent?.trim() || "");
    if (restoredErr !== "") {
      throw new Error(`Webhook error did not clear after restoring valid URL: ${restoredErr}`);
    }

    // Empty state screenshots (?empty=1)
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto(`${baseUrl}?empty=1`, { waitUntil: "networkidle" });
    await page.waitForTimeout(100);
    await page.screenshot({ path: path.join(shotsDir, "extra_empty_prehled_light.png") });
    await page.click('.nav-item[data-page="historie"]');
    await page.waitForTimeout(60);
    await page.screenshot({ path: path.join(shotsDir, "extra_empty_historie_light.png") });
    await page.click('.nav-item[data-page="slovnik"]');
    await page.waitForTimeout(60);
    await page.screenshot({ path: path.join(shotsDir, "extra_empty_slovnik_light.png") });
    await page.click('.nav-item[data-page="zkratky"]');
    await page.waitForTimeout(60);
    await page.screenshot({ path: path.join(shotsDir, "extra_empty_zkratky_light.png") });

    // v3 checks: Historie edit + inline suggestion, Slovnik AI import, Trenink recording + Dar dialog, Invalid config bar
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await page.click('.nav-item[data-page="historie"]');
    await page.waitForSelector("#history-container .history-row");
    await page.click('#history-container .history-row button[aria-label="Upravit"]');
    await page.waitForSelector("#history-container .history-edit-textarea");
    await page.fill("#history-container .history-edit-textarea", "Upravený přepis testovací věty.");
    await page.screenshot({ path: path.join(shotsDir, "extra_historie_edit_light.png") });
    await page.click("#history-container .history-edit-actions .btn-primary");
    await page.waitForSelector("#history-container .history-suggestion");
    await page.screenshot({ path: path.join(shotsDir, "extra_historie_suggestion_light.png") });

    await page.click('.nav-item[data-page="slovnik"]');
    await page.fill("#import-json-input", '```json\n{"slova": ["Mluvka", "Playwright", "Whisper"], "opravy": [["vispr", "Whisper"]]}\n```');
    await page.click("#import-load-btn");
    await page.waitForSelector("#import-preview:not([hidden])");
    const impCnt = await page.textContent("#import-preview-count");
    if (!impCnt || !impCnt.includes("1 už máš")) {
      throw new Error(`Unexpected import preview count: ${impCnt}`);
    }
    await page.screenshot({ path: path.join(shotsDir, "extra_slovnik_import_light.png") });

    await page.emulateMedia({ colorScheme: "dark" });
    await page.click('.nav-item[data-page="trenink"]');
    await page.waitForSelector("#trenink-sentence");
    await page.click("#trenink-record-btn");
    await page.waitForSelector("#trenink-live-box:not([hidden])");
    await page.waitForTimeout(160);
    await page.screenshot({ path: path.join(shotsDir, "extra_trenink_recording_dark.png") });
    await page.click("#trenink-record-btn");
    await page.waitForSelector("#trenink-rerecord-btn:not([hidden])");

    await page.emulateMedia({ colorScheme: "light" });
    await page.click("#open-dar-btn");
    await page.waitForSelector("#dar-dialog[open]");
    const subDisabled = await page.isDisabled("#dar-submit-btn");
    if (!subDisabled) throw new Error("Dar submit button must start disabled");
    await page.check("#dar-chk-1");
    await page.check("#dar-chk-2");
    await page.check("#dar-chk-3");
    await page.waitForTimeout(40);
    await page.screenshot({ path: path.join(shotsDir, "extra_dar_dialog_light.png") });
    await page.click("#dar-submit-btn");
    await page.waitForSelector("#dar-result-wrap:not([hidden])");

    if ((await page.locator("#trenink-models-list").count()) > 0) {
      const modelRadios = page.locator('#trenink-models-list input[name="osobni-model"]');
      if ((await modelRadios.count()) !== 2) {
        throw new Error(`Expected 2 model radio buttons, got ${await modelRadios.count()}`);
      }
      await modelRadios.nth(1).check();
      await page.waitForTimeout(80);
      const secondChecked = await modelRadios.nth(1).isChecked();
      if (!secondChecked) {
        throw new Error("Expected second model radio to remain checked after vybrat_model");
      }
      await page.locator("#h2-trenink-model").scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(shotsDir, "extra_trenink_model_light.png") });
      await page.emulateMedia({ colorScheme: "dark" });
      await page.waitForTimeout(60);
      await page.screenshot({ path: path.join(shotsDir, "extra_trenink_model_dark.png") });
      await page.emulateMedia({ colorScheme: "light" });
    }

    await page.goto(`${baseUrl}?invalid=1`, { waitUntil: "networkidle" });
    await page.waitForSelector("#config-invalid-bar:not([hidden])");
    await page.screenshot({ path: path.join(shotsDir, "extra_config_invalid_light.png") });

    // Doladeni states: 1040x700 and 390x844, light + dark + behavioral checks
    const doladeniShots = ["malo", "pripraveno", "bezi", "hotovo", "hotovo_stejny", "chyba", "zastaveno"];
    for (const st of doladeniShots) {
      for (const vp of [
        { label: "1040", width: 1040, height: 700 },
        { label: "390", width: 390, height: 844 },
      ]) {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        for (const theme of ["light", "dark"]) {
          await page.emulateMedia({ colorScheme: theme });
          await page.goto(`${baseUrl}?doladeni=${st}`, { waitUntil: "networkidle" });
          await page.click('.nav-item[data-page="trenink"]');
          await page.waitForSelector("#h2-trenink-doladeni");
          await page.waitForTimeout(80);
          if (st === "bezi" && vp.label === "1040" && theme === "light") {
            await page.click("#doladeni-details summary");
            await page.waitForTimeout(40);
          }
          await checkNoHorizontalOverflow(page, `doladeni_${st} (${theme}, ${vp.width}x${vp.height})`);
          const minBtnH = await page.evaluate(() => {
            const btns = Array.from(document.querySelectorAll(".card-doladeni button:not([hidden])"))
              .filter((b) => b.offsetParent !== null);
            return btns.length ? Math.min(...btns.map((b) => b.getBoundingClientRect().height)) : 999;
          });
          if (minBtnH < 36) {
            throw new Error(`Doladeni button too short (${minBtnH}px < 36px) in state=${st} ${vp.width}`);
          }
          await page.locator(".card-doladeni").evaluate((el) => el.scrollIntoView({ block: "center" }));
          await page.screenshot({ path: path.join(shotsDir, `doladeni_${vp.label}_${st}_${theme}.png`) });
        }
      }
    }

    // Interactive check: doladeni_pouzit() updates Osobní model & hides use button; polling runs ONLY when bezi on trenink
    await page.setViewportSize({ width: 1040, height: 700 });
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto(`${baseUrl}?doladeni=hotovo`, { waitUntil: "networkidle" });
    const countOnOverview = await page.evaluate(() => window.__stubCounters.doladeniStavCalls);
    if (countOnOverview !== 0) {
      throw new Error(`Expected 0 doladeni_stav calls on prehled, got ${countOnOverview}`);
    }
    await page.click('.nav-item[data-page="trenink"]');
    await page.waitForSelector("#doladeni-use-btn:not([hidden])");
    await page.click("#doladeni-use-btn");
    await page.waitForSelector("#doladeni-active-note:not([hidden])");
    const activeNoteText = await page.textContent("#doladeni-active-note");
    if (!activeNoteText || !activeNoteText.includes("Používá se. Přepne se při další diktaci.")) {
      throw new Error(`Unexpected doladeni active note: ${activeNoteText}`);
    }
    if ((await page.locator('input[name="osobni-model"]').count()) > 0) {
      const osobniChecked = await page.isChecked('input[name="osobni-model"][value="ggml-osobni-20261113-0915-q8_0.bin"]');
      if (!osobniChecked) {
        throw new Error("Expected newly trained osobni model radio to be selected after doladeni_pouzit");
      }
    }

    await page.goto(`${baseUrl}?doladeni=bezi`, { waitUntil: "networkidle" });
    await page.click('.nav-item[data-page="trenink"]');
    await page.waitForSelector("#doladeni-state-running:not([hidden])");
    if (!(await page.isDisabled("#trenink-measure-btn"))) {
      throw new Error("Expected #trenink-measure-btn to be disabled while doladeni is bezi");
    }
    const c1 = await page.evaluate(() => window.__stubCounters.doladeniStavCalls);
    await page.waitForTimeout(2300);
    const c2 = await page.evaluate(() => window.__stubCounters.doladeniStavCalls);
    if (c2 <= c1) {
      throw new Error(`Expected 2s polling on trenink when bezi (${c1} -> ${c2})`);
    }
    await page.click('.nav-item[data-page="prehled"]');
    const c3 = await page.evaluate(() => window.__stubCounters.doladeniStavCalls);
    await page.waitForTimeout(2300);
    const c4 = await page.evaluate(() => window.__stubCounters.doladeniStavCalls);
    if (c4 !== c3) {
      throw new Error(`Expected polling to stop after leaving trenink (${c3} -> ${c4})`);
    }

    // Diktaty k doladeni: empty states, populated light/dark + 1040/390 shots, inline edit/verify/delete, zero inline styles
    await page.setViewportSize({ width: 1040, height: 700 });
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto(`${baseUrl}?diktaty=off`, { waitUntil: "networkidle" });
    await page.click('.nav-item[data-page="trenink"]');
    await page.waitForSelector("#diktaty-empty:not([hidden])");
    const offEmptyText = await page.textContent("#diktaty-empty");
    if (!offEmptyText || !offEmptyText.includes("Zapni Ukládat zvuk diktátů. Diktáty se pak objeví tady.")) {
      throw new Error(`Unexpected off empty text: ${offEmptyText}`);
    }

    await page.goto(`${baseUrl}?diktaty=on_empty`, { waitUntil: "networkidle" });
    await page.click('.nav-item[data-page="trenink"]');
    await page.waitForSelector("#diktaty-empty:not([hidden])");
    const onEmptyText = await page.textContent("#diktaty-empty");
    if (!onEmptyText || !onEmptyText.includes("Zatím žádný uložený diktát.")) {
      throw new Error(`Unexpected on_empty text: ${onEmptyText}`);
    }

    for (const vp of [
      { label: "1040", width: 1040, height: 700 },
      { label: "390", width: 390, height: 844 },
    ]) {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      for (const theme of ["light", "dark"]) {
        await page.emulateMedia({ colorScheme: theme });
        await page.goto(baseUrl, { waitUntil: "networkidle" });
        await page.click('.nav-item[data-page="trenink"]');
        await page.waitForSelector("#diktaty-list .diktat-item");
        await page.click("#diktaty-list .diktat-item .diktat-toggle");
        await page.waitForSelector("#diktaty-list .diktat-editor");
        await page.waitForTimeout(80);
        await checkNoHorizontalOverflow(page, `diktaty_${vp.label}_${theme}`);
        const inlineCount = await page.evaluate(() => document.querySelectorAll(".card-diktaty [style]").length);
        if (inlineCount > 0) {
          throw new Error(`Found ${inlineCount} inline [style] attributes in .card-diktaty`);
        }
        await page.locator('[aria-labelledby="h2-trenink-diktaty"]').evaluate((el) => el.scrollIntoView({ block: "center" }));
        await page.screenshot({ path: path.join(shotsDir, `diktaty_${vp.label}_${theme}.png`) });
      }
    }

    // Behavioral check on diktaty card: verify + delete + counts update + keyboard focus
    await page.setViewportSize({ width: 1040, height: 700 });
    await page.emulateMedia({ colorScheme: "light" });
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await page.click('.nav-item[data-page="trenink"]');
    await page.waitForSelector("#diktaty-list .diktat-item");
    const initialCounts = await page.textContent("#diktaty-counts");
    if (!initialCounts || !initialCounts.includes("Ověřeno 1 z 3")) {
      throw new Error(`Unexpected initial diktaty counts: ${initialCounts}`);
    }
    await page.click("#diktaty-list .diktat-item .diktat-play-btn");
    const openCountPlay = await page.locator("#diktaty-list .diktat-editor").count();
    if (openCountPlay !== 0) {
      throw new Error("Clicking Přehrát should not toggle the editor open");
    }
    await page.click("#diktaty-list .diktat-item .diktat-toggle");
    await page.waitForSelector("#diktaty-list .diktat-textarea");
    const prefilled = await page.inputValue("#diktaty-list .diktat-textarea");
    if (!prefilled.includes("ehm tak já to zkusím poslat")) {
      throw new Error(`Unexpected prefilled textarea value: ${prefilled}`);
    }
    await page.press("#diktaty-list .diktat-textarea", "Escape");
    await page.waitForTimeout(40);
    if ((await page.locator("#diktaty-list .diktat-editor").count()) !== 0) {
      throw new Error("Pressing Escape should close diktat editor");
    }
    await page.click("#diktaty-list .diktat-item:nth-child(2) .diktat-toggle");
    await page.waitForSelector("#diktaty-list .diktat-raw-box");
    await page.click("#diktaty-list .diktat-item:nth-child(2) .diktat-toggle");
    await page.click("#diktaty-list .diktat-item .diktat-toggle");
    await page.waitForSelector("#diktaty-list .diktat-textarea");
    await page.fill("#diktaty-list .diktat-textarea", "Ehm tak já to zkusím poslat ještě dnes odpoledne, Petře.");
    await page.click("#diktaty-list .diktat-save-btn");
    await page.waitForTimeout(60);
    const updatedCounts = await page.textContent("#diktaty-counts");
    if (!updatedCounts || !updatedCounts.includes("Ověřeno 2 z 3")) {
      throw new Error(`Expected Ověřeno 2 z 3 after verify, got: ${updatedCounts}`);
    }
    await page.click("#diktaty-list .diktat-item:last-child .diktat-toggle");
    await page.waitForSelector("#diktaty-list .diktat-delete-btn");
    await page.click("#diktaty-list .diktat-delete-btn");
    await page.waitForTimeout(60);
    const afterDelCounts = await page.textContent("#diktaty-counts");
    if (!afterDelCounts || !afterDelCounts.includes("Ověřeno 2 z 2")) {
      throw new Error(`Expected Ověřeno 2 z 2 after delete, got: ${afterDelCounts}`);
    }

    if (errors.length > 0) {
      throw new Error(`Console/page errors:\n${errors.join("\n")}`);
    }
    console.log("ALL SHOTS OK, console errors: 0");
  } finally {
    await browser.close();
    server.close();
  }
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
