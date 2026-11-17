import { useEffect, useState } from "react";
import type {
  ChoiceOptionResponse,
  DraftSchematicResponse,
  SchematicFieldSuggestion,
  SchematicSubmissionSuggestions,
} from "../generated/types.gen";
import type { SubmissionApi } from "../lib/submission-api";
import { translate, type Locale } from "../lib/i18n";

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
  const [error, setError] = useState<string>();
  const [refresh, setRefresh] = useState(0);
  const t = (key: Parameters<typeof translate>[1]) => translate(locale, key);

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
        setError(undefined);
        if (files.some((file) => file.state === "pending" || file.state === "uploading")) {
          timer = setTimeout(() => void load(), 1500);
        }
      } catch (error) {
        if (!cancelled) setError(error instanceof Error ? error.message : String(error));
      }
    };
    void load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [api, draftId, refresh]);

  const mutate = async (operation: () => Promise<unknown>) => {
    try {
      await onMutation(operation);
      setRefresh((value) => value + 1);
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error));
      setRefresh((value) => value + 1);
    }
  };

  return (
    <section className="surface submission-section" aria-labelledby="schematic-intake-title">
      <h2 id="schematic-intake-title">{t("submitFlow.schematicTitle")}</h2>
      <p>{t("submitFlow.schematicHelp")}</p>
      <label>
        {t("submitFlow.schematicUpload")}
        <input
          type="file"
          accept=".schem,.schematic,.litematic,.nbt,.mcstructure"
          disabled={disabled}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) {
              const uploadId = crypto.randomUUID();
              void mutate(() => api.uploadSchematic(draftId, file, uploadId));
            }
          }}
        />
      </label>
      {error && <p role="alert">{error}</p>}
      <button type="button" disabled={disabled} onClick={() => setRefresh((value) => value + 1)}>
        {t("submitFlow.schematicRefresh")}
      </button>
      <ul>
        {files.map((file) => (
          <li key={file.id}>
            {file.filename} — {file.state}
            {file.primary ? (
              ` (${t("submitFlow.schematicPrimary")})`
            ) : (
              <button
                type="button"
                disabled={disabled}
                onClick={() => void mutate(() => api.selectPrimarySchematic(draftId, file.id))}
              >
                {t("submitFlow.schematicPrimary")}
              </button>
            )}
            <button
              type="button"
              disabled={disabled}
              onClick={() => void mutate(() => api.discardSchematic(draftId, file.id))}
            >
              {t("submitFlow.schematicDiscard")}
            </button>
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
                if (value && typeof value === "object" && "name" in value)
                  return String(value.name);
                return (
                  choices[suggestion.field_id]?.find((choice) => choice.value === value)?.label ??
                  String(value ?? "")
                );
              })
              .join(", ")}
            <p>{suggestion.evidence}</p>
            <button type="button" disabled={disabled} onClick={() => onAccept(suggestion)}>
              {t("submitFlow.schematicAccept")}
            </button>
          </li>
        ))}
      </ul>
      {result?.detections?.length ? <p>{t("submitFlow.schematicUndetected")}</p> : null}
    </section>
  );
}
