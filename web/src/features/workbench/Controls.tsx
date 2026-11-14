import { translate, type Locale, type StaticMessageKey } from "../../lib/i18n";
import { useState } from "react";
import { ArrowRight, Check, Plus } from "lucide-react";
import { Button } from "../../ui/react/Button";
import { Field } from "../../ui/react/Field";
import { Dialog } from "../../ui/react/Dialog";

export function Controls({ locale }: { locale: Locale }) {
  const t = (key: StaticMessageKey): string => translate(locale, key);
  const [title, setTitle] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [saved, setSaved] = useState(false);
  const invalid = submitted && !title.trim();
  return (
    <div className="sq-stack">
      <div className="sq-row">
        <Dialog
          title={t("workbench.create-a-draft")}
          description={t("workbench.draft-description")}
          closeLabel={t("workbench.close-dialog")}
          trigger={
            <Button variant="primary">
              <Plus size={16} aria-hidden="true" />
              {t("workbench.new-draft")}
            </Button>
          }
        >
          <form
            className="sq-stack"
            noValidate
            onSubmit={(event) => {
              event.preventDefault();
              setSubmitted(true);
              setSaved(Boolean(title.trim()));
            }}
          >
            <Field
              label={t("workbench.build-title")}
              value={title}
              required
              onChange={(event) => {
                setTitle(event.target.value);
                setSaved(false);
              }}
              error={invalid ? t("workbench.enter-a-title-for-your-build") : undefined}
            />
            <Button type="submit" variant="primary">
              {t("workbench.save-draft")}
              <ArrowRight size={16} aria-hidden="true" />
            </Button>
            <p role="status">
              {saved
                ? t("workbench.example-draft-saved")
                : invalid
                  ? t("workbench.check-the-highlighted-field")
                  : ""}
            </p>
          </form>
        </Dialog>
        <Button
          onClick={() => {
            document.getElementById("patterns")?.scrollIntoView({ behavior: "smooth" });
          }}
        >
          {t("workbench.browse-examples")}
        </Button>
        <Button disabled>{t("workbench.unavailable")}</Button>
      </div>
      <div className="sq-field-grid">
        <Field
          label={t("workbench.creator")}
          hint={t("workbench.public-name-shown-on-the-build")}
          placeholder="CircuitSage"
        />
        <Field
          label={t("workbench.minecraft-version")}
          defaultValue="1.21"
          error={t("workbench.choose-java-or-bedrock-as-well")}
        />
      </div>
      <p className="sq-inline-note">
        <Check size={16} aria-hidden="true" />
        {t("workbench.controls-description")}
      </p>
    </div>
  );
}
