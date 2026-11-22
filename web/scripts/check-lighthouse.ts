import { spawn, type ChildProcess } from "node:child_process";
import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import {
  API_PORT,
  APP_URL,
  FIXTURE_API_COMMAND,
  appEnvironment,
  fixtureApiEnvironment,
} from "../tests/support/servers";

const pages = [
  ["home", `${APP_URL}/`],
  ["chinese-home", `${APP_URL}/zh-cn`],
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
    spawn(FIXTURE_API_COMMAND[0], [FIXTURE_API_COMMAND[1]], {
      stdio: "inherit",
      env: { ...process.env, ...fixtureApiEnvironment },
    }),
    spawn("node", ["dist/server/entry.mjs"], {
      stdio: "inherit",
      env: { ...process.env, ...appEnvironment },
    }),
  );

  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const [api, app] = await Promise.all([
        fetch(`http://127.0.0.1:${API_PORT}/livez`),
        fetch(`${APP_URL}/about`),
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
