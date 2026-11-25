import { render, screen, within } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import RecordDetail from "@/features/catalogue/RecordDetail.svelte";
import RecordList from "@/features/catalogue/RecordList.svelte";
import * as m from "@/locales/messages.gen";
import { buildFixtures, recordFixtures } from "@/workbench/fixtures";

describe("catalogue", () => {
  it("explains an empty record list and links to the next page", () => {
    render(RecordList, { records: [], prevHref: null, nextHref: "/records?after_id=4" });
    expect(screen.getByText(m.recordsEmpty())).toBeVisible();
    const pages = screen.getByRole("navigation", { name: m.navPagination() });
    expect(within(pages).getByRole("link", { name: m.commonNext() })).toHaveAttribute(
      "href",
      "/records?after_id=4",
    );
  });

  it("lists records as linked cards under the page title", () => {
    const records = recordFixtures();
    render(RecordList, { records, prevHref: null, nextHref: null });
    const heading = screen.getByRole("heading", { level: 2, name: m.storySmallestIris() });
    expect(within(heading).getByRole("link")).toHaveAttribute("href", "/records/smallest-iris");
    expect(screen.getByText(m.recordRefreshPending(), { exact: false })).toBeVisible();
  });

  it("shows one holder apart from contenders and tied builds, or says there is none", () => {
    const [fastest, smallest] = recordFixtures();
    const [holder, tied, contender] = buildFixtures();
    if (!fastest || !smallest || !holder || !tied || !contender)
      throw new Error("The workbench needs two record and three build fixtures.");
    const { unmount } = render(RecordDetail, {
      record: {
        ...fastest,
        holder_builds: [holder],
        tied_builds: [tied],
        contender_builds: [contender],
        gaps: [{ build_id: contender.id, field: "opening_visible" }],
      },
    });
    expect(screen.getByRole("heading", { level: 1, name: fastest.title })).toBeVisible();
    const holders = screen.getByRole("region", { name: m.recordHolders() });
    expect(within(holders).getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByText(m.recordChangeable({ count: 1 }))).toBeVisible();
    const contenders = screen.getByRole("region", { name: m.recordContenders() });
    expect(
      within(contenders).getByText(m.buildMissingFacts({ facts: "Visible opening time" })),
    ).toBeVisible();
    const ties = screen.getByRole("region", { name: m.recordTied() });
    expect(within(ties).getAllByRole("listitem")).toHaveLength(1);
    unmount();

    render(RecordDetail, { record: { ...smallest, holder_builds: [] } });
    expect(screen.getByText(m.recordNoHolders())).toBeVisible();
  });

  it("explains a record shared because completion dates are unknown", () => {
    const [record] = recordFixtures();
    const builds = buildFixtures().slice(0, 2);
    if (!record) throw new Error("The workbench needs a record fixture.");
    render(RecordDetail, {
      record: {
        ...record,
        holder_builds: builds,
        gaps: builds.map((build) => ({ build_id: build.id, field: "completion_at" as const })),
      },
    });
    const holders = screen.getByRole("region", { name: m.recordCoHolders() });
    expect(within(holders).getByText(m.recordCoHoldersUndated())).toBeVisible();
    expect(
      within(holders).getAllByText(m.buildMissingFacts({ facts: "Completion date" })),
    ).toHaveLength(2);
    expect(screen.queryByRole("region", { name: m.recordContenders() })).toBeNull();
  });
});
