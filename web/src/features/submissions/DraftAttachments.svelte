<script lang="ts">
  import { BrowserApi, errorMessage } from "@/api-client/mutation";
  import { ApiError } from "@/api-client/api-error";
  import { submissionSchematicUpload, submissionMediaUpload } from "@/generated/sdk.gen";
  import type { DraftMediaListResponse, DraftSchematicResponse } from "@/generated/types.gen";
  import * as m from "@/locales/messages.gen";
  import FileDrop from "@/ui/forms/FileDrop.svelte";

  type Props = {
    baseUrl: string;
    draftId: string;
    editable?: boolean;
    schematics: DraftSchematicResponse[];
    media: DraftMediaListResponse;
  };
  let { baseUrl, draftId, schematics, media, editable = true }: Props = $props();
  const uploaded = $state<string[]>([]);
  let busy = $state(false);
  let error = $state("");
  const keys = new WeakMap<File, string>();
  async function upload(files: File[], rejected: File[], schematic: boolean) {
    if (rejected.length > 0) {
      error = m.submissionFileRejected();
      return;
    }
    const file = files[0];
    if (!file || busy) return;
    const key = keys.get(file) ?? crypto.randomUUID();
    keys.set(file, key);
    busy = true;
    error = "";
    try {
      const api = new BrowserApi(baseUrl);
      const path = { draft_id: draftId };
      if (schematic) {
        await api.mutate(
          (headers) =>
            submissionSchematicUpload({
              client: api.client,
              headers,
              path: { ...path, upload_id: key },
              query: { filename: file.name },
              body: file,
            }),
          { idempotencyKey: key },
        );
      } else {
        await api.mutate(
          (headers) =>
            submissionMediaUpload({
              client: api.client,
              headers: { ...headers, "Content-Type": file.type },
              path: { ...path, kind: file.type.startsWith("video/") ? "video" : "image" },
              query: { upload_id: key },
              body: file,
            }),
          { idempotencyKey: key },
        );
      }
      uploaded.push(file.name);
    } catch (caught) {
      error = errorMessage(ApiError.from(caught));
    } finally {
      busy = false;
    }
  }
</script>

<ul>
  {#each schematics.filter((s) => !s.discarded) as source (source.id)}<li>
      {source.filename} · {source.state}
    </li>{/each}
  {#each media.media as file (file.id)}<li>{file.kind} · {file.status}</li>{/each}
  {#each uploaded as name, index (index)}<li>{name}</li>{/each}
</ul>
{#if editable}
  <FileDrop
    label={m.submissionSchematic()}
    labels={{ drop: m.submissionChooseFile(), browse: m.submissionChooseFile() }}
    disabled={busy}
    onFiles={(files, rejected) => void upload(files, rejected, true)}
  />
  <FileDrop
    label={m.submissionMedia()}
    accept="image/*,video/*"
    maxBytes={media.limits.max_upload_bytes}
    labels={{ drop: m.submissionChooseFile(), browse: m.submissionChooseFile() }}
    disabled={busy}
    onFiles={(files, rejected) => void upload(files, rejected, false)}
  />
{/if}
{#if error}<p role="alert">{error}</p>{/if}
