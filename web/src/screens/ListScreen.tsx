// My Segments or Explore: the control bar, then cards on a phone or a table on a computer.

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { api, Refused, type LoggedIn } from "../api";
import { FIELDS, show } from "../rules/fields";
import { filterCount } from "../rules/filters";
import { buildList, chooseSort } from "../rules/lists";
import type { Filters, ListData, ListKind, Row, Sort, TimeRange } from "../rules/types";
import { FilterPanel, HoursPanel, SortPanel, TimePanel } from "./Panels";
import { Wind } from "./parts";

type PanelName = "sort" | "filter" | "time" | null;

const SAVE_FILTERS_AFTER_MS = 500;

export function ListScreen({ kind, state, data, wide, onRefresh, onChoices }: {
  kind: ListKind;
  state: LoggedIn;
  data: ListData;
  wide: boolean;
  onRefresh: () => void;
  // Keeps the rider's sort and filters for when they come back to this tab.
  onChoices: (choices: { sort: Sort; filters: Filters }) => void;
}) {
  // The rider's choices show at once; saving them happens in the background.
  const [sort, setSort] = useState<Sort>(data.sort);
  const [filters, setFilters] = useState<Filters>(data.filters);
  const [range, setRange] = useState<TimeRange>(data.time_range);
  const [rangeError, setRangeError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [panel, setPanel] = useState<PanelName>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [hoursFor, setHoursFor] = useState<Row | null>(null);
  const filterTimer = useRef<number | undefined>(undefined);

  // A newer list from the server, after a refresh or a wind update.
  useEffect(() => setRange(data.time_range), [data.time_range]);

  const built = useMemo(
    () => buildList(data.segments, data.this_hour, { sort, filters, timeRange: range }, state.fastest_time),
    [data, sort, filters, range, state.fastest_time],
  );

  const failed = (error: unknown) => setSaveError(error instanceof Refused ? error.message : "Couldn't save that. Check your connection.");

  function changeSort(next: Sort) {
    setSort(next);
    onChoices({ sort: next, filters });
    api.saveSort(kind, next).then(() => setSaveError(null), failed);
  }

  function changeFilters(next: Filters) {
    setFilters(next);
    onChoices({ sort, filters: next });
    window.clearTimeout(filterTimer.current);
    filterTimer.current = window.setTimeout(
      () => api.saveFilters(kind, next).then(() => setSaveError(null), failed),
      SAVE_FILTERS_AFTER_MS,
    );
  }

  function changeRange(next: TimeRange) {
    const before = range;
    setRange(next);
    api.saveTimeRange(kind, next).then(
      () => setRangeError(null),
      (error) => {
        setRange(before);
        setRangeError(error instanceof Refused ? error.message : "Couldn't change the time range. Check your connection.");
      },
    );
  }

  const pulled = usePullToRefresh(!wide && !state.syncing, onRefresh);

  const count = filterCount(built.filters);
  const toggle = (name: PanelName) => setPanel(panel === name ? null : name);
  const sortField = FIELDS[sort.field];

  return (
    <>
      <div className="controls">
        <div className="wrap">
          <button className="chip" onClick={() => toggle("sort")} aria-expanded={panel === "sort"}>
            Sort <span className="val">{sortField.label} {sort.direction === "asc" ? "↑" : "↓"}</span>
          </button>
          <button className={count ? "chip on" : "chip"} onClick={() => toggle("filter")} aria-expanded={panel === "filter"}>
            Filter{count > 0 && <span className="val">({count})</span>}
          </button>
          <button className={range ? "chip on" : "chip"} onClick={() => toggle("time")} aria-expanded={panel === "time"}>
            Time <span className="val">{range ? `${show.hour(range[0])}–${show.clock(range[1])}` : "48 h"}</span>
          </button>
          {wide && <span className="count">{built.rows.length} of {built.unfiltered.length} segments</span>}
          {panel === "sort" && (
            <SortPanel sort={sort} fastestTime={state.fastest_time} onSort={changeSort} onClose={() => setPanel(null)} />
          )}
          {panel === "filter" && (
            <FilterPanel filters={filters} unfiltered={built.unfiltered} fastestTime={state.fastest_time}
              onFilters={changeFilters} onClose={() => setPanel(null)} />
          )}
          {panel === "time" && (
            <TimePanel hours={data.hours} range={range} error={rangeError} onRange={changeRange} onClose={() => setPanel(null)} />
          )}
        </div>
        <SyncProgress state={state} />
      </div>
      {!wide && (
        <div className={pulled ? "pull dragging" : "pull"} style={{ height: pulled }} aria-hidden="true">
          {pulled >= PULL_TO_REFRESH_PX ? "Release to refresh" : "Pull to refresh"}
        </div>
      )}
      <main className="wrap">
        <SyncMessage state={state} wide={wide} />
        {saveError && <p className="notice error-notice" role="alert">{saveError}</p>}
        {kind === "explore" && !state.pooled && (
          <p className="notice">Explore matches My Segments for now: other riders' segments will appear once sharing is turned on.</p>
        )}
        {built.rows.length === 0 ? (
          <p className="empty">
            {count > 0
              ? "No segments match your filters."
              : state.syncing
                ? "Your segments will appear here once Strava has sent them."
                : "No segments in this area found in your road rides from the last 90 days."}
          </p>
        ) : wide ? (
          <Table rows={built.rows} sort={sort} onSort={changeSort} openId={openId} setOpenId={setOpenId}
            fastestTime={state.fastest_time} onHours={setHoursFor} />
        ) : (
          <div className="cards">
            {built.rows.map((row) => (
              <Card key={row.id} row={row} open={openId === row.id} fastestTime={state.fastest_time}
                onToggle={() => setOpenId(openId === row.id ? null : row.id)} onHours={() => setHoursFor(row)} />
            ))}
          </div>
        )}
        <p className="muted small" style={{ marginTop: "1.25rem" }}>
          Wind numbers are estimates from forecast wind: the change in your time at the same effort, compared with no
          wind. Negative means faster. ~ means less certain.
          {state.wind_pending && built.unfiltered.length > 0 && " Working out the latest wind…"}
        </p>
      </main>
      {hoursFor && (
        <HoursPanel row={built.unfiltered.find((row) => row.id === hoursFor.id) ?? hoursFor} range={range} wide={wide}
          onClose={() => setHoursFor(null)} />
      )}
    </>
  );
}

function Facts({ row, fastestTime }: { row: Row; fastestTime: boolean }) {
  const facts: [string, string][] = [
    ["Distance", show.miles(row.distance_m)],
    ["Avg grade", show.percent(row.average_grade)],
    ["Steepest grade", show.percent(row.maximum_grade)],
    ["Elevation gain", show.feet(row.elevation_gain_m)],
    ["Climb category", show.climbCategory(row.climb_category)],
    ["Your attempts, 90 days", show.count(row.recent_efforts)],
    ["Recent best", show.duration(row.recent_best)],
    ["People on Strava", show.count(row.athlete_count)],
    ["Total attempts", show.count(row.effort_count)],
  ];
  if (fastestTime) facts.push(["Fastest time", show.duration(row.fastest_time_s)]);
  return (
    <dl className="facts">
      {facts.map(([label, value]) => (
        <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  );
}

function DetailActions({ row, onHours }: { row: Row; onHours: () => void }) {
  return (
    <div className="body-actions">
      {row.wind_hours.length > 0 ? <button className="btn" onClick={onHours}>See all hours</button> : <span />}
      <a className="strava-link" href={`https://www.strava.com/segments/${row.id}`} target="_blank" rel="noopener">
        View on Strava
      </a>
    </div>
  );
}

function BestHour({ row }: { row: Row }) {
  return (
    <>
      <span className="value"><Wind hour={row.wind_best} lessCertain={row.less_certain} /></span>
      {row.wind_best && <span className="sub">{show.hour(row.wind_best.hour)}</span>}
    </>
  );
}

function Card({ row, open, fastestTime, onToggle, onHours }: {
  row: Row;
  open: boolean;
  fastestTime: boolean;
  onToggle: () => void;
  onHours: () => void;
}) {
  return (
    <article className="card">
      <button className="card-head" aria-expanded={open} onClick={onToggle}>
        <span className="card-name"><b>{row.name}</b><span>{show.blank(row.town)}</span></span>
        <span className="stat">
          <span className="label">Wind now</span>
          <span className="value"><Wind hour={row.wind_now} lessCertain={row.less_certain} /></span>
        </span>
        <span className="stat"><span className="label">Best hour</span><BestHour row={row} /></span>
      </button>
      {open && (
        <div className="card-body">
          <Facts row={row} fastestTime={fastestTime} />
          <DetailActions row={row} onHours={onHours} />
        </div>
      )}
    </article>
  );
}

// The computer table's columns. The rest are in the row that opens.
const COLUMNS = ["wind_now", "best_hour", "name", "town", "distance", "average_grade", "your_attempts"];

function Table({ rows, sort, onSort, openId, setOpenId, fastestTime, onHours }: {
  rows: Row[];
  sort: Sort;
  onSort: (sort: Sort) => void;
  openId: number | null;
  setOpenId: (id: number | null) => void;
  fastestTime: boolean;
  onHours: (row: Row) => void;
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th style={{ width: "2rem" }}><span className="plain"><span className="sr-only">Open</span></span></th>
            {COLUMNS.map((key) => {
              const field = FIELDS[key];
              const on = sort.field === key;
              // Name and town can't be sorted; every other column is a number.
              if (!field.firstClick) {
                return <th key={key} scope="col" className="text"><span className="plain">{field.label}</span></th>;
              }
              return (
                <th key={key} scope="col"
                  aria-sort={on ? (sort.direction === "asc" ? "ascending" : "descending") : undefined}>
                  <button onClick={() => onSort(chooseSort(sort, key))}>
                    {field.label}
                    <span className="arrow" aria-hidden="true">{on ? (sort.direction === "asc" ? "↑" : "↓") : ""}</span>
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const open = openId === row.id;
            return (
              <Fragment key={row.id}>
                <tr className="row" aria-expanded={open} onClick={() => setOpenId(open ? null : row.id)}>
                  <td>
                    <button className="sr-only" aria-expanded={open} onClick={(event) => { event.stopPropagation(); setOpenId(open ? null : row.id); }}>
                      {open ? "Close" : "Open"} {row.name}
                    </button>
                    <span className="chev" aria-hidden="true">›</span>
                  </td>
                  <td className="wind"><Wind hour={row.wind_now} lessCertain={row.less_certain} /></td>
                  <td>
                    <b className="wind"><Wind hour={row.wind_best} lessCertain={row.less_certain} /></b>{" "}
                    {row.wind_best && <span className="muted small">{show.hour(row.wind_best.hour)}</span>}
                  </td>
                  <td className="name text">{row.name}</td>
                  <td className="town text">{show.blank(row.town)}</td>
                  <td>{show.miles(row.distance_m)}</td>
                  <td>{show.percent(row.average_grade)}</td>
                  <td>{show.count(row.recent_efforts)}</td>
                </tr>
                {open && (
                  <tr className="detail">
                    <td />
                    <td colSpan={COLUMNS.length}>
                      <div style={{ display: "grid", gap: ".8rem" }}>
                        <Facts row={row} fastestTime={fastestTime} />
                        <DetailActions row={row} onHours={() => onHours(row)} />
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** The thin bar under the controls while Strava data is coming in. */
function SyncProgress({ state }: { state: LoggedIn }) {
  const job = state.job;
  if (!state.syncing || !job) return null;
  const width = job.total ? `${(100 * job.done) / job.total}%` : "12%";
  return <div className="progress"><i style={{ width }} /></div>;
}

export function syncText(state: LoggedIn): string | null {
  const job = state.job;
  if (!state.syncing || !job) return null;
  if (job.status === "paused") {
    const at = job.resume_at ? new Date(job.resume_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "soon";
    return `Waiting for Strava's request limit. This will carry on at ${at}.${job.total ? ` (${job.done} of ${job.total} done)` : ""}`;
  }
  if (job.phase === "segments") return `Fetching segment details: ${job.done} of ${job.total}`;
  if (job.phase === "rides" && job.total) return `Reading your rides: ${job.done} of ${job.total}`;
  return "Checking Strava for your rides…";
}

function SyncMessage({ state, wide }: { state: LoggedIn; wide: boolean }) {
  const text = syncText(state);
  if (text) return <div className="progress-msg" role="status">{text}</div>;
  if (!wide) return <p className="muted small" style={{ margin: "0 0 .6rem" }}>Pull down to refresh.</p>;
  return null;
}

const PULL_TO_REFRESH_PX = 60;

/** On a phone: pulling the list down from the top refreshes it. Returns how far it's pulled. */
function usePullToRefresh(enabled: boolean, onRefresh: () => void): number {
  const [pulled, setPulled] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let start: number | null = null;
    let distance = 0;
    const down = (event: TouchEvent) => {
      start = window.scrollY <= 0 ? event.touches[0].clientY : null;
    };
    const move = (event: TouchEvent) => {
      if (start == null) return;
      distance = Math.min(90, Math.max(0, event.touches[0].clientY - start) * 0.5);
      setPulled(distance);
    };
    const up = () => {
      if (start != null && distance >= PULL_TO_REFRESH_PX) onRefresh();
      start = null;
      distance = 0;
      setPulled(0);
    };
    window.addEventListener("touchstart", down, { passive: true });
    window.addEventListener("touchmove", move, { passive: true });
    window.addEventListener("touchend", up);
    return () => {
      window.removeEventListener("touchstart", down);
      window.removeEventListener("touchmove", move);
      window.removeEventListener("touchend", up);
    };
  }, [enabled, onRefresh]);
  return pulled;
}
