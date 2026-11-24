<script lang="ts">
  import { cn } from "@/ui/cn";

  type Change = "added" | "removed" | "changed" | "unchanged";
  type Row = { label: string; before: string | null; after: string | null; kind: Change };

  /**
   * Field-by-field changes, such as an edit or a correction proposal. Each row names its kind
   * of change in text, so color is never the only signal. Empty values show `labels.empty`.
   */
  type Props = {
    rows: Row[];
    caption: string;
    labels: { field: string; before: string; after: string; empty: string } & Record<
      Change,
      string
    >;
    showUnchanged?: boolean;
    class?: string;
  };
  let { rows, caption, labels, showUnchanged = false, class: className }: Props = $props();
  const shown = $derived(showUnchanged ? rows : rows.filter((row) => row.kind !== "unchanged"));
</script>

<div class={cn("min-w-0 overflow-x-auto rounded-md border border-line", className)}>
  <table class="w-full border-collapse text-sm">
    <caption class="sr-only">{caption}</caption>
    <thead class="bg-surface-muted text-start text-xs text-fg-muted">
      <tr>
        <th scope="col" class="px-3 py-2 text-start font-semibold">{labels.field}</th>
        <th scope="col" class="px-3 py-2 text-start font-semibold">{labels.before}</th>
        <th scope="col" class="px-3 py-2 text-start font-semibold">{labels.after}</th>
      </tr>
    </thead>
    <tbody class="divide-y divide-line">
      {#each shown as row (row.label)}
        <tr class="align-top">
          <th scope="row" class="px-3 py-2 text-start font-semibold">
            {row.label}
            <span class="block text-xs font-normal text-fg-muted">{labels[row.kind]}</span>
          </th>
          <td
            class={cn(
              "px-3 py-2 wrap-anywhere",
              row.kind !== "unchanged" && row.before !== null && "bg-danger-soft",
            )}
          >
            {#if row.before === null}<span class="text-fg-muted">{labels.empty}</span
              >{:else if row.kind === "unchanged"}{row.before}{:else}<del
                class="no-underline forced-colors:line-through">{row.before}</del
              >{/if}
          </td>
          <td
            class={cn(
              "px-3 py-2 wrap-anywhere",
              row.kind !== "unchanged" && row.after !== null && "bg-success-soft",
            )}
          >
            {#if row.after === null}<span class="text-fg-muted">{labels.empty}</span
              >{:else if row.kind === "unchanged"}{row.after}{:else}<ins
                class="no-underline forced-colors:underline">{row.after}</ins
              >{/if}
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>
