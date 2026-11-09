// The things shown about every segment on a list, how each is shown, sorted and filtered.

import { formatTime } from "./times";
import type { Direction, Row, TickValue } from "./types";

export const METERS_PER_MILE = 1609.34;
export const FEET_PER_METER = 3.28084;

// Strava's climb category 0 to 5 as the list shows it, so a category is its position here.
export const CLIMB_CATEGORIES = ["none", "4", "3", "2", "1", "HC"];

/**
 * One field. `kind` says how it's filtered: "number" (a minimum and a maximum), "ticks"
 * (tick boxes) or "search". `firstClick` is the direction of the first sort on it, or
 * null if it can't be sorted. `value` reads it from a row, giving null for a blank, which
 * always sorts last.
 *
 * For a number, `perShownUnit` is how many of the row's units make one shown unit
 * (1609.34 metres to a mile), and `shownDigits` how many decimal places are shown.
 * `isTime` means entries are typed as minutes and seconds. Filters compare the value as
 * it's shown, so a segment shown as "1.0 mi" passes "at least 1".
 *
 * For ticks, `choices` are fixed boxes as [stored value, label]. Without them, the boxes
 * are the values found on the list (towns).
 */
export interface Field {
  label: string;
  kind: "number" | "ticks" | "search";
  firstClick: Direction | null;
  value: (row: Row) => number | string | null;
  perShownUnit?: number;
  shownDigits?: number;
  isTime?: boolean;
  unit?: string; // Shown beside the filter boxes.
  choices?: [TickValue, string][];
}

const wind = (key: "wind_now" | "wind_best") => (row: Row) => row[key]?.percent ?? null;

// The keys are saved in riders' list settings (and checked in app/lists.py), so think
// twice before renaming one: a saved key that's no longer here is ignored.
export const FIELDS: Record<string, Field> = {
  name: { label: "Segment", kind: "search", firstClick: null, value: (row) => row.name },
  town: { label: "Town", kind: "ticks", firstClick: null, value: (row) => row.town },
  distance: {
    label: "Distance", kind: "number", firstClick: "desc", value: (row) => row.distance_m,
    perShownUnit: METERS_PER_MILE, shownDigits: 1, unit: "mi",
  },
  average_grade: {
    label: "Avg grade", kind: "number", firstClick: "desc", value: (row) => row.average_grade,
    shownDigits: 1, unit: "%",
  },
  steepest_grade: {
    label: "Steepest grade", kind: "number", firstClick: "desc", value: (row) => row.maximum_grade,
    shownDigits: 1, unit: "%",
  },
  elevation_gain: {
    label: "Elevation gain", kind: "number", firstClick: "desc", value: (row) => row.elevation_gain_m,
    perShownUnit: 1 / FEET_PER_METER, unit: "ft",
  },
  // Strava's 0 to 5, so biggest first is hardest first: HC, 1, 2, 3, 4, then none.
  climb_category: {
    label: "Climb category", kind: "ticks", firstClick: "desc", value: (row) => row.climb_category,
    choices: CLIMB_CATEGORIES.map((label, value) => [value, label]),
  },
  your_attempts: {
    label: "Your attempts, 90 days", kind: "number", firstClick: "desc", value: (row) => row.recent_efforts,
  },
  // Seconds, fastest first. Blank for segments the rider hasn't attempted.
  recent_best: {
    label: "Recent best", kind: "number", firstClick: "asc", value: (row) => row.recent_best, isTime: true,
  },
  // Most negative first: the biggest help from the wind.
  wind_now: { label: "Wind now", kind: "number", firstClick: "asc", value: wind("wind_now"), unit: "%" },
  best_hour: { label: "Best hour", kind: "number", firstClick: "asc", value: wind("wind_best"), unit: "%" },
  people_on_strava: {
    label: "People on Strava", kind: "number", firstClick: "desc", value: (row) => row.athlete_count,
  },
  total_attempts: {
    label: "Total attempts", kind: "number", firstClick: "desc", value: (row) => row.effort_count,
  },
  // Seconds, fastest first. Only exists while the fastest-time setting is on.
  fastest_time: {
    label: "Fastest time", kind: "number", firstClick: "asc", value: (row) => row.fastest_time_s ?? null,
    isTime: true,
  },
};

// Fields that only exist while their app setting is on.
const SETTING_FIELDS = new Set(["fastest_time"]);

/** The fields a list has. Without the fastest-time setting, that field is absent. */
export function activeFields(fastestTime: boolean): Record<string, Field> {
  return Object.fromEntries(
    Object.entries(FIELDS).filter(([key]) => fastestTime || !SETTING_FIELDS.has(key)),
  );
}

/** A number rounded as the list shows it. Display and filters both use this. */
export function shownNumber(value: number, digits = 0): number {
  return Number(value.toFixed(digits));
}

// How each value is shown. A blank is "–".

const BLANK = "–";
const grouped = (n: number) => n.toLocaleString("en-US");

export const show = {
  miles: (meters: number | null) =>
    meters == null ? BLANK : `${shownNumber(meters / METERS_PER_MILE, 1).toFixed(1)} mi`,
  percent: (grade: number | null) => (grade == null ? BLANK : `${shownNumber(grade, 1).toFixed(1)}%`),
  feet: (meters: number | null) =>
    meters == null ? BLANK : `${grouped(shownNumber(meters * FEET_PER_METER))} ft`,
  count: (value: number | null) => (value == null ? BLANK : grouped(value)),
  climbCategory: (category: number | null) =>
    category != null && category >= 0 && category < CLIMB_CATEGORIES.length ? CLIMB_CATEGORIES[category] : BLANK,
  duration: (seconds: number | null | undefined) => (seconds == null ? BLANK : formatTime(seconds)),
  blank: (value: string | null) => value || BLANK,
  /** A wind effect: "+3%", "−9%" (a true minus sign) or "0%". */
  wind: (percent: number) => {
    const rounded = shownNumber(percent);
    if (rounded === 0) return "0%";
    return rounded > 0 ? `+${rounded}%` : `−${-rounded}%`;
  },
  mph: (speedMs: number) => `${shownNumber(speedMs * 2.23694)} mph`,
  compass: (degrees: number) =>
    ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"][
      Math.round(degrees / 22.5) % 16
    ],
  /** An hour in the rider's own time zone: "Sat 9am". */
  hour: (iso: string) => {
    const date = new Date(iso);
    const day = date.toLocaleDateString("en-US", { weekday: "short" });
    return `${day} ${show.clock(iso)}`;
  },
  clock: (iso: string) => {
    const hours = new Date(iso).getHours();
    return `${hours % 12 || 12}${hours < 12 ? "am" : "pm"}`;
  },
};
