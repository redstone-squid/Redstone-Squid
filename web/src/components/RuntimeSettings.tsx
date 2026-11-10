import { useEffect, useMemo, useState, type SyntheticEvent } from "react";

import type { RuntimeSettingsDetail } from "../generated/types.gen";
import type { RuntimeConfig } from "../lib/config";
import type { Locale } from "../lib/i18n";
import {
  asSubmissionError,
  createRuntimeSettingsApi,
  type RuntimeSettingsApi,
} from "../lib/submission-api";
import ConsentGate from "./ConsentGate";

type Props = { locale: Locale; config: RuntimeConfig; api?: RuntimeSettingsApi };
type Setting = keyof RuntimeSettingsDetail;

const labels: Record<Setting, [string, string]> = {
  welcome_forward_chance: ["Welcome forwarding probability (0–1)", "欢迎消息转发概率（0–1）"],
  welcome_enabled: ["Welcome relay enabled", "启用欢迎消息转发"],
  redstoner_enabled: ["Automatic Redstoner role grants enabled", "启用自动授予 Redstoner 身份组"],
  build_vote_pass_threshold: ["Build vote approval threshold", "作品投票通过阈值"],
  build_vote_fail_threshold: ["Build vote rejection threshold", "作品投票否决阈值"],
  delete_vote_pass_threshold: ["Log deletion approval threshold", "日志删除通过阈值"],
  delete_vote_fail_threshold: ["Log deletion rejection threshold", "日志删除否决阈值"],
  notification_retention_days: ["Notification retention (days)", "通知保留天数"],
  redstoner_starboard_author_id: ["Starboard author ID", "星标消息作者 ID"],
  redstoner_starboard_channel_id: ["Starboard channel ID", "星标频道 ID"],
  redstoner_role_id: ["Redstoner role ID", "Redstoner 身份组 ID"],
  redstoner_corner_channel_id: ["Redstoner corner channel ID", "Redstoner 角落频道 ID"],
  redstoner_announcement_channel_id: ["Role announcement channel ID", "身份组公告频道 ID"],
  welcome_channel_id: ["Welcome source channel ID", "欢迎消息来源频道 ID"],
  welcome_relay_channel_id: ["Welcome destination channel ID", "欢迎消息目标频道 ID"],
  version_tracker_channel_id: ["Version announcement channel ID", "版本公告频道 ID"],
  build_log_channel_ids: ["Build log channel IDs (comma separated)", "作品日志频道 ID（逗号分隔）"],
};

function display(value: RuntimeSettingsDetail[Setting]): string {
  return Array.isArray(value) ? value.join(", ") : String(value);
}

export default function RuntimeSettings({ locale, config, api: suppliedApi }: Props) {
  const api = useMemo(
    () => suppliedApi ?? createRuntimeSettingsApi(locale, config),
    [suppliedApi, locale, config],
  );
  const zh = locale === "zh-CN";
  const [settings, setSettings] = useState<RuntimeSettingsDetail | null>(null);
  const [selected, setSelected] = useState<Setting>("welcome_forward_chance");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [auth, setAuth] = useState(false);
  const [consent, setConsent] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let active = true;
    void api
      .get()
      .then((current) => {
        if (!active) return;
        setSettings(current);
        setSelected("welcome_forward_chance");
        setValue(display(current.welcome_forward_chance));
        setError(null);
        setAuth(false);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        const failure = asSubmissionError(caught);
        setAuth(failure.kind === "authentication");
        setError(
          failure.status === 403
            ? zh
              ? "需要全局管理员权限。"
              : "Global administrator permission is required."
            : failure.message,
        );
      });
    return () => {
      active = false;
    };
  }, [api, zh]);

  async function save(event: SyntheticEvent) {
    event.preventDefault();
    if (!settings || busy) return;
    setBusy(true);
    setSaved(false);
    setError(null);
    const previous = settings[selected];
    const parsed = Array.isArray(previous)
      ? value
          .split(",")
          .map((id) => id.trim())
          .filter(Boolean)
      : typeof previous === "boolean"
        ? value === "true"
        : typeof previous === "number"
          ? Number(value)
          : value.trim();
    try {
      const updated = await api.update({ [selected]: parsed });
      setSettings(updated);
      setValue(display(updated[selected]));
      setSaved(true);
    } catch (caught) {
      const failure = asSubmissionError(caught);
      setAuth(failure.kind === "authentication");
      setConsent(failure.kind === "consent");
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="surface runtime-settings" aria-label={zh ? "全局设置" : "Global settings"}>
      {error && <p role="alert">{error}</p>}
      {auth && <a href={api.signInUrl(window.location.href)}>{zh ? "登录" : "Sign in"}</a>}
      {consent && (
        <ConsentGate
          locale={locale}
          config={config}
          onAccepted={() => {
            setConsent(false);
            setError(null);
          }}
        />
      )}
      {!settings && !error && <p role="status">{zh ? "正在加载…" : "Loading settings…"}</p>}
      {settings && (
        <>
          <p>
            {zh
              ? "更改影响后续操作；现有投票保留原阈值。"
              : "Changes apply to subsequent operations. Existing votes keep their thresholds."}
          </p>
          <form onSubmit={(event) => void save(event)}>
            <label htmlFor="runtime-setting">{zh ? "设置" : "Setting"}</label>
            <select
              id="runtime-setting"
              value={selected}
              disabled={busy}
              onChange={(event) => {
                const name = event.target.value as Setting;
                setSelected(name);
                setValue(display(settings[name]));
                setSaved(false);
              }}
            >
              {(Object.keys(labels) as Setting[]).map((name) => (
                <option key={name} value={name}>
                  {labels[name][zh ? 1 : 0]}
                </option>
              ))}
            </select>
            <label htmlFor="runtime-value">{zh ? "值" : "Value"}</label>
            {typeof settings[selected] === "boolean" ? (
              <select
                id="runtime-value"
                value={value}
                disabled={busy}
                onChange={(event) => setValue(event.target.value)}
              >
                <option value="true">{zh ? "启用" : "Enabled"}</option>
                <option value="false">{zh ? "禁用" : "Disabled"}</option>
              </select>
            ) : (
              <input
                id="runtime-value"
                value={value}
                disabled={busy}
                type={typeof settings[selected] === "number" ? "number" : "text"}
                step={selected === "welcome_forward_chance" ? "any" : "1"}
                required={!Array.isArray(settings[selected])}
                onChange={(event) => setValue(event.target.value)}
              />
            )}
            <button className="button button--primary" type="submit" disabled={busy}>
              {busy ? (zh ? "正在保存…" : "Saving…") : zh ? "保存" : "Save setting"}
            </button>
          </form>
          {saved && <p role="status">{zh ? "设置已保存。" : "Setting saved."}</p>}
          <dl>
            {(Object.keys(labels) as Setting[]).map((name) => (
              <div key={name}>
                <dt>{labels[name][zh ? 1 : 0]}</dt>
                <dd>{display(settings[name])}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
    </section>
  );
}
