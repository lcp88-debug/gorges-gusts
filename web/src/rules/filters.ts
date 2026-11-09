// Filters: reading what the rider types, and which segments pass.
//
// A list's filters are one object, {field key: filter}, saved on the server as it is here
// (see types.ts for each kind's shape). A field that isn't filtered has no entry.

import { activeFields, FIELDS, shownNumber, type Field } from "./fields";
import { formatTime, parseTime } from "./times";
import type { Filters, NumberFilter, Row, TickValue } from "./types";

/** An entry that can't be used, with the message shown to the rider. */
export class FilterError extends Error {}

const NUMBER = /^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i;

/** What's typed in one min or max box: a number, or null for a blank. Throws FilterError. */
export function parseNumberBox(text: string, field: Field): number | null {
  const trimmed = text.trim().replace(/−/g, "-"); // The minus sign wind numbers are shown with.
  if (!trimmed) return null;
  const number = field.isTime ? parseTime(trimmed) : NUMBER.test(trimmed) ? Number(trimmed) : null;
  if (number == null) {
    const what = field.isTime ? "a time like 1:30" : "a number";
    throw new FilterError(`${field.label}: “${trimmed}” isn't ${what}.`);
  }
  if (!Number.isFinite(number)) {
    throw new FilterError(`${field.label}: “${trimmed}” isn't a usable number.`);
  }
  return number;
}

/** A number field's filter from its two boxes, or null if both are blank. Throws FilterError. */
export function parseNumberFilter(key: string, min: string, max: string): NumberFilter | null {
  const field = FIELDS[key];
  const bounds: NumberFilter = {};
  const low = parseNumberBox(min, field);
  const high = parseNumberBox(max, field);
  if (low != null) bounds.min = low;
  if (high != null) bounds.max = high;
  if (low != null && high != null && low > high) {
    throw new FilterError(`${field.label}: the minimum is above the maximum.`);
  }
  return low == null && high == null ? null : bounds;
}

/** The text a number filter's boxes show, in the units they're typed in. */
export function numberBoxes(filter: NumberFilter | undefined, field: Field): { min: string; max: string } {
  const text = (number: number | undefined) =>
    number == null ? "" : field.isTime ? formatTime(number) : String(number);
  return { min: text(filter?.min), max: text(filter?.max) };
}

function numberMatches(value: unknown, bounds: NumberFilter, field: Field): boolean {
  if (typeof value !== "number") return false; // A blank can't be said to be within the limits.
  const shown = shownNumber(value / (field.perShownUnit ?? 1), field.shownDigits ?? 0);
  return (bounds.min ?? -Infinity) <= shown && shown <= (bounds.max ?? Infinity);
}

function ticksMatch(value: unknown, ticked: TickValue[]): boolean {
  return value != null && ticked.includes(value as TickValue);
}

function searchMatches(value: unknown, text: string): boolean {
  return value != null && String(value).toLowerCase().includes(text.trim().toLowerCase());
}

/**
 * The tick boxes to show for a field, as [value, label]. Unless the field has fixed
 * choices, they're the values found in `rows`, so no box matches nothing.
 */
export function tickChoices(key: string, rows: Row[]): [TickValue, string][] {
  const field = FIELDS[key];
  if (field.choices) return field.choices;
  const found = new Set<string>();
  for (const row of rows) {
    const value = field.value(row);
    if (value != null && value !== "") found.add(String(value));
  }
  return [...found]
    .sort((a, b) => compareText(a.toLowerCase(), b.toLowerCase()))
    .map((value) => [value, value]);
}

/**
 * The saved filters that apply to a list whose segments before filtering are `rows`.
 *
 * Ticks with no box are left out: a town that's no longer on the list. They stay saved
 * all the same, until the rider clears filters.
 */
export function filtersInUse(filters: Filters, rows: Row[], fastestTime: boolean): Filters {
  const fields = activeFields(fastestTime);
  const inUse: Filters = {};
  for (const [key, saved] of Object.entries(filters)) {
    const field = fields[key];
    if (!field) continue;
    if (field.kind === "ticks") {
      const boxes = new Set(tickChoices(key, rows).map(([value]) => value));
      const kept = (saved as TickValue[]).filter((value) => boxes.has(value));
      if (kept.length) inUse[key] = kept;
    } else {
      inUse[key] = saved;
    }
  }
  return inUse;
}

/** The rows that pass every filter in `filters` (see filtersInUse). */
export function filterRows(rows: Row[], filters: Filters): Row[] {
  return rows.filter((row) =>
    Object.entries(filters).every(([key, saved]) => {
      const field = FIELDS[key];
      const value = field.value(row);
      if (field.kind === "number") return numberMatches(value, saved as NumberFilter, field);
      if (field.kind === "ticks") return ticksMatch(value, saved as TickValue[]);
      return searchMatches(value, saved as string);
    }),
  );
}

/**
 * Ticks or unticks one box. Saved ticks that have no box right now stay as they are.
 */
export function toggleTick(filters: Filters, key: string, value: TickValue, ticked: boolean): Filters {
  const current = (filters[key] as TickValue[] | undefined) ?? [];
  const values = ticked ? [...new Set([...current, value])] : current.filter((v) => v !== value);
  return withFilter(filters, key, values.length ? sortTicks(values) : null);
}

/** `filters` with field `key` set to `filter`, or unfiltered if it's null. */
export function withFilter(filters: Filters, key: string, filter: Filters[string] | null): Filters {
  const next = { ...filters };
  if (filter == null) delete next[key];
  else next[key] = filter;
  return next;
}

function sortTicks(values: TickValue[]): TickValue[] {
  return [...values].sort((a, b) => compareText(String(a), String(b)));
}

/** Compares as Python compares strings, by code point, so orders match the server's. */
export function compareText(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

/** How many fields are filtered, for the Filter button. */
export function filterCount(filters: Filters): number {
  return Object.keys(filters).length;
}
