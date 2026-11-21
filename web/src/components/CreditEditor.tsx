import { useState } from "react";
import { Field, FieldGroup, SelectField } from "../ui/react/Field";
import { Button } from "../ui/react/Button";
import { type Locale, translate } from "../lib/i18n";
import type { CreditAnswer } from "../lib/submission-form";

const kinds = [
  ["name", "Name"],
  ["discord", "Discord"],
  ["minecraft_java", "Minecraft Java"],
  ["minecraft_bedrock", "Minecraft Bedrock"],
];
const roles = ["creator", "designer", "layout", "contributor"] as const;

export function CreditEditor({
  locale,
  value,
  disabled,
  onCommit,
}: {
  locale: Locale;
  value: CreditAnswer[];
  disabled: boolean;
  onCommit: (credits: CreditAnswer[]) => void;
}) {
  const [credits, setCredits] = useState(value);
  const update = (index: number, patch: Partial<CreditAnswer>) => {
    const next = credits.map((credit, position) =>
      position === index
        ? { ...credit, ...patch, attribution: "explicit", source_message_id: null }
        : credit,
    );
    setCredits(next);
  };
  const save = () => {
    if (credits.every((credit) => credit.name.trim())) onCommit(credits);
  };
  return (
    <div
      className="sq-stack"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) save();
      }}
    >
      {credits.map((credit, index) => (
        <FieldGroup
          key={index}
          disabled={disabled}
          label={translate(locale, "credit.row", { number: index + 1 })}
        >
          <Field
            label={translate(locale, "credit.name")}
            value={credit.name}
            required
            maxLength={255}
            onChange={(event) => update(index, { name: event.target.value })}
          />
          <SelectField
            label={translate(locale, "credit.identity")}
            value={credit.kind}
            onChange={(event) =>
              update(index, { kind: event.target.value, username: null, subject: null })
            }
          >
            {kinds.map(([kind, name]) => (
              <option key={kind} value={kind}>
                {kind === "name" ? translate(locale, "credit.name") : name}
              </option>
            ))}
          </SelectField>
          {credit.kind !== "name" && (
            <>
              <Field
                label={translate(locale, "credit.username")}
                detail={translate(locale, "submitFlow.optional")}
                value={credit.username ?? ""}
                onChange={(event) =>
                  update(index, { username: event.target.value || null, subject: null })
                }
              />
              <Field
                label={
                  credit.kind === "minecraft_java"
                    ? "UUID"
                    : credit.kind === "discord"
                      ? translate(locale, "credit.discord-id")
                      : "XUID"
                }
                detail={translate(locale, "submitFlow.optional")}
                value={credit.subject ?? ""}
                onChange={(event) => update(index, { subject: event.target.value || null })}
              />
            </>
          )}
          <SelectField
            label={translate(locale, "credit.contribution")}
            value={credit.role}
            onChange={(event) => update(index, { role: event.target.value })}
          >
            {roles.map((role) => (
              <option key={role} value={role}>
                {translate(locale, `credit.${role}`)}
              </option>
            ))}
          </SelectField>
          <Button
            type="button"
            onClick={() => {
              const next = credits.filter((_, position) => position !== index);
              setCredits(next);
              onCommit(next);
            }}
          >
            {translate(locale, "credit.remove")}
          </Button>
        </FieldGroup>
      ))}
      <Button
        type="button"
        disabled={disabled}
        onClick={() =>
          setCredits([
            ...credits,
            { name: "", kind: "name", role: "creator", attribution: "explicit" },
          ])
        }
      >
        {translate(locale, "credit.add")}
      </Button>
    </div>
  );
}
