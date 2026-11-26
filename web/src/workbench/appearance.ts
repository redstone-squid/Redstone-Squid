import { Route } from "@/navigation/route";
import { parseContrast, parseTheme, type Contrast, type Theme } from "@/ui/themes";
import type { Group } from "./groups";

const scope = Symbol("workbench");
type Appearance = { theme: Theme; contrast: Contrast };
type Request = { url: URL; locals: Pick<App.Locals, "theme" | "contrast"> };
type PatternFields = { q: string; scope: string; category: string; holders: string };
type TrialFields = { scenario: "retry" | "upload-retry" };

function appearance({ url, locals }: Request) {
  const theme = parseTheme(url.searchParams.get("theme")) ?? locals.theme ?? "light";
  const contrast = parseContrast(url.searchParams.get("contrast")) ?? null;
  return { theme, contrast };
}

const index = new Route<Appearance>("/design-system", scope);
const trial = new Route<Appearance & TrialFields>("/design-system-trial", scope);
const group = (name: Group) => new Route<Appearance>(`/design-system/${name}`, scope);
const patterns = new Route<Appearance & PatternFields>(group("patterns").path, scope);

export const workbench = {
  index,
  trial,
  group,
  home: new Route<Record<string, never>>("/"),
  at(request: Request) {
    const retained = appearance(request);
    return {
      ...retained,
      effectiveContrast: retained.contrast ?? request.locals.contrast,
      nav: index.at(request.url, retained),
    };
  },
  trialAt(request: Request) {
    return trial.at(request.url, appearance(request));
  },
  patternForms(request: Request) {
    const nav = patterns.at(request.url, appearance(request));
    return {
      search: nav.form(["q"]),
      scopedSearch: nav.form(["q"], { scope: "builds" }),
      filter: nav.form(["category", "holders"]),
      scopedFilter: nav.form(["category", "holders"], { q: "door" }),
    };
  },
};
