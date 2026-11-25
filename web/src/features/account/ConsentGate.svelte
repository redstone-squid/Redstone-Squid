<script lang="ts">
  import type { PrivacyNoticeDetail } from "@/generated/types.gen";
  import * as m from "@/locales/messages.gen";
  import Button from "@/ui/actions/Button.svelte";
  import Prose from "@/ui/content/Prose.svelte";
  import Text from "@/ui/content/Text.svelte";
  import Notice from "@/ui/feedback/Notice.svelte";
  import FormActions from "@/ui/forms/FormActions.svelte";
  import Layout from "@/ui/layout/Layout.svelte";

  /**
   * The privacy notice with accept and decline buttons. The form posts back to the current
   * page, so its `redirect_to` survives, and sends the notice version shown, so the API can
   * refuse an answer to a notice that has since changed.
   */
  type Props = {
    notice: PrivacyNoticeDetail;
    /** Why the last answer did not go through. */
    error: "stale" | "failed" | null;
  };
  let { notice, error }: Props = $props();

  const errors = { stale: m.consentErrorStale, failed: m.consentErrorFailed };
  // The API sends plain text with blank lines between paragraphs.
  const paragraphs = $derived(notice.body.split(/\n\s*\n/).map((paragraph) => paragraph.trim()));
</script>

<Layout pattern="stack">
  {#if error !== null}
    <Notice tone={error === "stale" ? "warning" : "danger"} role="alert">{errors[error]()}</Notice>
  {/if}
  <Prose>
    {#each paragraphs as paragraph, index (index)}
      <p>{paragraph}</p>
    {/each}
  </Prose>
  <Text size="sm" tone="muted">{m.consentVersion({ version: notice.version })}</Text>
  <form method="post">
    <!-- eslint-disable-next-line squid/no-raw-elements -- a hidden field has no control to render -->
    <input type="hidden" name="version" value={notice.version} />
    <FormActions>
      {#snippet primary()}
        <Button variant="primary" type="submit" name="decision" value="accept">
          {m.consentAccept()}
        </Button>
      {/snippet}
      {#snippet secondary()}
        <Button type="submit" name="decision" value="decline">{m.consentDecline()}</Button>
      {/snippet}
    </FormActions>
  </form>
</Layout>
