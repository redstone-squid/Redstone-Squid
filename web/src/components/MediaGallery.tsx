import "./media-gallery.css";
import { useState } from "react";

import { translate, type Locale } from "../lib/i18n";
import type { MediaItem } from "../lib/view-models";

type Props = { items: MediaItem[]; locale: Locale; title: string };

export default function MediaGallery({ items, locale, title }: Props) {
  const [index, setIndex] = useState(0);
  const [failed, setFailed] = useState<ReadonlySet<string>>(new Set());
  const move = (amount: number) =>
    setIndex((current) => (current + amount + items.length) % items.length);

  function navigate(event: React.KeyboardEvent<HTMLButtonElement>) {
    switch (event.key) {
      case "ArrowRight":
        move(1);
        break;
      case "ArrowLeft":
        move(-1);
        break;
      case "Home":
        setIndex(0);
        break;
      case "End":
        setIndex(items.length - 1);
        break;
      default:
        return;
    }
    event.preventDefault();
  }

  if (items.length === 0) return null;

  return (
    <section className="gallery" aria-label={translate(locale, "build.media")}>
      <div className="gallery-stage">
        {items.map((item, itemIndex) => (
          <div className="gallery-slide" hidden={itemIndex !== index} key={item.url}>
            {failed.has(item.url) ? (
              <span className="gallery-unavailable">
                {translate(locale, "common.imageUnavailable")}
              </span>
            ) : (
              <img
                src={item.url}
                alt={`${title} — ${item.label}`}
                loading={itemIndex === 0 ? "eager" : "lazy"}
                decoding="async"
                referrerPolicy="no-referrer"
                onError={() => setFailed((previous) => new Set(previous).add(item.url))}
              />
            )}
          </div>
        ))}
      </div>
      <div className="gallery-footer">
        <a
          href={items[index]?.url}
          rel="noreferrer"
          target="_blank"
          aria-label={`${translate(locale, "gallery.open")} (${translate(locale, "common.external")})`}
        >
          {translate(locale, "gallery.open")}
        </a>
        <span className="gallery-position" aria-live="polite">
          {translate(locale, "gallery.position", { current: index + 1, total: items.length })}
        </span>
        {items.length > 1 && (
          <div className="gallery-controls">
            <button
              type="button"
              aria-label={translate(locale, "gallery.previous")}
              onClick={() => move(-1)}
              onKeyDown={navigate}
            >
              ←
            </button>
            <button
              type="button"
              aria-label={translate(locale, "gallery.next")}
              onClick={() => move(1)}
              onKeyDown={navigate}
            >
              →
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
