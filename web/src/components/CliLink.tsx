import { useEffect, useMemo, useState } from "react";

import type { CliEnrollmentApprovalResponse } from "../generated/types.gen";
import {
  asSubmissionError,
  createCliLinkApi,
  type CliLinkApi,
  type SubmissionApiError,
} from "../lib/submission-api";
import type { RuntimeConfig } from "../lib/config";
import type { Locale } from "../lib/i18n";

type Props = {
  locale: Locale;
  config: RuntimeConfig;
  initialCode?: string;
  api?: CliLinkApi;
};

const USER_CODE = /^[A-Za-z0-9-]{8,32}$/;
const SESSION_CODE_KEY = "squid.cli.enrollment_code";

const COPY = {
  en: {
    code: "User code",
    help: "Enter only the short code shown by the Squid CLI. Never paste a device code, private key, or session token here.",
    review: "Review this device",
    reviewing: "Loading device…",
    invalid: "Enter the 8–32 character user code shown by the CLI.",
    auth: "Sign in with Discord before reviewing this CLI device.",
    consent: "Accept the current privacy notice before approving this CLI device.",
    unavailable: "CLI linking is temporarily unavailable. Try again shortly.",
    signIn: "Sign in with Discord",
    device: "Device",
    fingerprint: "Public-key fingerprint",
    warning:
      "Approve only if this label and fingerprint match the CLI you started. Approval lets that device manage your private Squid drafts.",
    approve: "Approve this CLI device",
    approving: "Approving…",
    success: "CLI device approved",
    successBody: "The CLI may now finish signing in. You can close this page.",
    another: "Review another code",
  },
  "zh-CN": {
    code: "用户代码",
    help: "只输入 Squid CLI 显示的短代码。请勿在此粘贴设备代码、私钥或会话令牌。",
    review: "检查此设备",
    reviewing: "正在加载设备…",
    invalid: "请输入 CLI 显示的 8–32 位用户代码。",
    auth: "请先使用 Discord 登录，再检查此 CLI 设备。",
    consent: "批准此 CLI 设备前，请接受当前隐私声明。",
    unavailable: "CLI 关联暂时不可用，请稍后重试。",
    signIn: "使用 Discord 登录",
    device: "设备",
    fingerprint: "公钥指纹",
    warning:
      "仅当标签和指纹与你启动的 CLI 完全一致时才批准。批准后，该设备可管理你的私密 Squid 草稿。",
    approve: "批准此 CLI 设备",
    approving: "正在批准…",
    success: "CLI 设备已获批准",
    successBody: "CLI 现在可以完成登录。你可以关闭此页面。",
    another: "检查另一个代码",
  },
} as const;

function errorMessage(error: SubmissionApiError, locale: Locale): string {
  const copy = COPY[locale];
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

export default function CliLink({ locale, config, initialCode = "", api: suppliedApi }: Props) {
  const copy = COPY[locale];
  const api = useMemo(
    () => suppliedApi ?? createCliLinkApi(locale, config),
    [config, locale, suppliedApi],
  );
  const [code, setCode] = useState(normalizedCode(initialCode));
  const [busy, setBusy] = useState<"review" | "approve">();
  const [error, setError] = useState<SubmissionApiError | string>();
  const [preview, setPreview] = useState<CliEnrollmentApprovalResponse>();
  const [approved, setApproved] = useState(false);

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
    } catch (caught) {
      setError(asSubmissionError(caught));
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
