<script lang="ts">
  import type { SubmissionDetail, VoteSessionDetail } from "@/generated/types.gen";
  import { localizePath } from "@/locales/locale";
  import * as m from "@/locales/messages.gen";
  import Button from "@/ui/actions/Button.svelte";
  import DescriptionList from "@/ui/data/DescriptionList.svelte";
  import TextField from "@/ui/forms/TextField.svelte";
  import PageHeader from "@/ui/patterns/PageHeader.svelte";
  import Section from "@/ui/patterns/Section.svelte";
  import { factRows, statusLabel } from "./status";

  /**
   * A submission as its submitter and reviewers see it: the submitted details, the reviewed
   * revision once there is one, and, as the viewer's permissions allow, the approve and reject
   * form and the review vote. Both forms post to the current page, which handles them.
   * `previewHref` opens the record prediction for this submission.
   */
  type Props = {
    title: string;
    detail: SubmissionDetail;
    vote: VoteSessionDetail | null;
    previewHref: string;
  };
  let { title, detail, vote, previewHref }: Props = $props();
  const row = $derived(detail.submission);
</script>

<PageHeader
  {title}
  description={[
    row.kind === "edit" ? m.submissionEdit() : m.submissionNew(),
    statusLabel(row.status),
  ].join(" · ")}
/>
<Section title={m.submissionReviewFacts()}>
  <DescriptionList items={factRows(detail.facts)} />
</Section>
{#if detail.feedback}<p>{detail.feedback}</p>{/if}
{#if row.published}
  <Button href={localizePath(`/builds/${row.build_id}`)}>{m.submissionViewPublic()}</Button>
{/if}
{#if Object.keys(detail.reviewed_facts).length > 0}
  <Section title={m.submissionReviewedVersion({ revision: detail.review_revision ?? 0 })}>
    <DescriptionList items={factRows(detail.reviewed_facts)} />
  </Section>
{/if}
{#if detail.can_approve === true || detail.can_reject === true}
  <form method="post">
    <input type="hidden" name="revision" value={detail.review_revision} />
    {#if detail.can_reject === true}
      <TextField label={m.submissionFeedback()} name="feedback" maxlength={2000} />
    {/if}
    {#if detail.can_approve === true}
      <Button type="submit" name="decision" value="approve">{m.submissionApprove()}</Button>
    {/if}
    {#if detail.can_reject === true}
      <Button type="submit" name="decision" value="reject">{m.submissionReject()}</Button>
    {/if}
  </form>
{/if}
{#if vote?.tallies}
  <p>
    {m.submissionTally({
      up: vote.tallies.upvotes,
      down: vote.tallies.downvotes,
      pass: vote.pass_threshold ?? 0,
      fail: Math.abs(vote.fail_threshold ?? 0),
    })}
  </p>
{/if}
{#if vote?.viewer?.eligible === true}
  <form method="post">
    <input type="hidden" name="vote_session_id" value={vote.id} />
    {#each vote.options as option (option.id)}
      <Button type="submit" name="option" value={option.id}>{option.label ?? option.id}</Button>
    {/each}
    {#if vote.own_selection}
      <Button type="submit" name="option" value="">{m.submissionWithdraw()}</Button>
    {/if}
  </form>
{/if}
<Button href={previewHref} target="_blank" rel="noopener noreferrer">{m.predictionTitle()}</Button>
