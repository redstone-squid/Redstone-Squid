import { useMemo, useState } from "react";

import type { ChallengeApprovalResponse } from "../generated/types.gen";
import {
  asSubmissionError,
  createMinecraftLinkApi,
  type MinecraftLinkApi,
  type SubmissionApiError,
} from "../lib/submission-api";
import type { RuntimeConfig } from "../lib/config";
import type { Locale } from "../lib/i18n";

type Props = {
  locale: Locale;
  config: RuntimeConfig;
  initialCode?: string;
  api?: MinecraftLinkApi;
};

const USER_CODE = /^[A-Za-z2-7-]{8,32}$/;

const COPY = {
  en: {
    code: "User code",
    help: "Enter only the short user code shown by the Minecraft client. Never paste a device code or access token here.",
    approve: "Approve this player",
    approving: "Approving…",
    invalid: "Enter the 8–32 character user code shown in game.",
    auth: "Sign in with Discord before approving this player.",
    consent: "Accept the current privacy notice before approving this player.",
    unavailable: "Minecraft linking is temporarily unavailable. Try again shortly.",
    signIn: "Sign in with Discord",
    success: "Player approved",
    successBody: "The {origin} may now finish linking Java account {uuid}.",
    paper: "Paper server",
    fabric: "Fabric client",
    another: "Approve another code",
  },
  "zh-CN": {
    code: "用户代码",
    help: "只输入 Minecraft 客户端显示的短用户代码。请勿在此粘贴设备代码或访问令牌。",
    approve: "批准此玩家",
    approving: "正在批准…",
    invalid: "请输入游戏内显示的 8–32 位用户代码。",
    auth: "请先使用 Discord 登录，再批准此玩家。",
    consent: "批准此玩家前，请接受当前隐私声明。",
    unavailable: "Minecraft 关联暂时不可用，请稍后重试。",
    signIn: "使用 Discord 登录",
    success: "玩家已获批准",
    successBody: "{origin}现在可以完成 Java 账号 {uuid} 的关联。",
    paper: "Paper 服务端",
    fabric: "Fabric 客户端",
    another: "批准另一个代码",
  },
} as const;

function errorMessage(error: SubmissionApiError, locale: Locale): string {
  const copy = COPY[locale];
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
}: Props) {
  const copy = COPY[locale];
  const api = useMemo(
    () => suppliedApi ?? createMinecraftLinkApi(locale, config),
    [config, locale, suppliedApi],
  );
  const [code, setCode] = useState(USER_CODE.test(initialCode) ? initialCode.toUpperCase() : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<SubmissionApiError | string>();
  const [approval, setApproval] = useState<ChallengeApprovalResponse>();
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
    } catch (caught) {
      setError(asSubmissionError(caught));
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
          {copy.successBody
            .replace("{origin}", approval.origin === "paper" ? copy.paper : copy.fabric)
            .replace("{uuid}", approval.java_uuid)}
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
