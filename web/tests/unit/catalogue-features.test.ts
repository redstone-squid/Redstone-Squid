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

  it("shows a record's holders, or says there are none", () => {
    const [fastest, smallest] = recordFixtures();
    if (!fastest || !smallest) throw new Error("The workbench needs two record fixtures.");
    const builds = buildFixtures();
    const { unmount } = render(RecordDetail, {
      record: { ...fastest, holder_builds: builds },
    });
    expect(screen.getByRole("heading", { level: 1, name: fastest.title })).toBeVisible();
    const holders = screen.getByRole("region", { name: m.recordHolders() });
    expect(within(holders).getAllByRole("listitem")).toHaveLength(builds.length);
    expect(within(holders).getByText(m.buildTimingUnknown())).toBeVisible();
    unmount();

    render(RecordDetail, { record: { ...smallest, holder_builds: [] } });
    expect(screen.getByText(m.recordNoHolders())).toBeVisible();
  });
});
