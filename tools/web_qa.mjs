// QA webu: screenshoty + kontroly (overflow, konzole, zdroje, interakce).
// node tools/web_qa.mjs [url]
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const { chromium } = await import(
  process.env.PLAYWRIGHT_CORE ? pathToFileURL(process.env.PLAYWRIGHT_CORE).href : "playwright-core"
);

const url = process.argv[2] || "http://127.0.0.1:4173/";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.join(__dirname, "..", ".ai", "work", "shots_web");
fs.mkdirSync(outDir, { recursive: true });

const VIEWS = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobil", width: 390, height: 844 },
];
const problems = [];

const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  for (const v of VIEWS) {
    for (const theme of ["light", "dark"]) {
      const ctx = await browser.newContext({
        viewport: { width: v.width, height: v.height },
        colorScheme: theme,
        locale: "cs-CZ",
        deviceScaleFactor: 1,
        permissions: ["clipboard-read", "clipboard-write"],
      });
      const page = await ctx.newPage();
      const errors = [];
      const bad = [];
      page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
      page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
      page.on("response", (r) => {
        if (r.status() >= 400) bad.push(`${r.status()} ${r.url()}`);
      });

      await page.goto(url, { waitUntil: "networkidle" });
      await page.waitForTimeout(2600); // nechá doběhnout automatické demo

      const over = await page.evaluate(() => ({
        sw: document.documentElement.scrollWidth,
        iw: window.innerWidth,
      }));
      if (over.sw > over.iw + 1) problems.push(`${v.name}/${theme}: horizontální scroll ${over.sw} > ${over.iw}`);

      // demo v hero: krátký stisk
      await page.locator("#key").hover();
      await page.mouse.down();
      await page.waitForTimeout(120);
      await page.mouse.up();
      await page.waitForTimeout(250);
      const shortMsg = await page.locator("#pill-txt").textContent();
      if (!/Podrž klávesu déle/.test(shortMsg || "")) problems.push(`${v.name}/${theme}: krátký stisk nehlásí nic (${shortMsg})`);

      // demo: dlouhý stisk přes klávesu ControlRight
      await page.waitForTimeout(1500);
      const before = await page.locator("#mail-text").textContent();
      await page.keyboard.down("ControlRight");
      await page.waitForTimeout(700);
      await page.keyboard.up("ControlRight");
      await page.waitForTimeout(1800);
      const after = await page.locator("#mail-text").textContent();
      if (!after || after === before) problems.push(`${v.name}/${theme}: dlouhý stisk nevložil text`);

      // taby
      await page.locator("#t-slovnik").click();
      await page.waitForTimeout(500);
      const src = await page.locator("#shot-light").getAttribute("src");
      if (!/slovnik/.test(src || "")) problems.push(`${v.name}/${theme}: tab Slovník nepřepnul screenshot (${src})`);
      const cap = await page.locator("#app-caption").textContent();
      if (!/Jména/.test(cap || "")) problems.push(`${v.name}/${theme}: popisek tabu se nezměnil`);
      await page.locator("#t-prehled").click();
      await page.waitForTimeout(400);

      // kopírování
      await page.locator("#copy").click();
      await page.waitForTimeout(300);
      const copyTxt = await page.locator("#copy").textContent();
      if (!/Zkopírováno/.test(copyTxt || "")) problems.push(`${v.name}/${theme}: kopírování nehlásí úspěch (${copyTxt})`);

      // mobilní menu
      if (v.width < 860) {
        await page.locator("#menu-btn").click();
        await page.waitForTimeout(250);
        if (!(await page.locator("#nav").evaluate((n) => n.classList.contains("open"))))
          problems.push("mobil: menu se neotevřelo");
        await page.keyboard.press("Escape");
        await page.waitForTimeout(250);
        if (await page.locator("#nav").evaluate((n) => n.classList.contains("open")))
          problems.push("mobil: Esc nezavřel menu");
      }

      await page.evaluate(() => window.scrollTo(0, 0));
      await page.waitForTimeout(300);
      await page.screenshot({ path: path.join(outDir, `${v.name}-${theme}-fold.png`) });
      await page.screenshot({ path: path.join(outDir, `${v.name}-${theme}-full.png`), fullPage: true });

      if (errors.length) problems.push(`${v.name}/${theme}: konzole ${JSON.stringify(errors.slice(0, 4))}`);
      if (bad.length) problems.push(`${v.name}/${theme}: chybné zdroje ${JSON.stringify(bad.slice(0, 4))}`);
      await ctx.close();
    }
  }
} finally {
  await browser.close();
}

if (problems.length) {
  console.log("NÁLEZY:");
  problems.forEach((p) => console.log(" - " + p));
  process.exitCode = 1;
} else {
  console.log("OK: bez nálezů");
}
