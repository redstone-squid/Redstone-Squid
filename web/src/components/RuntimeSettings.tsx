import { useEffect, useMemo, useState, type SyntheticEvent } from "react";

import type { RuntimeSettingsDetail } from "../generated/types.gen";
import type { RuntimeConfig } from "../lib/config";
import { translate, type Locale, type StaticMessageKey } from "../lib/i18n";
import {
  asSubmissionError,
  createRuntimeSettingsApi,
  type RuntimeSettingsApi,
} from "../lib/submission-api";
import ConsentGate from "./ConsentGate";

type Props = { locale: Locale; config: RuntimeConfig; api?: RuntimeSettingsApi };
type Setting = Exclude<keyof RuntimeSettingsDetail, "revision">;

const labels: Record<Setting, StaticMessageKey> = {
  welcome_forward_chance: "settings.welcome_forward_chance",
  welcome_enabled: "settings.welcome_enabled",
  redstoner_enabled: "settings.redstoner_enabled",
  build_vote_pass_threshold: "settings.build_vote_pass_threshold",
  build_vote_fail_threshold: "settings.build_vote_fail_threshold",
  delete_vote_pass_threshold: "settings.delete_vote_pass_threshold",
  delete_vote_fail_threshold: "settings.delete_vote_fail_threshold",
  notification_retention_days: "settings.notification_retention_days",
  redstoner_starboard_author_id: "settings.redstoner_starboard_author_id",
  redstoner_starboard_channel_id: "settings.redstoner_starboard_channel_id",
  redstoner_role_id: "settings.redstoner_role_id",
  redstoner_corner_channel_id: "settings.redstoner_corner_channel_id",
  redstoner_announcement_channel_id: "settings.redstoner_announcement_channel_id",
  welcome_channel_id: "settings.welcome_channel_id",
  welcome_relay_channel_id: "settings.welcome_relay_channel_id",
  version_tracker_channel_id: "settings.version_tracker_channel_id",
  build_log_channel_ids: "settings.build_log_channel_ids",
};

function display(value: RuntimeSettingsDetail[Setting]): string {
  return Array.isArray(value) ? value.join(", ") : String(value);
}

export default function RuntimeSettings({ locale, config, api: suppliedApi }: Props) {
  const api = useMemo(
    () => suppliedApi ?? createRuntimeSettingsApi(locale, config),
    [suppliedApi, locale, config],
  );
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
            ? translate(locale, "settings.global-administrator-permission-is-required")
            : failure.message,
        );
      });
    return () => {
      active = false;
    };
  }, [api, locale]);

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
      const updated = await api.update({ [selected]: parsed }, settings.revision);
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
    <section
      className="surface runtime-settings"
      aria-label={translate(locale, "settings.global-settings")}
    >
      {error && <p role="alert">{error}</p>}
      {auth && (
        <a href={api.signInUrl(window.location.href)}>{translate(locale, "settings.sign-in")}</a>
      )}
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
      {!settings && !error && <p role="status">{translate(locale, "settings.loading-settings")}</p>}
      {settings && (
        <>
          <p>
            {translate(
              locale,
              "settings.changes-apply-to-subsequent-operations-existing-votes-keep-their-thresholds",
            )}
          </p>
          <form onSubmit={(event) => void save(event)}>
            <label htmlFor="runtime-setting">{translate(locale, "settings.setting")}</label>
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
                  {translate(locale, labels[name])}
                </option>
              ))}
            </select>
            <label htmlFor="runtime-value">{translate(locale, "settings.value")}</label>
            {typeof settings[selected] === "boolean" ? (
              <select
                id="runtime-value"
                value={value}
                disabled={busy}
                onChange={(event) => setValue(event.target.value)}
              >
                <option value="true">{translate(locale, "settings.enabled")}</option>
                <option value="false">{translate(locale, "settings.disabled")}</option>
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
              {busy
                ? translate(locale, "settings.saving")
                : translate(locale, "settings.save-setting")}
            </button>
          </form>
          {saved && <p role="status">{translate(locale, "settings.setting-saved")}</p>}
          <dl>
            {(Object.keys(labels) as Setting[]).map((name) => (
              <div key={name}>
                <dt>{translate(locale, labels[name])}</dt>
                <dd>{display(settings[name])}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
    </section>
  );
}
