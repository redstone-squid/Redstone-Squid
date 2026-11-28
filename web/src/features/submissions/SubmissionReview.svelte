<script lang="ts">
  import ExternalLink from "@lucide/svelte/icons/external-link";
  import ThumbsDown from "@lucide/svelte/icons/thumbs-down";
  import ThumbsUp from "@lucide/svelte/icons/thumbs-up";
  import type { SubmissionDetail, VoteSessionDetail } from "@/generated/types.gen";
  import { localizePath } from "@/locales/locale";
  import * as m from "@/locales/messages.gen";
  import Button from "@/ui/actions/Button.svelte";
  import Text from "@/ui/content/Text.svelte";
  import DescriptionList from "@/ui/data/DescriptionList.svelte";
  import Diff from "@/ui/data/Diff.svelte";
  import Badge from "@/ui/feedback/Badge.svelte";
  import Notice from "@/ui/feedback/Notice.svelte";
  import Progress from "@/ui/feedback/Progress.svelte";
  import TextareaField from "@/ui/forms/TextareaField.svelte";
  import Layout from "@/ui/layout/Layout.svelte";
  import PageHeader from "@/ui/patterns/PageHeader.svelte";
  import Section from "@/ui/patterns/Section.svelte";
  import { factRows, statusLabel, statusTone } from "./status";

  /**
   * A submission as its submitter and reviewers see it: the submitted details, what changed
   * in the reviewed revision, and, as the viewer's permissions allow, the review vote and the
   * approve and reject form. Both forms post to the current page, which handles them.
   * `previewHref` opens the record prediction for this submission in a new tab.
   */
  type Props = {
    title: string;
    detail: SubmissionDetail;
    vote: VoteSessionDetail | null;
    previewHref: string;
  };
  let { title, detail, vote, previewHref }: Props = $props();
  const row = $derived(detail.submission);
  const submitted = $derived(factRows(detail.facts));
  const changes = $derived.by(() => {
    const reviewed = factRows(detail.reviewed_facts);
    const keys = [...new Set([...submitted, ...reviewed].map((fact) => fact.key))];
    return keys.map((key) => {
      const before = submitted.find((fact) => fact.key === key);
      const after = reviewed.find((fact) => fact.key === key);
      const kind =
        before === undefined
          ? ("added" as const)
          : after === undefined
            ? ("removed" as const)
            : before.value === after.value
              ? ("unchanged" as const)
              : ("changed" as const);
      return {
        label: (after ?? before)?.term ?? key,
        before: before?.value ?? null,
        after: after?.value ?? null,
        kind,
      };
    });
  });
  const choices: Record<string, { label: () => string; icon: typeof ThumbsUp }> = {
    up: { label: m.submissionApprove, icon: ThumbsUp },
    down: { label: m.submissionReject, icon: ThumbsDown },
  };
  const deciding = $derived(detail.can_approve === true || detail.can_reject === true);
  const reviewing = $derived(vote?.tallies != null || vote?.viewer?.eligible === true || deciding);
</script>

<Layout pattern="stack" gap="xl">
  <PageHeader {title} eyebrow={row.kind === "edit" ? m.submissionEdit() : m.submissionNew()}>
    {#snippet meta()}
      <Badge tone={statusTone(row.status)} dot>{statusLabel(row.status)}</Badge>
      <Text as="span" size="sm" tone="muted">
        {m.submissionSubmittedOn({ date: row.submitted_at })}
      </Text>
    {/snippet}
    {#snippet actions()}
      <Button href={previewHref} target="_blank" rel="noopener noreferrer" iconEnd={ExternalLink}>
        {m.predictionTitle()}
      </Button>
    {/snippet}
    {#snippet notice()}
      {#if detail.feedback}
        <Notice variant="banner" tone="danger" title={m.submissionFeedbackTitle()}>
          {detail.feedback}
        </Notice>
      {:else if row.published}
        <Notice variant="banner" tone="success" title={m.submissionPublished()}>
          {#snippet actions()}
            <Button size="sm" href={localizePath(`/builds/${String(row.build_id)}`)}>
              {m.submissionViewPublic()}
            </Button>
          {/snippet}
        </Notice>
      {/if}
    {/snippet}
  </PageHeader>
  <Layout pattern="grid" min="card-wide" gap="xl" align="start">
    <Layout pattern="stack" gap="xl">
      <Section title={m.submissionReviewFacts()}>
        <DescriptionList layout="grid" items={submitted} />
      </Section>
      {#if Object.keys(detail.reviewed_facts).length > 0}
        <Section title={m.submissionReviewedVersion({ revision: detail.review_revision ?? 0 })}>
          {#if changes.some((change) => change.kind !== "unchanged")}
            <Diff
              rows={changes}
              caption={m.submissionReviewedChanges()}
              labels={{
                field: m.submissionDiffField(),
                before: m.submissionDiffSubmitted(),
                after: m.submissionDiffReviewed(),
                empty: m.submissionDiffEmpty(),
                added: m.submissionDiffAdded(),
                removed: m.submissionDiffRemoved(),
                changed: m.submissionDiffChanged(),
                unchanged: m.submissionDiffUnchanged(),
              }}
            />
          {:else}
            <Text tone="muted">{m.submissionReviewedSame()}</Text>
          {/if}
        </Section>
      {/if}
    </Layout>
    {#if reviewing}
      <Section title={m.submissionReviewTitle()} surface>
        <Layout pattern="stack" gap="lg">
          {#if vote?.tallies}
            <Layout pattern="stack" gap="sm">
              <Progress
                kind="meter"
                tone="success"
                label={m.submissionApprove()}
                value={vote.tallies.upvotes}
                max={Math.max(vote.pass_threshold ?? 1, 1)}
                valueLabel={m.submissionVotesOf({
                  count: vote.tallies.upvotes,
                  needed: vote.pass_threshold ?? 0,
                })}
              />
              <Progress
                kind="meter"
                tone="danger"
                label={m.submissionReject()}
                value={vote.tallies.downvotes}
                max={Math.max(Math.abs(vote.fail_threshold ?? 1), 1)}
                valueLabel={m.submissionVotesOf({
                  count: vote.tallies.downvotes,
                  needed: Math.abs(vote.fail_threshold ?? 0),
                })}
              />
            </Layout>
          {/if}
          {#if vote?.viewer?.eligible === true}
            <form method="post">
              <input type="hidden" name="vote_session_id" value={vote.id} />
              <Layout pattern="cluster" gap="xs">
                {#each vote.options as option (option.id)}
                  {@const choice = choices[option.choice]}
                  <Button
                    type="submit"
                    name="option"
                    value={option.id}
                    icon={choice?.icon}
                    variant={vote.own_selection?.option_id === option.id ? "primary" : "secondary"}
                    aria-pressed={vote.own_selection?.option_id === option.id}
                  >
                    {option.label ?? choice?.label() ?? option.id}
                  </Button>
                {/each}
                {#if vote.own_selection}
                  <Button type="submit" name="option" value="" variant="quiet">
                    {m.submissionWithdraw()}
                  </Button>
                {/if}
              </Layout>
            </form>
          {/if}
          {#if deciding}
            <form method="post">
              <input type="hidden" name="revision" value={detail.review_revision} />
              <Layout pattern="stack" gap="md">
                {#if detail.can_reject === true}
                  <TextareaField
                    label={m.submissionFeedback()}
                    name="feedback"
                    maxlength={2000}
                    rows={3}
                  />
                {/if}
                <!-- Not FormActions: its container query inside this narrow panel kept Chromium
                     relaying out the page for seconds at phone widths. -->
                <Layout pattern="cluster" gap="xs">
                  {#if detail.can_approve === true}
                    <Button type="submit" name="decision" value="approve" variant="primary">
                      {m.submissionDecideApprove()}
                    </Button>
                  {/if}
                  {#if detail.can_reject === true}
                    <!-- The accent is red, so a danger button would read like the approve button. -->
                    <Button type="submit" name="decision" value="reject">
                      {m.submissionDecideReject()}
                    </Button>
                  {/if}
                </Layout>
              </Layout>
            </form>
          {/if}
        </Layout>
      </Section>
    {/if}
  </Layout>
</Layout>
