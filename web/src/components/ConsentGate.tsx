import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { PrivacyNoticeDetail } from "../generated/types.gen";
import {
  asSubmissionError,
  createConsentApi,
  type ConsentApi,
  SubmissionApiError,
} from "../lib/submission-api";
import type { RuntimeConfig } from "../lib/config";
import { translate, type Locale } from "../lib/i18n";

type Options = {
  locale: Locale;
  config: RuntimeConfig;
  api?: ConsentApi;
};

type Props = Options & {
  /** Called after each successful grant; the parent owns the action to retry. */
  onAccepted: () => void | Promise<void>;
  onCancel?: () => void;
};

/**
 * Load the notice on mount and accept the version displayed to the reader.
 *
 * Parents mount the gate after a consent refusal, avoiding this request for other visitors.
 * A stale-version refusal reloads the notice so the reader can review the replacement.
 */
export function useConsentGate({ locale, config, api: suppliedApi }: Options) {
  const api = useMemo(
    () => suppliedApi ?? createConsentApi(locale, config),
    [config, locale, suppliedApi],
  );
  const [notice, setNotice] = useState<PrivacyNoticeDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const request = useRef(0);
  useEffect(() => {
    // A locale change starts a second fetch; only the newest one may set state.
    const token = ++request.current;
    void (async () => {
      try {
        const loaded = await api.notice();
        if (request.current === token) setNotice(loaded);
      } catch {
        if (request.current === token) setError(translate(locale, "consent.failed"));
      }
    })();
  }, [api, locale]);

  const accept = useCallback(async (): Promise<boolean> => {
    if (!notice) return false;
    setBusy(true);
    setError(null);
    try {
      // The version that was rendered, not the current one: the server refuses a mismatch, which
      // is what stops a stale cached notice recording consent to text nobody read.
      await api.grant(notice.version);
      return true;
    } catch (caught) {
      const failure = asSubmissionError(caught);
      const stale =
        failure instanceof SubmissionApiError && failure.problem?.code === "CONSENT_VERSION_STALE";
      setError(translate(locale, stale ? "consent.stale" : "consent.rejected"));
      if (stale) setNotice(await api.notice().catch(() => notice));
      return false;
    } finally {
      setBusy(false);
    }
  }, [api, locale, notice]);

  return { notice, busy, error, accept };
}

export default function ConsentGate({ onAccepted, onCancel, ...options }: Props) {
  const { locale } = options;
  const { notice, busy, error, accept } = useConsentGate(options);

  return (
    <section className="surface consent-gate" aria-live="polite">
      <h3>{translate(locale, "consent.heading")}</h3>
      {error && (
        <p className="submission-inline-error" role="alert">
          {error}
        </p>
      )}
      {notice ? (
        <>
          <p className="kicker">
            {translate(locale, "consent.version", { version: notice.version })}
          </p>
          <h4>{notice.title}</h4>
          {/* Render API text as paragraphs; React escapes it instead of interpreting HTML. */}
          {notice.body.split("\n\n").map((paragraph) => (
            <p key={paragraph}>{paragraph}</p>
          ))}
          <div className="consent-gate__actions">
            <button
              className="button button--primary"
              type="button"
              disabled={busy}
              onClick={() => {
                void (async () => {
                  if (await accept()) await onAccepted();
                })();
              }}
            >
              {busy ? translate(locale, "consent.accepting") : translate(locale, "consent.accept")}
            </button>
            {onCancel && (
              <button className="button" type="button" disabled={busy} onClick={onCancel}>
                {translate(locale, "consent.cancel")}
              </button>
            )}
          </div>
        </>
      ) : (
        !error && <p>{translate(locale, "consent.loading")}</p>
      )}
    </section>
  );
}
