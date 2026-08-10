import { useState } from "react";

import { translate, type Locale } from "../lib/i18n";
import type { MediaItem } from "../lib/view-models";

type Props = { items: MediaItem[]; locale: Locale; title: string };

export default function MediaGallery({ items, locale, title }: Props) {
  const [index, setIndex] = useState(0);
  const move = (amount: number) =>
    setIndex((current) => (current + amount + items.length) % items.length);

  if (items.length === 0) return null;

  return (
    <section className="gallery" aria-label={translate(locale, "build.media")}>
      <div className="gallery-stage">
        {items.map((item, itemIndex) => (
          <div className="gallery-slide" hidden={itemIndex !== index} key={item.url}>
            <div className="media-frame">
              <span className="remote-image__fallback">
                {translate(locale, "common.imageUnavailable")}
              </span>
              <img
                src={item.url}
                alt={`${title} — ${item.label}`}
                loading={itemIndex === 0 ? "eager" : "lazy"}
                decoding="async"
                referrerPolicy="no-referrer"
                onError={(event) => {
                  event.currentTarget.hidden = true;
                }}
              />
            </div>
          </div>
        ))}
      </div>
      <div className="gallery-footer">
        <a href={items[index]?.url} rel="noreferrer" target="_blank">
          {translate(locale, "gallery.open")}{" "}
          <span className="visually-hidden">({translate(locale, "common.external")})</span>
        </a>
        <span className="gallery-status" aria-live="polite">
          {translate(locale, "gallery.position", { current: index + 1, total: items.length })}
        </span>
        {items.length > 1 && (
          <div className="gallery-controls">
            <button
              className="gallery-control"
              type="button"
              aria-label={translate(locale, "gallery.previous")}
              onClick={() => move(-1)}
              onKeyDown={(event) => {
                if (event.key === "ArrowRight") move(1);
                else if (event.key === "Home") setIndex(0);
                else if (event.key === "End") setIndex(items.length - 1);
              }}
            >
              ←
            </button>
            <button
              className="gallery-control"
              type="button"
              aria-label={translate(locale, "gallery.next")}
              onClick={() => move(1)}
              onKeyDown={(event) => {
                if (event.key === "ArrowLeft") move(-1);
                else if (event.key === "Home") setIndex(0);
                else if (event.key === "End") setIndex(items.length - 1);
              }}
            >
              →
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
