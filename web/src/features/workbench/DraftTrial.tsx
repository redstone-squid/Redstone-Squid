import { useEffect, useRef, useState } from "react";
import { translate, type Locale, type StaticMessageKey } from "../../lib/i18n";
import { Button } from "../../ui/react/Button";
import { Field, SelectField } from "../../ui/react/Field";
import { FilePicker } from "../../ui/react/FilePicker";
import { Notice } from "../../ui/react/Notice";
import { Progress } from "../../ui/react/Progress";
import { layoutStyles } from "../../ui/variants";
import {
  createDraftFixture,
  draftFeedback,
  type DraftPhase,
  type DraftScenario,
  validateDraft,
  versionOptions,
  type DraftErrors,
  type DraftInput,
} from "./draft-fixture";

type Props = { locale: Locale; scenario?: DraftScenario };
export function DraftTrial({ locale, scenario }: Props) {
  const t = (key: StaticMessageKey): string => translate(locale, key);
  const [input, setInput] = useState<DraftInput>({
    title: "",
    category: "door",
    width: "",
    version: "",
  });
  const [search, setSearch] = useState("");
  const [errors, setErrors] = useState<DraftErrors>({});
  const [phase, setPhase] = useState<DraftPhase>("idle");
  const [progress, setProgress] = useState(0);
  const [retryFile, setRetryFile] = useState<File>();
  const [attachment, setAttachment] = useState("");
  const [api] = useState(() => createDraftFixture(scenario));
  const operation = useRef<AbortController | null>(null);
  useEffect(() => () => operation.current?.abort(), []);
  const busy = phase === "saving" || phase === "uploading";
  const feedback = draftFeedback(phase, errors);
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
      if (controller.signal.aborted || operation.current !== controller) return;
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
    setRetryFile(undefined);
    setProgress(0);
    setAttachment("");
    setPhase("uploading");
    try {
      const uploaded = await api.upload(file, controller.signal, (percent) => {
        if (!controller.signal.aborted && operation.current === controller) setProgress(percent);
      });
      if (controller.signal.aborted || operation.current !== controller) return;
      setAttachment(uploaded);
      setPhase("idle");
    } catch {
      if (!controller.signal.aborted) {
        setRetryFile(file);
        setPhase("idle");
      }
    }
  }
  return (
    <form
      className={layoutStyles({ pattern: "stack" })}
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
        <SelectField
          label={t("trial.category")}
          value={input.category}
          onChange={(event) => update({ category: event.target.value as DraftInput["category"] })}
        >
          <option value="door">{t("trial.door")}</option>
          <option value="utility">{t("trial.utility")}</option>
        </SelectField>
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
        <SelectField
          label={t("workbench.minecraft-version")}
          value={input.version}
          required
          error={errors.version ? t(`trial.${errors.version}`) : undefined}
          onChange={(event) => update({ version: event.target.value })}
        >
          <option value="">{t("trial.choose-version")}</option>
          {versions.map((version) => (
            <option key={version} value={version}>
              {version}
            </option>
          ))}
        </SelectField>
        <FilePicker label={t("trial.upload")} onFiles={([file]) => void upload(file)} />
      </fieldset>
      {phase === "uploading" && <Progress label={t("trial.uploading")} value={progress} />}
      {retryFile && (
        <>
          <Notice role="alert" tone="warning">
            {t("trial.uploadFailure")}
          </Notice>
          <div className={layoutStyles({ pattern: "actions" })}>
            <Button disabled={busy} onClick={() => void upload(retryFile)}>
              {t("trial.retryUpload")}
            </Button>
          </div>
        </>
      )}
      {attachment && <p>{translate(locale, "trial.attachment", { filename: attachment })}</p>}
      <div className={layoutStyles({ pattern: "actions" })}>
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
      <Notice role="status" tone={feedback.tone}>
        {t(feedback.message)}
      </Notice>
    </form>
  );
}
