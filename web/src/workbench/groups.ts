/**
 * The workbench pages, one per folder of `src/ui`. Each has a stories component in
 * `stories/`, imported by `src/pages/design-system/[group].astro`.
 */
export const GROUPS = {
  layout: { title: "Layout", description: "Page width, rhythm, and responsive arrangement." },
  content: { title: "Content", description: "Headings, text, links, code, and icons." },
  actions: { title: "Actions", description: "Buttons and links styled as commands." },
  feedback: {
    title: "Feedback",
    description: "Badges, chips, notices, progress, and loading, empty, and error states.",
  },
  data: { title: "Data", description: "Cards, lists, images, timelines, and diffs." },
  navigation: {
    title: "Navigation",
    description: "Breadcrumbs, tabs, segmented controls, navigation lists, and pagination.",
  },
  forms: { title: "Forms", description: "Fields, choices, suggestions, and file selection." },
  table: { title: "Table", description: "Server-sorted data tables with grouped rows." },
  chart: {
    title: "Chart",
    description: "Series, ranges, and distributions, each with a data table.",
  },
  overlays: {
    title: "Overlays",
    description: "Dialogs, sheets, menus, popovers, and tooltips.",
  },
  patterns: { title: "Patterns", description: "Page headers, sections, and catalogue cards." },
} as const;
export type Group = keyof typeof GROUPS;

export function isGroup(value: string | undefined): value is Group {
  return value !== undefined && Object.hasOwn(GROUPS, value);
}
