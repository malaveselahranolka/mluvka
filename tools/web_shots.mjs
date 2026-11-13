// Screenshoty okna Mluvky pro web (docs/shots/*.webp), světlé i tmavé.
// Spuštění: node tools/web_shots.mjs   (PLAYWRIGHT_CORE = cesta k playwright-core/index.mjs, když není v projektu)
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const { chromium } = await import(
  process.env.PLAYWRIGHT_CORE ? pathToFileURL(process.env.PLAYWRIGHT_CORE).href : "playwright-core"
);

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const uiDir = path.join(repoRoot, "mluvka", "ui");
const stubPath = path.join(repoRoot, "tools", "ui_stub.js");
const outDir = path.join(repoRoot, "docs", "shots");
const tmpDir = path.join(repoRoot, ".ai", "work", "shots_web_tmp");

fs.mkdirSync(outDir, { recursive: true });
fs.mkdirSync(tmpDir, { recursive: true });

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
    if (reqUrl.pathname === "/ui_stub.js") {
      res.writeHead(200, { "Content-Type": MIME[".js"] });
      res.end(fs.readFileSync(stubPath));
      return;
    }
    if (reqUrl.pathname === "/favicon.ico") {
      res.writeHead(204);
      res.end();
      return;
    }
    const clean = path.normalize(reqUrl.pathname.replace(/^\/+/, ""));
    const target = path.join(uiDir, clean);
    if (!target.startsWith(uiDir) || !fs.existsSync(target)) {
      res.writeHead(404);
      res.end("Not found");
      return;
    }
    res.writeHead(200, {
      "Content-Type": MIME[path.extname(target)] || "application/octet-stream",
    });
    res.end(fs.readFileSync(target));
  });

  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve({ server, baseUrl: `http://127.0.0.1:${server.address().port}/` }));
  });
}

const PAGES = ["prehled", "historie", "slovnik", "trenink", "soukromi"];

const { server, baseUrl } = await startDevServer();
const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 2,
  });
  const page = await context.newPage();
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.waitForSelector("#recent-list .history-row");

  for (const theme of ["light", "dark"]) {
    await page.emulateMedia({ colorScheme: theme });
    for (const p of PAGES) {
      await page.click(`.nav-item[data-page="${p}"]`);
      await page.waitForTimeout(120);
      const png = path.join(tmpDir, `${p}-${theme}.png`);
      await page.screenshot({ path: png });
      execFileSync("ffmpeg", [
        "-v", "error", "-i", png,
        "-c:v", "libwebp", "-quality", "82", "-y",
        path.join(outDir, `${p}-${theme}.webp`),
      ]);
      console.log(`${p}-${theme}.webp`);
    }
  }
} finally {
  await browser.close();
  server.close();
}
