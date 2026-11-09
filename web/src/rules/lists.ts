// Putting a list together: wind now and best hour, filtering and sorting.

import { activeFields, FIELDS } from "./fields";
import { compareText, filterRows, filtersInUse } from "./filters";
import type { Filters, Row, Segment, Sort, TimeRange, WindHour } from "./types";

const at = (iso: string) => Date.parse(iso);

/**
 * A segment's wind now (the stored hour that is `thisHour`) and its best hour: the most
 * helpful hour inside `range`, or in all stored hours when it's null. The earliest of
 * equal hours wins.
 */
export function toRow(segment: Segment, thisHour: string, range: TimeRange): Row {
  const now = at(thisHour);
  const hours = segment.wind_hours;
  const inRange = range
    ? hours.filter((hour) => at(range[0]) <= at(hour.hour) && at(hour.hour) <= at(range[1]))
    : hours;
  let best: WindHour | null = null;
  for (const hour of inRange) if (best === null || hour.percent < best.percent) best = hour;
  return {
    ...segment,
    wind_now: hours.find((hour) => at(hour.hour) === now) ?? null,
    wind_best: best,
  };
}

export function canSort(key: string, fastestTime: boolean): boolean {
  return activeFields(fastestTime)[key]?.firstClick != null;
}

/** Choosing a sortable field: reverses the current sort, or starts a new one. */
export function chooseSort(sort: Sort, key: string): Sort {
  if (key === sort.field) return { field: key, direction: sort.direction === "desc" ? "asc" : "desc" };
  return { field: key, direction: FIELDS[key].firstClick ?? "desc" };
}

/** Sorted by the chosen field, with blanks last either way and ties in name order. */
export function sortRows(rows: Row[], sort: Sort): Row[] {
  const value = FIELDS[sort.field].value;
  const byName = [...rows].sort(
    (a, b) => compareText(a.name.toLowerCase(), b.name.toLowerCase()) || a.id - b.id,
  );
  const known = byName.filter((row) => value(row) != null);
  const blank = byName.filter((row) => value(row) == null);
  const sign = sort.direction === "desc" ? -1 : 1;
  const position = new Map(byName.map((row, index) => [row.id, index]));
  known.sort((a, b) => {
    const x = value(a) as number;
    const y = value(b) as number;
    return (x < y ? -sign : x > y ? sign : 0) || position.get(a.id)! - position.get(b.id)!;
  });
  return [...known, ...blank];
}

export interface BuiltList {
  rows: Row[]; // As shown: filtered and sorted.
  unfiltered: Row[]; // Every segment on the list, for tick boxes.
  filters: Filters; // The filters in use.
}

/** A list as shown, from its segments and the rider's choices. */
export function buildList(
  segments: Segment[],
  thisHour: string,
  choices: { sort: Sort; filters: Filters; timeRange: TimeRange },
  fastestTime: boolean,
): BuiltList {
  const unfiltered = segments.map((segment) => toRow(segment, thisHour, choices.timeRange));
  const filters = filtersInUse(choices.filters, unfiltered, fastestTime);
  // The server only sends a sort the list can use (app/lists.py, load_list_settings).
  return { rows: sortRows(filterRows(unfiltered, filters), choices.sort), unfiltered, filters };
}
