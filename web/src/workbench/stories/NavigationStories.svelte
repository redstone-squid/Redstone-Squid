<script lang="ts">
  import LayoutGrid from "@lucide/svelte/icons/layout-grid";
  import Table from "@lucide/svelte/icons/table";
  import Bell from "@lucide/svelte/icons/bell";
  import User from "@lucide/svelte/icons/user";
  import * as m from "@/locales/messages.gen";
  import Breadcrumbs from "@/ui/navigation/Breadcrumbs.svelte";
  import NavList from "@/ui/navigation/NavList.svelte";
  import Pagination from "@/ui/navigation/Pagination.svelte";
  import SegmentedControl from "@/ui/navigation/SegmentedControl.svelte";
  import Tabs from "@/ui/navigation/Tabs.svelte";
  import Story from "./Story.svelte";
  import Variant from "./Variant.svelte";

  // Each specimen needs its own name: several navigation landmarks share this page.
  const labels = (nav: string) => ({ nav, previous: m.commonPrevious(), next: m.commonNext() });
</script>

<Story name="Breadcrumbs" source="navigation/Breadcrumbs">
  <Breadcrumbs
    label="Breadcrumb"
    items={[
      { label: "Builds", href: "#breadcrumbs" },
      { label: "Doors", href: "#breadcrumbs" },
      { label: "Copper Bolt 5×5" },
    ]}
  />
</Story>

<Story name="Tabs" source="navigation/Tabs" description="Links between views kept in the URL.">
  <Tabs
    label="Search scope"
    items={[
      { label: "All", href: "#tabs", current: true, count: "312" },
      { label: "Builds", href: "#tabs-builds", count: "280" },
      { label: "Records", href: "#tabs-records", count: "32" },
    ]}
  />
</Story>

<Story name="SegmentedControl" source="navigation/SegmentedControl">
  <SegmentedControl
    label="View"
    items={[
      { label: "Cards", href: "#segmentedcontrol", current: true, icon: LayoutGrid },
      { label: "Table", href: "#segmentedcontrol-table", icon: Table },
    ]}
  />
</Story>

<Story name="NavList" source="navigation/NavList">
  <NavList
    label="Account"
    class="max-w-64"
    groups={[
      {
        heading: "Account",
        items: [
          { label: "Profile", href: "#navlist", current: true, icon: User },
          { label: "Notifications", href: "#navlist-notifications", count: "3", icon: Bell },
        ],
      },
    ]}
  />
</Story>

<Story
  name="Pagination"
  source="navigation/Pagination"
  description="Hidden when there is one page."
>
  <Variant label="previous and next">
    <Pagination
      prevHref="#pagination-prev"
      nextHref="#pagination-next"
      range="21–40 of 312"
      labels={labels(m.navPagination())}
    />
  </Variant>
  <Variant label="first page">
    <Pagination nextHref="#pagination-next" labels={labels("First page")} />
  </Variant>
  <Variant label="numbered">
    <Pagination
      prevHref="#pagination-1"
      nextHref="#pagination-3"
      range="21–40 of 60"
      labels={labels("Numbered pages")}
      pages={[
        { label: "1", href: "#pagination-1", name: "Page 1" },
        { label: "2", href: "#pagination-2", name: "Page 2", current: true },
        { label: "3", href: "#pagination-3", name: "Page 3" },
      ]}
    />
  </Variant>
</Story>
