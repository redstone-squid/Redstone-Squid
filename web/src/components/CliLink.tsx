import { useEffect, useMemo, useState } from "react";

import type { CliEnrollmentApprovalResponse } from "../generated/types.gen";
import {
  asSubmissionError,
  createCliLinkApi,
  type CliLinkApi,
  type ConsentApi,
  type SubmissionApiError,
} from "../lib/submission-api";
import type { RuntimeConfig } from "../lib/config";
import { type Locale, translate } from "../lib/i18n";
import ConsentGate from "./ConsentGate";

type Props = {
  locale: Locale;
  config: RuntimeConfig;
  initialCode?: string;
  api?: CliLinkApi;
  consentApi?: ConsentApi;
};

const USER_CODE = /^[A-Za-z0-9-]{8,32}$/;
const SESSION_CODE_KEY = "squid.cli.enrollment_code";

const copyFor = (locale: Locale) => ({
  code: translate(locale, "cliLink.code"),
  help: translate(locale, "cliLink.help"),
  review: translate(locale, "cliLink.review"),
  reviewing: translate(locale, "cliLink.reviewing"),
  invalid: translate(locale, "cliLink.invalid"),
  auth: translate(locale, "cliLink.auth"),
  consent: translate(locale, "cliLink.consent"),
  unavailable: translate(locale, "cliLink.unavailable"),
  signIn: translate(locale, "cliLink.signIn"),
  device: translate(locale, "cliLink.device"),
  fingerprint: translate(locale, "cliLink.fingerprint"),
  warning: translate(locale, "cliLink.warning"),
  approve: translate(locale, "cliLink.approve"),
  approving: translate(locale, "cliLink.approving"),
  success: translate(locale, "cliLink.success"),
  successBody: translate(locale, "cliLink.successBody"),
  another: translate(locale, "cliLink.another"),
});

function errorMessage(error: SubmissionApiError, locale: Locale): string {
  const copy = copyFor(locale);
  if (error.kind === "authentication") return copy.auth;
  if (error.kind === "consent") return copy.consent;
  if (error.kind === "unavailable") return copy.unavailable;
  return error.message;
}

function normalizedCode(value: string): string {
  const normalized = value.trim().toUpperCase();
  return USER_CODE.test(normalized) ? normalized : "";
}

function rememberCode(value: string): void {
  try {
    if (value) sessionStorage.setItem(SESSION_CODE_KEY, value);
    else sessionStorage.removeItem(SESSION_CODE_KEY);
  } catch {
    // Storage can be unavailable in hardened browsers; the visible form remains usable.
  }
}

function consumeFragmentCode(): string {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const code = normalizedCode(fragment.get("code") ?? "");
  if (window.location.hash) {
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  }
  if (code) {
    rememberCode(code);
    return code;
  }
  try {
    return normalizedCode(sessionStorage.getItem(SESSION_CODE_KEY) ?? "");
  } catch {
    return "";
  }
}

export default function CliLink({
  locale,
  config,
  initialCode = "",
  api: suppliedApi,
  consentApi,
}: Props) {
  const copy = copyFor(locale);
  const api = useMemo(
    () => suppliedApi ?? createCliLinkApi(locale, config),
    [config, locale, suppliedApi],
  );
  const [code, setCode] = useState(normalizedCode(initialCode));
  const [busy, setBusy] = useState<"review" | "approve">();
  const [error, setError] = useState<SubmissionApiError | string>();
  const [preview, setPreview] = useState<CliEnrollmentApprovalResponse>();
  const [approved, setApproved] = useState(false);
  const [needsConsent, setNeedsConsent] = useState(false);

  useEffect(() => {
    const restored = consumeFragmentCode();
    if (!restored) return;
    const update = window.setTimeout(() => setCode(restored), 0);
    return () => window.clearTimeout(update);
  }, []);

  const returnUrl = new URL(locale === "zh-CN" ? "/zh-cn/cli/link" : "/cli/link", config.siteUrl);
  const signInUrl = api.signInUrl(returnUrl.toString());

  const review = async () => {
    const normalized = normalizedCode(code);
    if (!normalized) {
      setError(copy.invalid);
      return;
    }
    rememberCode(normalized);
    setCode(normalized);
    setBusy("review");
    setError(undefined);
    setPreview(undefined);
    try {
      setPreview(await api.preview(normalized));
    } catch (caught) {
      setError(asSubmissionError(caught));
    } finally {
      setBusy(undefined);
    }
  };

  const approve = async () => {
    const normalized = normalizedCode(code);
    if (!normalized || !preview) return;
    setBusy("approve");
    setError(undefined);
    try {
      await api.approve(normalized);
      rememberCode("");
      setApproved(true);
      setNeedsConsent(false);
    } catch (caught) {
      const failure = asSubmissionError(caught);
      // Recoverable in place: show the notice, and on acceptance run the same approval again.
      if (failure.kind === "consent") setNeedsConsent(true);
      setError(failure);
    } finally {
      setBusy(undefined);
    }
  };

  if (approved) {
    return (
      <section className="surface cli-link-card" role="status">
        <p className="kicker">CLI / APPROVED</p>
        <h2>{copy.success}</h2>
        <p>{copy.successBody}</p>
        <button
          className="button"
          type="button"
          onClick={() => {
            setApproved(false);
            setPreview(undefined);
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
    <section className="surface cli-link-card">
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
      <div className="field">
        <label htmlFor="cli-user-code">{copy.code}</label>
        <input
          id="cli-user-code"
          value={code}
          minLength={8}
          maxLength={32}
          pattern="[A-Za-z0-9-]{8,32}"
          autoComplete="one-time-code"
          autoCapitalize="characters"
          spellCheck={false}
          aria-describedby="cli-user-code-help"
          onChange={(event) => {
            setCode(event.currentTarget.value);
            setPreview(undefined);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") void review();
          }}
        />
        <small id="cli-user-code-help">{copy.help}</small>
      </div>
      {!preview && (
        <button
          className="button button--primary"
          type="button"
          disabled={busy !== undefined}
          onClick={() => void review()}
        >
          {busy === "review" ? copy.reviewing : copy.review}
        </button>
      )}
      {preview && (
        <div className="cli-device-preview" role="group" aria-label={copy.device}>
          <dl>
            <div>
              <dt>{copy.device}</dt>
              <dd>{preview.label}</dd>
            </div>
            <div>
              <dt>{copy.fingerprint}</dt>
              <dd className="mono">{preview.public_key_fingerprint}</dd>
            </div>
          </dl>
          <p>{copy.warning}</p>
          <button
            className="button button--primary"
            type="button"
            disabled={busy !== undefined}
            onClick={() => void approve()}
          >
            {busy === "approve" ? copy.approving : copy.approve}
          </button>
        </div>
      )}
    </section>
  );
}
