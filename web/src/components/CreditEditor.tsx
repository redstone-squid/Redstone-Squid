import { useState } from "react";
import type { CreditAnswer } from "../lib/submission-form";

const kinds = [
  ["name", "Name"],
  ["discord", "Discord"],
  ["minecraft_java", "Minecraft Java"],
  ["minecraft_bedrock", "Minecraft Bedrock"],
];
const roles = ["creator", "designer", "layout", "contributor"];

export function CreditEditor({
  value,
  disabled,
  onCommit,
}: {
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
      className="submission-field--wide"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) save();
      }}
    >
      {credits.map((credit, index) => (
        <fieldset key={index} disabled={disabled}>
          <legend>Credit {index + 1}</legend>
          <label>
            Name{" "}
            <input
              value={credit.name}
              required
              maxLength={255}
              onChange={(event) => update(index, { name: event.target.value })}
            />
          </label>
          <label>
            Identity{" "}
            <select
              value={credit.kind}
              onChange={(event) =>
                update(index, {
                  kind: event.target.value,
                  username: null,
                  subject: null,
                })
              }
            >
              {kinds.map(([kind, label]) => (
                <option key={kind} value={kind}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {credit.kind !== "name" && (
            <>
              <label>
                Username (optional){" "}
                <input
                  value={credit.username ?? ""}
                  onChange={(event) =>
                    update(index, { username: event.target.value || null, subject: null })
                  }
                />
              </label>
              <label>
                {credit.kind === "minecraft_java"
                  ? "UUID"
                  : credit.kind === "discord"
                    ? "Discord ID"
                    : "XUID"}{" "}
                (optional)
                <input
                  value={credit.subject ?? ""}
                  onChange={(event) => update(index, { subject: event.target.value || null })}
                />
              </label>
            </>
          )}
          <label>
            Contribution{" "}
            <select
              value={credit.role}
              onChange={(event) => update(index, { role: event.target.value })}
            >
              {roles.map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() => {
              const next = credits.filter((_, position) => position !== index);
              setCredits(next);
              onCommit(next);
            }}
          >
            Remove credit
          </button>
        </fieldset>
      ))}
      <button
        type="button"
        disabled={disabled}
        onClick={() =>
          setCredits([
            ...credits,
            { name: "", kind: "name", role: "creator", attribution: "explicit" },
          ])
        }
      >
        Add credit
      </button>
    </div>
  );
}
