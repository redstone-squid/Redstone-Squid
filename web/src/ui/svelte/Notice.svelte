<script lang="ts">
  import type { Snippet } from "svelte";
  import { noticeStyles, type NoticeProps } from "@/ui/variants";
  let { title, tone, role, children }: NoticeProps & { children: Snippet } = $props();
</script>

<div class={noticeStyles({ tone })} {role}>
  {#if title}<strong>{title}</strong>{/if}
  {@render children()}
</div>
