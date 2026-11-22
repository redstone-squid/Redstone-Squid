import { spawn, type ChildProcess } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import {
  API_PORT,
  APP_URL,
  FIXTURE_API_COMMAND,
  appEnvironment,
  fixtureApiEnvironment,
} from "@tests/support/servers";

const pages = [
  { name: "home", route: "/", profile: "desktop", budgets: [] },
  { name: "chinese-home", route: "/zh-cn", profile: "desktop", budgets: [] },
  {
    name: "mobile-tag",
    route: "/zh-cn/tags/zero-tick",
    profile: "mobile",
    // A text-only catalogue page should leave room for media on other routes.
    budgets: [
      ["lcpMs", 1_500],
      ["cls", 0.05],
      ["transferBytes", 80 * 1024],
    ],
  },
  {
    name: "mobile-draft",
    route: "/design-system-trial",
    profile: "mobile",
    budgets: [
      ["lcpMs", 4_500],
      ["cls", 0.1],
      ["tbtMs", 200],
    ],
  },
] satisfies {
  name: string;
  route: string;
  profile: "desktop" | "mobile";
  budgets: [Metric, number][];
}[];
const thresholds = { performance: 0.9, "best-practices": 0.95, seo: 0.95 } as const;
const metricAudits = {
  lcpMs: "largest-contentful-paint",
  cls: "cumulative-layout-shift",
  tbtMs: "total-blocking-time",
  fcpMs: "first-contentful-paint",
  ttfbMs: "server-response-time",
  transferBytes: "total-byte-weight",
} as const;
type Metric = keyof typeof metricAudits;
type Metrics = Record<Metric, number>;
type Report = {
  categories: Record<string, { score: number | null }>;
  audits: Record<string, { numericValue?: number }>;
  runtimeError?: { message: string };
};
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

function readMetrics(report: Report, name: string): Metrics {
  const metrics = {} as Metrics;
  for (const metric of Object.keys(metricAudits) as Metric[]) {
    const value = report.audits[metricAudits[metric]]?.numericValue;
    if (value === undefined || !Number.isFinite(value)) {
      throw new Error(`${name} is missing Lighthouse metric ${metricAudits[metric]}.`);
    }
    metrics[metric] = value;
  }
  return metrics;
}

/** Use the middle of three independent mobile runs to reduce sensitivity to runner load. */
function medianMetrics(samples: Metrics[]): Metrics {
  const metrics = {} as Metrics;
  for (const metric of Object.keys(metricAudits) as Metric[]) {
    const values = samples.map((sample) => sample[metric]).sort((a, b) => a - b);
    const middle = values[Math.floor(values.length / 2)];
    if (middle === undefined) throw new Error("No Lighthouse samples.");
    metrics[metric] = middle;
  }
  return metrics;
}

try {
  servers.push(
    spawn(FIXTURE_API_COMMAND[0], [FIXTURE_API_COMMAND[1]], {
      stdio: "inherit",
      env: { ...process.env, ...fixtureApiEnvironment },
    }),
    spawn("bun", ["dist/server/entry.mjs"], {
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
  const summary: Record<
    string,
    { route: string; profile: string; median: Metrics; budgets: Partial<Metrics> }
  > = {};
  const failures: string[] = [];
  for (const { name, route, profile, budgets } of pages) {
    const samples: Metrics[] = [];
    const desktop = profile === "desktop";
    for (let run = 1; run <= (desktop ? 1 : 3); run += 1) {
      const reportPath = path.join(
        reportDirectory,
        desktop ? `${name}.json` : `${name}-${run}.json`,
      );
      const lighthouse = spawn(
        "bunx",
        [
          "lighthouse",
          `${APP_URL}${route}`,
          "--quiet",
          ...(desktop
            ? ["--preset=desktop", "--only-categories=performance,best-practices,seo"]
            : ["--only-categories=performance"]),
          "--output=json",
          `--output-path=${reportPath}`,
          "--chrome-flags=--headless --no-sandbox --disable-dev-shm-usage",
        ],
        { stdio: "inherit", env: process.env },
      );
      await waitForExit(lighthouse);
      const report = JSON.parse(await readFile(reportPath, "utf8")) as Report;
      if (report.runtimeError !== undefined) {
        throw new Error(`${name}: ${report.runtimeError.message}`);
      }
      samples.push(readMetrics(report, name));
      if (desktop) {
        for (const [category, threshold] of Object.entries(thresholds)) {
          const score = report.categories[category]?.score;
          if (score === null || score === undefined || score < threshold) {
            failures.push(`${name} ${category} score ${score ?? "missing"} is below ${threshold}.`);
          }
          console.info(
            `${name} ${category}: ${score == null ? "missing" : Math.round(score * 100)}`,
          );
        }
      }
    }
    const median = medianMetrics(samples);
    for (const [metric, limit] of budgets) {
      if (median[metric] > limit) {
        failures.push(`${name} median ${metric} ${median[metric].toFixed(3)} exceeds ${limit}.`);
      }
    }
    summary[name] = { route, profile, median, budgets: Object.fromEntries(budgets) };
    console.info(`${name}:`, median);
  }
  await writeFile(path.join(reportDirectory, "summary.json"), JSON.stringify(summary, null, 2));
  if (failures.length > 0) throw new Error(failures.join("\n"));
} finally {
  stopServers();
}
