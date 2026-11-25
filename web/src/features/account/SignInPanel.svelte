<script lang="ts">
  import type { BrowserProvider } from "@/generated/types.gen";
  import * as m from "@/locales/messages.gen";
  import Button from "@/ui/actions/Button.svelte";
  import Notice from "@/ui/feedback/Notice.svelte";
  import Layout from "@/ui/layout/Layout.svelte";

  /**
   * The sign-in choices. Each provider is a plain link to the API, which runs the OAuth
   * round trip and comes back to `/sign-in` with `auth_error` when it fails.
   */
  type Props = {
    providers: { provider: BrowserProvider; href: string }[];
    /** The callback's `auth_error`, if the last attempt failed. */
    authError: string | null;
  };
  let { providers, authError }: Props = $props();

  const providerNames: Record<string, () => string> = { discord: m.signInProviderDiscord };

  /** The `auth_error` values the API's OAuth callback sends. */
  type AuthError = "cancelled" | "invalid_state" | "provider_unavailable" | "identity_in_use";
  const authErrors = {
    cancelled: m.signInErrorCancelled,
    invalid_state: m.signInErrorInvalidState,
    provider_unavailable: m.signInErrorProviderUnavailable,
    identity_in_use: m.signInErrorIdentityInUse,
  } satisfies Record<AuthError, () => string>;
  // A code this build does not know still tells the visitor that sign-in failed.
  const errorMessage = $derived(
    authError === null
      ? null
      : (Object.hasOwn(authErrors, authError)
          ? authErrors[authError as AuthError]
          : m.signInErrorGeneric)(),
  );
</script>

<Layout pattern="stack">
  {#if errorMessage !== null}
    <Notice tone="danger" role="alert">{errorMessage}</Notice>
  {/if}
  {#if providers.length === 0}
    <Notice tone="warning">{m.signInUnavailable()}</Notice>
  {:else}
    <Layout as="ul" pattern="stack" aria-label={m.signInProviders()}>
      {#each providers as { provider, href } (provider.id)}
        <li>
          <Button variant="primary" {href}>
            {m.signInContinue({ provider: providerNames[provider.id]?.() ?? provider.id })}
          </Button>
        </li>
      {/each}
    </Layout>
  {/if}
</Layout>
