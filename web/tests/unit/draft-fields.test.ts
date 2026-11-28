import { expect, it } from "vitest";
import { ApiError } from "@/api-client/api-error";
import { DraftFields } from "@/features/submissions/draft-fields";
import type { FormFieldResponse } from "@/generated/types.gen";

function field(
  id: string,
  control: FormFieldResponse["control"],
  value_kind: FormFieldResponse["value_kind"],
) {
  return { id, control, value_kind };
}

it("translates submitted controls into typed changes and preserves credit identity", () => {
  const credits = field("credits", "credits", "credit_list");
  const tags = field("tags", "showcase_tags", "showcase_tag_list");
  const choices = field("types", "multi_choice", "string_list");
  const names = field("names", "text", "string_list");
  const flag = field("flag", "boolean", "boolean");
  const width = field("width", "number", "integer");
  const title = field("title", "text", "string");
  const editor = new DraftFields({
    credits: [{ name: "Ada", role: "designer", id: "owned" }],
    names: ["a", "b"],
    title: "old",
  });
  expect(editor.display(names)).toBe("a\nb");
  expect(editor.display(credits)).toContain("owned");
  expect(editor.display(choices)).toBe("");
  const form = new FormData();
  for (const [key, value] of [
    ["credits.name", "Ada"],
    ["credits.role", "builder"],
    ["credits.name", "New"],
    ["credits.name", ""],
    ["tags", "a"],
    ["tags.a", "yes"],
    ["tags", "b"],
    ["types", "small"],
    ["names", "b\r\nc\n"],
    ["flag", "false"],
    ["width", "0"],
    ["title", ""],
  ] as const)
    form.append(key, value);
  const changes = editor.operations(form, [credits, tags, choices, names, flag, width, title]);
  expect(changes.map(({ field_id, kind, value }) => [field_id, kind, value])).toEqual([
    [
      "credits",
      "set",
      [
        { name: "Ada", role: "builder", id: "owned" },
        { name: "New", role: "creator" },
      ],
    ],
    ["tags", "set", [{ key: "a", value: "yes" }, { key: "b" }]],
    ["types", "set", ["small"]],
    ["names", "set", ["b", "c"]],
    ["flag", "set", false],
    ["width", "set", 0],
    ["title", "unset", null],
  ]);
  form.set("width", "bad");
  expect(() => editor.operations(form, [width])).toThrow(ApiError);
});

it("leaves unchanged values alone and clears an emptied list", () => {
  const tags = field("tags", "showcase_tags", "showcase_tag_list");
  const title = field("title", "text", "string");
  const credits = field("credits", "credits", "credit_list");
  const editor = new DraftFields({ tags: [{ key: "old" }], title: "stable" });
  const form = new FormData();
  expect(editor.objects(credits)).toEqual([]);
  expect(editor.operations(form, [title, credits])).toEqual([]);
  form.set("title", "stable");
  expect(editor.operations(form, [tags, title])).toMatchObject([
    { field_id: "tags", kind: "unset", value: null },
  ]);
});
