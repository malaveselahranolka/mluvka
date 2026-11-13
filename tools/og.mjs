// Vyrenderuje docs/og.png z tools/og.html. Potrebuje bezici server nad korenem repa.
import path from "node:path"; import fs from "node:fs"; import { pathToFileURL } from "node:url";
const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_CORE).href);
const b = await chromium.launch({ channel: "chrome", headless: true });
const p = await b.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
await p.goto("http://127.0.0.1:4173/tools/og.html", { waitUntil: "networkidle" });
await p.waitForTimeout(600);
await p.screenshot({ path: "docs/og.png" });
await b.close();
