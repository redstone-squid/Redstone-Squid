<script lang="ts">
  /**
   * A machine-readable date. Messages format the visible `label` (such as "3 days ago") so
   * translators control it; `title` can carry the full date shown on hover.
   */
  type Props = { datetime: string; label: string; title?: string };
  let { datetime, label, title }: Props = $props();
</script>

<time {datetime} {title}>{label}</time>
