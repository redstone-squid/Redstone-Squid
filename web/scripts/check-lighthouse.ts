import { spawn, type ChildProcess } from "node:child_process";
import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";

const pages = [
  ["home", "http://127.0.0.1:4321/"],
  ["builds", "http://127.0.0.1:4321/builds"],
  ["build-detail", "http://127.0.0.1:4321/builds/1"],
  ["record-detail", "http://127.0.0.1:4321/records/11"],
  ["chinese-about", "http://127.0.0.1:4321/zh-cn/about"],
] as const;
const thresholds = { performance: 0.9, "best-practices": 0.95, seo: 0.95 } as const;
const servers: ChildProcess[] = [];

function stopServers(): void {
  for (const server of servers) server.kill("SIGTERM");
}

function waitForExit(child: ChildProcess): Promise<void> {
  return new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`Lighthouse exited with code ${code ?? "unknown"}.`));
    });
  });
}

try {
  servers.push(
    spawn("bun", ["tests/fixtures/api.ts"], {
      stdio: "inherit",
      env: { ...process.env, FIXTURE_API_PORT: "8787" },
    }),
    spawn("node", ["dist/server/entry.mjs"], {
      stdio: "inherit",
      env: {
        ...process.env,
        HOST: "127.0.0.1",
        PORT: "4321",
        API_BASE_URL: "http://0.0.0.0:8787",
        SITE_URL: "https://catalogue.redstone-squid.org",
        DISCORD_COMMUNITY_URL: "https://discord.gg/redstone",
        BOT_INVITE_URL: "https://discord.com/oauth2/authorize?client_id=fixture",
      },
    }),
  );

  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const [api, app] = await Promise.all([
        fetch("http://127.0.0.1:8787/livez"),
        fetch("http://127.0.0.1:4321/about"),
      ]);
      if (api.ok && app.ok) break;
    } catch {
      // The fixture processes may still be binding their ports.
    }
    if (attempt === 59) throw new Error("Catalogue fixture servers did not become ready.");
    await new Promise((resolve) => setTimeout(resolve, 250));
  }

  const reportDirectory = path.resolve(".lighthouseci/reports");
  await mkdir(reportDirectory, { recursive: true });
  for (const [name, url] of pages) {
    const reportPath = path.join(reportDirectory, `${name}.json`);
    const lighthouse = spawn(
      "bunx",
      [
        "lighthouse",
        url,
        "--quiet",
        "--preset=desktop",
        "--only-categories=performance,best-practices,seo",
        "--output=json",
        `--output-path=${reportPath}`,
        "--chrome-flags=--headless --no-sandbox --disable-dev-shm-usage",
      ],
      { stdio: "inherit", env: process.env },
    );
    await waitForExit(lighthouse);
    const report = JSON.parse(await readFile(reportPath, "utf8")) as {
      categories: Record<string, { score: number | null }>;
    };
    for (const [category, threshold] of Object.entries(thresholds)) {
      const score = report.categories[category]?.score;
      if (score === null || score === undefined || score < threshold) {
        throw new Error(`${name} ${category} score ${score ?? "missing"} is below ${threshold}.`);
      }
      console.info(`${name} ${category}: ${Math.round(score * 100)}`);
    }
  }
} finally {
  stopServers();
}
