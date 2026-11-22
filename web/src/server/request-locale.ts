import { AsyncLocalStorage } from "node:async_hooks";
import { setLocaleSource, type Locale } from "@/locales/locale";

// Each request renders in its own async context, so concurrent requests keep their locales.
const requestLocale = new AsyncLocalStorage<Locale>();
setLocaleSource(() => {
  const locale = requestLocale.getStore();
  if (locale === undefined)
    throw new Error("No request locale. Render inside middleware or pass a locale explicitly.");
  return locale;
});

/** Run `render` with `locale` as the result of `getLocale()` for everything it renders. */
export function withRequestLocale<T>(locale: Locale, render: () => T): T {
  return requestLocale.run(locale, render);
}
