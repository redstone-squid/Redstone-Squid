# TSDoc and comments

Write for an engineer who knows TypeScript and is new to this part of the catalogue.
Explain what callers can rely on and why the implementation makes choices that matter.
Use the [repository documentation guide](../docs/code-documentation-style.md) for the
shared principles.

## Document the contract

Use a `/** ... */` block for an interface that needs explanation. Start with a sentence
about its behavior. A single sentence often covers a small function. Give shared
components, types, and callbacks enough context to use them correctly. This includes
defaults, units, missing values, ordering, errors, and side effects when they matter.
In Astro or Svelte, put prop documentation on the relevant declaration in the
frontmatter or script.

Use `@param name - ...`, `@returns`, or `@throws` when the tag helps callers find a
detail the signature cannot express. Use `@example` when a small call and result make
a boundary easier to understand. Let TypeScript carry the types.

```ts
/**
 * Change a catalogue path's locale while preserving its query and fragment.
 *
 * @example
 * localizePath("/records?offset=20#results", "zh-CN");
 * // "/zh-cn/records?offset=20#results"
 */
```

## Explain decisions in place

Put a short comment beside a surprising step and name the behavior it protects.
Look for the reasons behind request boundaries, hydration timing, cancellation,
browser workarounds, and accessibility behavior. For example, the autocomplete
checks focus at mount because a visitor can start typing before Svelte hydrates.
A short signpost can also help readers find a phase in a longer function.

In tests, explain setup that would be hard to infer from the actions. The offline
test reloads after activation so the service worker controls the page before the
test takes the browser offline. Let test names and assertions describe ordinary
actions and outcomes.

When reviewing a change, look for context a reader would otherwise have to
reconstruct. Keep useful comments current and remove narration that repeats the
code.
