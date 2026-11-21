import { useEffect, useState } from "react";
import type {
  ChoiceOptionResponse,
  DraftSchematicResponse,
  SchematicFieldSuggestion,
  SchematicSubmissionSuggestions,
} from "../generated/types.gen";
import { Button } from "../ui/react/Button";
import { FilePicker } from "../ui/react/FilePicker";
import { Notice } from "../ui/react/Notice";
import type { SubmissionApi } from "../lib/submission-api";
import { translate, type StaticMessageKey, type Locale } from "../lib/i18n";

type Props = {
  api: SubmissionApi;
  draftId: string;
  locale: Locale;
  disabled: boolean;
  labels: Record<string, string>;
  choices: Record<string, ChoiceOptionResponse[]>;
  onMutation: (operation: () => Promise<unknown>) => Promise<void>;
  onAccept: (suggestion: SchematicFieldSuggestion) => void;
};

export default function SchematicSubmissionSection({
  api,
  draftId,
  locale,
  disabled,
  labels,
  choices,
  onMutation,
  onAccept,
}: Props) {
  const [files, setFiles] = useState<DraftSchematicResponse[]>([]);
  const [result, setResult] = useState<SchematicSubmissionSuggestions>();
  const [loadError, setLoadError] = useState<string>();
  const [actionError, setActionError] = useState<string>();
  const error = actionError ?? loadError;
  const [refresh, setRefresh] = useState(0);
  const t = (key: StaticMessageKey) => translate(locale, key);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const files = (await api.listSchematics(draftId)).filter((file) => !file.discarded);
        const primary =
          files.find((file) => file.primary) ?? (files.length === 1 ? files[0] : undefined);
        const result = primary ? await api.schematicSuggestions(draftId, primary.id) : undefined;
        if (cancelled) return;
        setFiles(files);
        setResult(result);
        setLoadError(undefined);
        if (files.some((file) => file.state === "pending" || file.state === "uploading")) {
          timer = setTimeout(() => void load(), 1500);
        }
      } catch (error) {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : String(error));
      }
    };
    void load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [api, draftId, refresh]);

  const mutate = async (operation: () => Promise<unknown>) => {
    setActionError(undefined);
    try {
      await onMutation(operation);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setRefresh((value) => value + 1);
    }
  };

  return (
    <section className="surface submission-section" aria-labelledby="schematic-intake-title">
      <h2 id="schematic-intake-title">{t("submitFlow.schematicTitle")}</h2>
      <p>{t("submitFlow.schematicHelp")}</p>
      <FilePicker
        label={t("submitFlow.schematicUpload")}
        accept=".schem,.schematic,.litematic,.nbt,.mcstructure"
        disabled={disabled}
        onFiles={([file]) => {
          if (!file) return;
          const uploadId = crypto.randomUUID();
          void mutate(() => api.uploadSchematic(draftId, file, uploadId));
        }}
      />
      {error && (
        <Notice tone="danger" role="alert">
          {error}
        </Notice>
      )}
      <div>
        <Button
          disabled={disabled}
          onClick={() => {
            setActionError(undefined);
            setRefresh((value) => value + 1);
          }}
        >
          {t("submitFlow.schematicRefresh")}
        </Button>
      </div>
      <ul>
        {files.map((file) => (
          <li key={file.id}>
            {file.filename} — {t(`uploadState.${file.state}`)}
            {file.primary ? (
              ` (${t("submitFlow.isPrimary")})`
            ) : (
              <Button
                size="small"
                disabled={disabled}
                onClick={() => void mutate(() => api.selectPrimarySchematic(draftId, file.id))}
              >
                {t("submitFlow.schematicPrimary")}
              </Button>
            )}
            <Button
              size="small"
              disabled={disabled}
              onClick={() => void mutate(() => api.discardSchematic(draftId, file.id))}
            >
              {t("submitFlow.schematicDiscard")}
            </Button>
          </li>
        ))}
      </ul>
      {result?.measurements && (
        <p>
          {result.measurements.dimensions.width} × {result.measurements.dimensions.height} ×{" "}
          {result.measurements.dimensions.length} ({t("submitFlow.schematicBounds")})
        </p>
      )}
      <ul>
        {result?.suggestions?.map((suggestion) => (
          <li key={suggestion.field_id}>
            <strong>{labels[suggestion.field_id] ?? suggestion.field_id}</strong>:{" "}
            {(Array.isArray(suggestion.value) ? suggestion.value : [suggestion.value])
              .map((value) => {
                if (value && typeof value === "object")
                  return "name" in value && typeof value.name === "string" ? value.name : "";
                return (
                  choices[suggestion.field_id]?.find((choice) => choice.value === value)?.label ??
                  String(value ?? "")
                );
              })
              .join(", ")}
            <p>{suggestion.evidence}</p>
            <Button size="small" disabled={disabled} onClick={() => onAccept(suggestion)}>
              {t("submitFlow.schematicAccept")}
            </Button>
          </li>
        ))}
      </ul>
      {result?.detections?.length ? <p>{t("submitFlow.schematicUndetected")}</p> : null}
    </section>
  );
}
