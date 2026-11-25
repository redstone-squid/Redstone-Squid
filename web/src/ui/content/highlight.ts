import { createHighlighterCoreSync, type ThemeRegistration } from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";
import diff from "shiki/langs/diff.mjs";
import json from "shiki/langs/json.mjs";
import log from "shiki/langs/log.mjs";
import python from "shiki/langs/python.mjs";
import shellscript from "shiki/langs/shellscript.mjs";
import toml from "shiki/langs/toml.mjs";
import yaml from "shiki/langs/yaml.mjs";

/** Languages `CodeBlock` can highlight. Each one adds its grammar to every bundle using it. */
export type CodeLanguage = "diff" | "json" | "log" | "python" | "shellscript" | "toml" | "yaml";

// Each "color" is a utility class string, which CodeBlock puts on the token, because inline
// styles are not allowed. The classes use the theme's text tokens, so they follow light,
// dark and high contrast and keep the 4.5:1 contrast of text.
const theme: ThemeRegistration = {
  name: "catalogue",
  colors: { "editor.foreground": "text-fg" },
  tokenColors: [
    {
      scope: ["comment", "punctuation.definition.comment"],
      settings: { foreground: "text-fg-muted italic" },
    },
    {
      scope: ["keyword", "storage", "keyword.operator.word", "markup.heading"],
      settings: { foreground: "text-accent" },
    },
    {
      scope: ["string", "constant.other.symbol", "markup.inserted"],
      settings: { foreground: "text-success" },
    },
    {
      scope: [
        "constant.numeric",
        "constant.language",
        "support.constant",
        "constant.character.escape",
      ],
      settings: { foreground: "text-warning" },
    },
    {
      scope: [
        "entity.name.function",
        "support.function",
        "entity.name.type",
        "entity.name.class",
        "support.type",
        "support.class",
        "entity.name.tag",
        "meta.diff.range",
        "meta.diff.header",
      ],
      settings: { foreground: "text-info" },
    },
    { scope: ["markup.deleted", "invalid"], settings: { foreground: "text-danger" } },
  ],
};

// The JavaScript regex engine avoids Oniguruma's WASM, so highlighting runs synchronously
// during server rendering and in islands alike.
const highlighter = createHighlighterCoreSync({
  themes: [theme],
  langs: [diff, json, log, python, shellscript, toml, yaml],
  engine: createJavaScriptRegexEngine(),
});

/** Splits `code` into runs of text, each with the utility classes that color it. */
export function highlight(code: string, lang: CodeLanguage): { content: string; class?: string }[] {
  return highlighter
    .codeToTokensBase(code, { lang, theme: "catalogue" })
    .flatMap((line, index) => [
      ...(index > 0 ? [{ content: "\n" }] : []),
      ...line.map((token) => ({ content: token.content, class: token.color })),
    ]);
}
