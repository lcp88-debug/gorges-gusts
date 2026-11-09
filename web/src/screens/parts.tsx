// Small pieces the screens share.

import { useEffect, useState, type ReactNode } from "react";
import { show } from "../rules/fields";
import type { WindHour } from "../rules/types";

/** Whether the screen is wide enough for the computer layout. */
export function useWide(): boolean {
  const query = "(min-width: 760px)";
  const [wide, setWide] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const changed = () => setWide(media.matches);
    media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, []);
  return wide;
}

/** A wind effect as the list shows it, "–" when there's none. */
export function Wind({ hour, lessCertain }: { hour: WindHour | null; lessCertain: boolean }) {
  if (!hour) return <>–</>;
  return (
    <span className="wind-cell">
      {show.wind(hour.percent)}
      {lessCertain && (
        <>
          {" "}
          <abbr title="Less certain: steep ground or a steep descent">~</abbr>
        </>
      )}
    </span>
  );
}

const ICONS = {
  my_segments: "M4 19c2-5 4-8 8-8s6 3 8 8M12 11V4m0 0 3 3m-3-3L9 7",
  explore: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm3.5-12.5-2 5-5 2 2-5 5-2Z",
  you: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-7 8c1-3.5 3.6-5 7-5s6 1.5 7 5",
  gust: "M3 8h10a3 3 0 1 0-3-3M3 12h15a3 3 0 1 1-3 3M3 16h7",
};

export function Icon({ name, size = 22 }: { name: keyof typeof ICONS; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={ICONS[name]} />
    </svg>
  );
}

/**
 * A panel: slides up from the bottom on a phone, drops down below the control bar on a
 * computer, or with `dialog` sits in the middle of a computer screen.
 */
export function Sheet({ title, onClose, action, dialog = false, children }: {
  title: string;
  onClose: () => void;
  action?: ReactNode;
  dialog?: boolean;
  children: ReactNode;
}) {
  useEffect(() => {
    const key = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  return (
    <>
      <div className={dialog ? "scrim dialog-scrim" : "scrim"} onClick={onClose} />
      <div className={dialog ? "sheet dialog" : "sheet drop"} role="dialog" aria-label={title}>
        <div className="grab" />
        <h2>
          {title}
          {action ?? <button onClick={onClose}>Done</button>}
        </h2>
        {children}
      </div>
    </>
  );
}

/** "2 hours ago", for when the lists were last fetched from Strava. */
export function ago(iso: string | null, now = Date.now()): string {
  if (!iso) return "never";
  const minutes = Math.round((now - Date.parse(iso)) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return hours === 1 ? "1 hour ago" : `${hours} hours ago`;
  return `${Math.round(hours / 24)} days ago`;
}
