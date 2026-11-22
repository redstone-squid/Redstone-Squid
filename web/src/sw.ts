/// <reference lib="webworker" />
import { matchPrecache, precacheAndRoute } from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";
import { NetworkOnly } from "workbox-strategies";
import { DEFAULT_LOCALE, localeFromPath, localizePath } from "./locales/locale";

// The site is server-rendered and changes often, so pages always come from the network.
// The worker only keeps styles and the offline page, and shows that page in the visitor's
// locale when a navigation fails.
declare const self: ServiceWorkerGlobalScope;

precacheAndRoute(self.__WB_MANIFEST);

const network = new NetworkOnly();
registerRoute(
  new NavigationRoute(async (options) => {
    try {
      return await network.handle(options);
    } catch {
      const locale = localeFromPath(options.url.pathname) ?? DEFAULT_LOCALE;
      return (await matchPrecache(localizePath("/offline", locale))) ?? Response.error();
    }
  }),
);
