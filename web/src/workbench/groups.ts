import * as m from "../locales/messages.gen";

/**
 * The workbench pages, one per folder of `src/ui`. Each has a stories component in
 * `stories/`, imported by `src/pages/design-system/[group].astro`.
 */
export const GROUPS = {
  layout: { title: m.storyLayout, description: m.workbenchLayoutDescription },
  content: { title: m.storyContent, description: m.workbenchContentDescription },
  actions: { title: m.storyActions, description: m.workbenchActionsDescription },
  feedback: {
    title: m.storyFeedback,
    description: m.workbenchFeedbackDescription,
  },
  data: { title: m.storyData, description: m.workbenchDataDescription },
  navigation: {
    title: m.storyNavigation,
    description: m.workbenchNavigationDescription,
  },
  forms: { title: m.storyForms, description: m.workbenchFormsDescription },
  table: { title: m.storyTable, description: m.workbenchTableDescription },
  chart: {
    title: m.storyChart,
    description: m.workbenchChartDescription,
  },
  overlays: {
    title: m.storyOverlays,
    description: m.workbenchOverlaysDescription,
  },
  patterns: { title: m.storyPatterns, description: m.workbenchPatternsDescription },
} as const;
export type Group = keyof typeof GROUPS;

export function isGroup(value: string | undefined): value is Group {
  return value !== undefined && Object.hasOwn(GROUPS, value);
}
