import { useMemo, useState } from "react";

import type { ChallengeApprovalResponse } from "../generated/types.gen";
import {
  asSubmissionError,
  createMinecraftLinkApi,
  type ConsentApi,
  type MinecraftLinkApi,
  type SubmissionApiError,
} from "../lib/submission-api";
import type { RuntimeConfig } from "../lib/config";
import { type Locale, translate } from "../lib/i18n";
import ConsentGate from "./ConsentGate";

type Props = {
  locale: Locale;
  config: RuntimeConfig;
  initialCode?: string;
  api?: MinecraftLinkApi;
  consentApi?: ConsentApi;
};

const USER_CODE = /^[A-Za-z2-7-]{8,32}$/;

const copyFor = (locale: Locale) => ({
  code: translate(locale, "minecraftLink.code"),
  help: translate(locale, "minecraftLink.help"),
  approve: translate(locale, "minecraftLink.approve"),
  approving: translate(locale, "minecraftLink.approving"),
  invalid: translate(locale, "minecraftLink.invalid"),
  auth: translate(locale, "minecraftLink.auth"),
  consent: translate(locale, "minecraftLink.consent"),
  unavailable: translate(locale, "minecraftLink.unavailable"),
  signIn: translate(locale, "minecraftLink.signIn"),
  success: translate(locale, "minecraftLink.success"),
  paper: translate(locale, "minecraftLink.paper"),
  fabric: translate(locale, "minecraftLink.fabric"),
  another: translate(locale, "minecraftLink.another"),
});

function errorMessage(error: SubmissionApiError, locale: Locale): string {
  const copy = copyFor(locale);
  if (error.kind === "authentication") return copy.auth;
  if (error.kind === "consent") return copy.consent;
  if (error.kind === "unavailable") return copy.unavailable;
  return error.message;
}

export default function MinecraftLink({
  locale,
  config,
  initialCode = "",
  api: suppliedApi,
  consentApi,
}: Props) {
  const copy = copyFor(locale);
  const api = useMemo(
    () => suppliedApi ?? createMinecraftLinkApi(locale, config),
    [config, locale, suppliedApi],
  );
  const [code, setCode] = useState(USER_CODE.test(initialCode) ? initialCode.toUpperCase() : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<SubmissionApiError | string>();
  const [approval, setApproval] = useState<ChallengeApprovalResponse>();
  const [needsConsent, setNeedsConsent] = useState(false);
  const returnUrl = new URL(
    locale === "zh-CN" ? "/zh-cn/minecraft/link" : "/minecraft/link",
    config.siteUrl,
  );
  const normalizedReturnCode = code.trim().toUpperCase();
  if (USER_CODE.test(normalizedReturnCode))
    returnUrl.searchParams.set("code", normalizedReturnCode);
  const signInUrl = api.signInUrl(returnUrl.toString());

  const approve = async () => {
    const normalized = code.trim().toUpperCase();
    if (!USER_CODE.test(normalized)) {
      setError(copy.invalid);
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      setApproval(await api.approve(normalized));
      setNeedsConsent(false);
    } catch (caught) {
      const failure = asSubmissionError(caught);
      // Recoverable in place: show the notice, and on acceptance run the same approval again.
      if (failure.kind === "consent") setNeedsConsent(true);
      setError(failure);
    } finally {
      setBusy(false);
    }
  };

  if (approval) {
    return (
      <section className="surface minecraft-link-card" role="status">
        <p className="kicker">MINECRAFT / APPROVED</p>
        <h2>{copy.success}</h2>
        <p>
          {translate(locale, "minecraftLink.successBody", {
            origin: approval.origin === "paper" ? copy.paper : copy.fabric,
            uuid: approval.java_uuid,
          })}
        </p>
        <button
          className="button"
          type="button"
          onClick={() => {
            setApproval(undefined);
            setCode("");
          }}
        >
          {copy.another}
        </button>
      </section>
    );
  }

  const failure =
    typeof error === "string" ? error : error ? errorMessage(error, locale) : undefined;
  return (
    <section className="surface minecraft-link-card">
      {needsConsent && (
        <ConsentGate
          locale={locale}
          config={config}
          api={consentApi}
          onAccepted={() => {
            setNeedsConsent(false);
            setError(undefined);
            return approve();
          }}
          onCancel={() => setNeedsConsent(false)}
        />
      )}
      {failure && (
        <div className="submission-inline-error" role="alert">
          <p>{failure}</p>
          {typeof error !== "string" && error?.kind === "authentication" && (
            <a className="button button--primary" href={signInUrl}>
              {copy.signIn}
            </a>
          )}
        </div>
      )}
      <div className="field">
        <label htmlFor="minecraft-user-code">{copy.code}</label>
        <input
          id="minecraft-user-code"
          value={code}
          minLength={8}
          maxLength={32}
          pattern="[A-Za-z2-7-]{8,32}"
          autoComplete="one-time-code"
          autoCapitalize="characters"
          spellCheck={false}
          aria-describedby="minecraft-user-code-help"
          onChange={(event) => setCode(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void approve();
          }}
        />
        <small id="minecraft-user-code-help">{copy.help}</small>
      </div>
      <button
        className="button button--primary"
        type="button"
        disabled={busy}
        onClick={() => void approve()}
      >
        {busy ? copy.approving : copy.approve}
      </button>
    </section>
  );
}
