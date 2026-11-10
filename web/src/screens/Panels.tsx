// The Sort, Filter and Time panels, and the hour-by-hour view of one segment.

import { useState } from "react";
import { activeFields, FIELDS, show, type Field } from "../rules/fields";
import {
  FilterError,
  numberBoxes,
  parseNumberFilter,
  tickChoices,
  toggleTick,
  withFilter,
} from "../rules/filters";
import { canSort, chooseSort } from "../rules/lists";
import type { Filters, NumberFilter, Row, Sort, TickValue, TimeRange } from "../rules/types";
import { Sheet, Wind } from "./parts";

export function SortPanel({ sort, fastestTime, onSort, onClose }: {
  sort: Sort;
  fastestTime: boolean;
  onSort: (sort: Sort) => void;
  onClose: () => void;
}) {
  const keys = Object.keys(activeFields(fastestTime)).filter((key) => canSort(key, fastestTime));
  return (
    <Sheet title="Sort by" onClose={onClose}>
      <div className="sort-list">
        {keys.map((key) => {
          const on = sort.field === key;
          return (
            <button key={key} aria-pressed={on} onClick={() => onSort(chooseSort(sort, key))}>
              <span>{FIELDS[key].label}</span>
              <span className="muted small">{on ? (sort.direction === "asc" ? "Low to high ↑" : "High to low ↓") : ""}</span>
            </button>
          );
        })}
      </div>
      <p className="muted small">Choose the sorted one again to flip the order.</p>
    </Sheet>
  );
}

/**
 * Every filter box. The list changes as the rider types. A box that can't be read says
 * why, and its field keeps the filter it had.
 */
export function FilterPanel({ filters, unfiltered, fastestTime, onFilters, onClose }: {
  filters: Filters;
  unfiltered: Row[];
  fastestTime: boolean;
  onFilters: (filters: Filters) => void;
  onClose: () => void;
}) {
  const fields = activeFields(fastestTime);
  // What's typed in each number box, which can differ from the filter while it's being typed.
  const [boxes, setBoxes] = useState<Record<string, { min: string; max: string }>>(() =>
    Object.fromEntries(
      Object.entries(fields)
        .filter(([, field]) => field.kind === "number")
        .map(([key, field]) => [key, numberBoxes(filters[key] as NumberFilter | undefined, field)]),
    ),
  );
  const [errors, setErrors] = useState<Record<string, string>>({});

  function typeNumber(key: string, edge: "min" | "max", text: string) {
    const next = { ...boxes[key], [edge]: text };
    setBoxes({ ...boxes, [key]: next });
    try {
      onFilters(withFilter(filters, key, parseNumberFilter(key, next.min, next.max)));
      setErrors(({ [key]: _gone, ...rest }) => rest);
    } catch (error) {
      if (!(error instanceof FilterError)) throw error;
      setErrors({ ...errors, [key]: error.message });
    }
  }

  function clearAll() {
    setBoxes(Object.fromEntries(Object.keys(boxes).map((key) => [key, { min: "", max: "" }])));
    setErrors({});
    onFilters({});
  }

  const ticked = (key: string) => (filters[key] as TickValue[] | undefined) ?? [];

  // The boxes for each kind of filter.
  const editors: Record<Field["kind"], (key: string, field: Field) => JSX.Element> = {
    search: (key, field) => (
      <div className="filter-group" key={key}>
        <label className="field" htmlFor={`${key}_search`} style={{ fontSize: ".9rem", color: "var(--fg)", fontWeight: 600 }}>
          {field.label} contains
        </label>
        <input id={`${key}_search`} type="text" value={(filters[key] as string | undefined) ?? ""}
          onChange={(event) => onFilters(withFilter(filters, key, event.target.value || null))} />
      </div>
    ),
    ticks: (key, field) => {
      const choices = tickChoices(key, unfiltered);
      return (
        <div className="filter-group" key={key}>
          <span>{field.label}</span>
          <div className="ticks">
            {choices.length === 0 && <em className="muted small">none on this list</em>}
            {choices.map(([value, label]) => (
              <label key={String(value)}>
                <input type="checkbox" checked={ticked(key).includes(value)}
                  onChange={(event) => onFilters(toggleTick(filters, key, value, event.target.checked))} />
                {label}
              </label>
            ))}
          </div>
        </div>
      );
    },
    number: (key, field) => (
      <div className="filter-group" key={key}>
        <span>{field.label}{field.unit ? ` (${field.unit})` : ""}</span>
        <div className="minmax">
          {(["min", "max"] as const).map((edge) => (
            <label className="field" key={edge}>
              {edge === "min" ? "Min" : "Max"}
              <input id={`${key}_${edge}`} type="text" inputMode={field.isTime ? "text" : "decimal"}
                placeholder={field.isTime ? "m:ss" : ""} value={boxes[key][edge]} aria-invalid={key in errors}
                onChange={(event) => typeNumber(key, edge, event.target.value)} />
            </label>
          ))}
        </div>
        {errors[key] && <p className="error" role="alert">{errors[key]}</p>}
      </div>
    ),
  };

  return (
    <Sheet title="Filters" onClose={onClose} action={<button onClick={clearAll}>Clear all filters</button>}>
      {Object.entries(fields).map(([key, field]) => editors[field.kind](key, field))}
      <div className="sheet-foot">
        <span className="muted small">The list changes as you type.</span>
        <button className="btn primary" onClick={onClose}>Show results</button>
      </div>
    </Sheet>
  );
}

export function TimePanel({ hours, range, error, onRange, onClose }: {
  hours: string[];
  range: TimeRange;
  error: string | null;
  onRange: (range: TimeRange) => void;
  onClose: () => void;
}) {
  const [first, last] = range ?? [hours[0], hours[hours.length - 1]];
  const position = (iso: string) => Math.max(0, hours.findIndex((hour) => Date.parse(hour) === Date.parse(iso)));
  const options = hours.map((hour, index) => <option key={hour} value={index}>{show.hour(hour)}</option>);
  // Moving one end past the other moves the other end with it.
  const setStart = (index: number) => onRange([hours[index], hours[Math.max(index, position(last))]]);
  const setEnd = (index: number) => onRange([hours[Math.min(index, position(first))], hours[index]]);
  return (
    <Sheet title="Time range" onClose={onClose} action={<button onClick={() => onRange(null)}>All 48 hours</button>}>
      <div className="minmax">
        <label className="field">From
          <select id="range_start" value={position(first)} onChange={(event) => setStart(Number(event.target.value))}>{options}</select>
        </label>
        <label className="field">To
          <select id="range_end" value={position(last)} onChange={(event) => setEnd(Number(event.target.value))}>{options}</select>
        </label>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <p className="muted small">Best hour is chosen from inside this range. Wind now and the hours view always show everything.</p>
      <div className="sheet-foot"><span /><button className="btn primary" onClick={onClose}>Done</button></div>
    </Sheet>
  );
}

export function HoursPanel({ row, range, wide, onClose }: {
  row: Row;
  range: TimeRange;
  wide: boolean;
  onClose: () => void;
}) {
  const inRange = (iso: string) =>
    !range || (Date.parse(range[0]) <= Date.parse(iso) && Date.parse(iso) <= Date.parse(range[1]));
  return (
    <Sheet title={row.name} onClose={onClose} dialog={wide}>
      <p className="muted small" style={{ marginTop: 0 }}>
        Every hour of the next 48 with a forecast.{range ? " Shaded hours are in your time range." : ""}
      </p>
      <div className="hours num">
        {row.wind_hours.map((hour, index) => {
          const midnight = new Date(hour.hour).getHours() === 0;
          const classes = [range && inRange(hour.hour) ? "in" : "", hour === row.wind_best ? "best" : "", index > 0 && midnight ? "day" : ""];
          return (
            <div key={hour.hour} className={classes.join(" ").trim()}>
              <span>{index === 0 || midnight ? show.hour(hour.hour) : show.clock(hour.hour)}</span>
              <b><Wind hour={hour} lessCertain={row.less_certain} /></b>
              <span className="muted">{show.mph(hour.speed_ms)} from {show.compass(hour.from_degrees)}</span>
            </div>
          );
        })}
      </div>
    </Sheet>
  );
}
