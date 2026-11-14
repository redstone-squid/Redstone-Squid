import { useEffect, useRef, useState } from "react";
import { translate, type Locale, type StaticMessageKey } from "../../lib/i18n";
import { Button } from "../../ui/react/Button";
import { Field } from "../../ui/react/Field";
import {
  createDraftFixture,
  validateDraft,
  versionOptions,
  type DraftErrors,
  type DraftInput,
} from "./draft-fixture";

type Props = { locale: Locale; retryFirstSave: boolean };
export function DraftTrial({ locale, retryFirstSave }: Props) {
  const t = (key: StaticMessageKey): string => translate(locale, key);
  const [input, setInput] = useState<DraftInput>({
    title: "",
    category: "door",
    width: "",
    version: "",
  });
  const [search, setSearch] = useState("");
  const [errors, setErrors] = useState<DraftErrors>({});
  const [phase, setPhase] = useState<"idle" | "saving" | "saved" | "uploading" | "error">("idle");
  const [progress, setProgress] = useState(0);
  const [attachment, setAttachment] = useState("");
  const [api] = useState(() => createDraftFixture(retryFirstSave));
  const operation = useRef<AbortController | null>(null);
  useEffect(() => () => operation.current?.abort(), []);
  const busy = phase === "saving" || phase === "uploading";
  const versions = versionOptions.filter((version) =>
    version.toLowerCase().includes(search.toLowerCase()),
  );
  function update(patch: Partial<DraftInput>): void {
    setInput((current) => ({ ...current, ...patch }));
    setErrors({});
    setPhase("idle");
  }
  async function save(): Promise<void> {
    const invalid = validateDraft(input);
    setErrors(invalid);
    if (Object.keys(invalid).length) return;
    operation.current?.abort();
    const controller = new AbortController();
    operation.current = controller;
    setPhase("saving");
    try {
      const result = await api.save(input, controller.signal);
      if (result.kind === "invalid") {
        setErrors(result.errors);
        setPhase("idle");
      } else setPhase("saved");
    } catch {
      if (!controller.signal.aborted) setPhase("error");
    }
  }
  async function upload(file: File | undefined): Promise<void> {
    if (!file) return;
    operation.current?.abort();
    const controller = new AbortController();
    operation.current = controller;
    setProgress(0);
    setAttachment("");
    setPhase("uploading");
    try {
      setAttachment(await api.upload(file, controller.signal, setProgress));
      setPhase("idle");
    } catch {
      if (!controller.signal.aborted) setPhase("error");
    }
  }
  return (
    <form
      className="sq-stack"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <p className="sq-muted">{t("trial.disclaimer")}</p>
      <fieldset className="sq-stack sq-trial-fields" disabled={busy}>
        <legend className="sq-kicker">{t("trial.details")}</legend>
        <Field
          label={t("workbench.build-title")}
          value={input.title}
          required
          onChange={(event) => update({ title: event.target.value })}
          error={errors.title ? t(`trial.${errors.title}`) : undefined}
        />
        <div className="sq-field">
          <label htmlFor="trial-category">{t("trial.category")}</label>
          <select
            className="sq-input"
            id="trial-category"
            value={input.category}
            onChange={(event) => update({ category: event.target.value as DraftInput["category"] })}
          >
            <option value="door">{t("trial.door")}</option>
            <option value="utility">{t("trial.utility")}</option>
          </select>
        </div>
        {input.category === "door" && (
          <Field
            label={t("trial.width")}
            inputMode="numeric"
            value={input.width}
            required
            onChange={(event) => update({ width: event.target.value })}
            error={errors.width ? t(`trial.${errors.width}`) : undefined}
          />
        )}
        <Field
          label={t("trial.find-version")}
          type="search"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            update({ version: "" });
          }}
        />
        <div className="sq-field">
          <label htmlFor="trial-version">{t("workbench.minecraft-version")}</label>
          <select
            className="sq-input"
            id="trial-version"
            value={input.version}
            aria-invalid={Boolean(errors.version)}
            aria-describedby={errors.version ? "trial-version-error" : undefined}
            required
            onChange={(event) => update({ version: event.target.value })}
          >
            <option value="">{t("trial.choose-version")}</option>
            {versions.map((version) => (
              <option key={version} value={version}>
                {version}
              </option>
            ))}
          </select>
          {errors.version && (
            <p id="trial-version-error" className="sq-error">
              {t(`trial.${errors.version}`)}
            </p>
          )}
        </div>
        <div className="sq-field">
          <label htmlFor="trial-upload">{t("trial.upload")}</label>
          <input
            id="trial-upload"
            type="file"
            onChange={(event) => {
              void upload(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
        </div>
      </fieldset>
      {phase === "uploading" && (
        <div className="sq-field">
          <label htmlFor="trial-progress">{t("trial.uploading")}</label>
          <progress id="trial-progress" max={100} value={progress}>
            {progress}%
          </progress>
        </div>
      )}
      {attachment && <p>{translate(locale, "trial.attachment", { filename: attachment })}</p>}
      <div className="sq-row">
        <Button type="submit" variant="primary" disabled={busy}>
          {t(
            phase === "saving"
              ? "trial.saving"
              : phase === "error"
                ? "trial.retry"
                : "workbench.save-draft",
          )}
        </Button>
        {busy && (
          <Button
            onClick={() => {
              operation.current?.abort();
              setPhase("idle");
            }}
          >
            {t("trial.cancel")}
          </Button>
        )}
      </div>
      <p role="status">
        {t(
          phase === "saved"
            ? "trial.saved"
            : phase === "error"
              ? "trial.failed"
              : Object.keys(errors).length
                ? "trial.fix-errors"
                : "trial.ready",
        )}
      </p>
    </form>
  );
}
