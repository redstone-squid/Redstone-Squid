import { Field, SelectField, CheckField } from "../ui/react/Field";
import { useState } from "react";
import type { ChoiceOptionResponse } from "../generated/types.gen";
import { type Locale, translate } from "../lib/i18n";
import type { ShowcaseTagAnswer } from "../lib/submission-form";

/**
 * Pick showcase tags and enter a value for each chosen tag that takes one.
 *
 * Checking or unchecking a tag and choosing a yes/no value commit at once; typed values commit
 * on blur. Removing a tag forgets its value. Values are sent as typed and the server checks them.
 */
export function ShowcaseTagEditor({
  value,
  options,
  maxItems,
  disabled,
  locale,
  onCommit,
}: {
  value: ShowcaseTagAnswer[];
  options: ChoiceOptionResponse[];
  maxItems: number | null;
  disabled: boolean;
  locale: Locale;
  onCommit: (tags: ShowcaseTagAnswer[] | undefined) => void;
}) {
  const [tags, setTags] = useState(value);
  const optionFor = (key: string) => options.find((option) => option.value === key);
  // A selected tag can drop out of the approved list; keep it visible so it can be removed.
  const choices = [
    ...options,
    ...tags
      .filter((tag) => !optionFor(tag.key))
      .map((tag) => ({ value: tag.key, label: tag.key, value_type: null })),
  ];
  const full = maxItems !== null && tags.length >= maxItems;

  const commit = (next: ShowcaseTagAnswer[]) => {
    setTags(next);
    const answers = next.map(({ key, value }) => {
      const option = optionFor(key);
      const takesValue = option ? option.value_type !== null : value !== undefined;
      return takesValue && value ? { key, value } : { key };
    });
    onCommit(answers.length > 0 ? answers : undefined);
  };
  const withValue = (key: string, value: string) =>
    tags.map((tag) => (tag.key === key ? { key, value } : tag));

  const valued = tags.flatMap((tag) => {
    const option = optionFor(tag.key);
    return option?.value_type ? [{ tag, option, valueType: option.value_type }] : [];
  });

  return (
    <>
      <div className="submission-check-grid">
        {choices.map((option) => {
          const checked = tags.some((tag) => tag.key === option.value);
          return (
            <CheckField
              key={option.value}
              label={option.label}
              checked={checked}
              disabled={disabled || (full && !checked)}
              onChange={(event) =>
                commit(
                  event.currentTarget.checked
                    ? [...tags, { key: option.value }]
                    : tags.filter((tag) => tag.key !== option.value),
                )
              }
            />
          );
        })}
      </div>
      {valued.length > 0 && (
        <div className="submission-tag-values">
          {valued.map(({ tag, option, valueType }) => {
            const label = translate(locale, "submitFlow.tagValue", { tag: option.label });
            return valueType === "boolean" ? (
              <SelectField
                label={label}
                key={tag.key}
                value={tag.value ?? ""}
                disabled={disabled}
                onChange={(event) => commit(withValue(tag.key, event.currentTarget.value))}
              >
                <option value="">{translate(locale, "submitFlow.chooseOption")}</option>
                <option value="true">{translate(locale, "common.true")}</option>
                <option value="false">{translate(locale, "common.false")}</option>
              </SelectField>
            ) : (
              <Field
                label={label}
                key={tag.key}
                type="text"
                // A number input would hide what was typed when the browser cannot parse it;
                // the raw text is the answer, so ask only for a decimal keypad.
                inputMode={valueType === "numeric" ? "decimal" : undefined}
                value={tag.value ?? ""}
                disabled={disabled}
                onChange={(event) => setTags(withValue(tag.key, event.currentTarget.value))}
                onBlur={() => commit(tags)}
              />
            );
          })}
        </div>
      )}
    </>
  );
}
