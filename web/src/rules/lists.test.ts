import { buildList, canSort, chooseSort, sortRows, toRow } from "./lists";
import { THIS_HOUR, hour, threeSegments, windHours, withColumn, withWindNow } from "./testing";
import type { Filters, Segment, Sort, TimeRange } from "./types";

function order(segments: Segment[], sort: Sort, filters: Filters = {}, timeRange: TimeRange = null, fastestTime = false) {
  return buildList(segments, THIS_HOUR, { sort, filters, timeRange }, fastestTime).rows.map((row) => row.id);
}

const first = (field: string): Sort => chooseSort({ field: "your_attempts", direction: "desc" }, field);

// Each field, made-up values for segments 500, 600 and 700, and the order its first sort gives.
const NUMBER_FIELDS: [string, Record<number, Partial<Segment>>, number[]][] = [
  ["distance", withColumn("distance_m", { 500: 900, 600: 2000, 700: null }), [600, 500, 700]],
  ["average_grade", withColumn("average_grade", { 500: 3.0, 600: null, 700: 7.0 }), [700, 500, 600]],
  ["people_on_strava", withColumn("athlete_count", { 500: null, 600: 5, 700: 50 }), [700, 600, 500]],
  ["total_attempts", withColumn("effort_count", { 500: 10, 600: null, 700: 99 }), [700, 500, 600]],
  ["steepest_grade", withColumn("maximum_grade", { 500: 9.0, 600: 12.0, 700: null }), [600, 500, 700]],
  ["elevation_gain", withColumn("elevation_gain_m", { 500: null, 600: 30, 700: 80 }), [700, 600, 500]],
  ["climb_category", withColumn("climb_category", { 500: 0, 600: 5, 700: null }), [600, 500, 700]],
  ["recent_best", withColumn("recent_best", { 500: 300, 600: null, 700: 90 }), [700, 500, 600]],
  ["wind_now", withWindNow({ 500: 4.0, 600: -6.0, 700: null }), [600, 500, 700]],
  ["best_hour", withWindNow({ 500: null, 600: 2.0, 700: -3.0 }), [700, 600, 500]],
];

describe("sorting", () => {
  test.each(NUMBER_FIELDS)("the first sort on %s puts the most useful end on top and blanks last", (field, changes, expected) => {
    expect(order(threeSegments(changes), first(field))).toEqual(expected);
  });

  test.each(NUMBER_FIELDS)("a second sort on %s reverses but keeps blanks last", (field, changes, expected) => {
    const twice = chooseSort(first(field), field);
    expect(order(threeSegments(changes), twice)).toEqual([expected[1], expected[0], expected[2]]);
  });

  test("your attempts sorts most first, then reverses", () => {
    const segments = threeSegments(withColumn("recent_efforts", { 500: 2, 600: 1, 700: 3 }));
    const most = chooseSort({ field: "distance", direction: "desc" }, "your_attempts");
    expect(most).toEqual({ field: "your_attempts", direction: "desc" });
    expect(order(segments, most)).toEqual([700, 500, 600]);
    expect(order(segments, chooseSort(most, "your_attempts"))).toEqual([600, 500, 700]);
  });

  test("ties stay in name order, ignoring capitals, either way round", () => {
    const segments = threeSegments({ 500: { name: "b hill" }, 600: { name: "A road" }, 700: { name: "c lane" } });
    const allTied = segments.map((segment) => ({ ...segment, distance_m: 100 }));
    expect(order(allTied, { field: "distance", direction: "desc" })).toEqual([600, 500, 700]);
    expect(order(allTied, { field: "distance", direction: "asc" })).toEqual([600, 500, 700]);
  });

  test("name and town can't be sorted, and fastest time only while its setting is on", () => {
    expect(canSort("name", false)).toBe(false);
    expect(canSort("town", false)).toBe(false);
    expect(canSort("nonsense", false)).toBe(false);
    expect(canSort("distance", false)).toBe(true);
    expect(canSort("fastest_time", false)).toBe(false);
    expect(canSort("fastest_time", true)).toBe(true);
  });

  test("fastest time sorts fastest first with blanks last, then reverses", () => {
    const segments = threeSegments(withColumn("fastest_time_s", { 500: 3725, 600: null, 700: 59 }));
    const fastest = first("fastest_time");
    expect(order(segments, fastest, {}, null, true)).toEqual([700, 500, 600]);
    expect(order(segments, chooseSort(fastest, "fastest_time"), {}, null, true)).toEqual([500, 700, 600]);
  });

  test("sortRows doesn't change the rows it's given", () => {
    const rows = threeSegments().map((segment) => toRow(segment, THIS_HOUR, null));
    const before = rows.map((row) => row.id);
    sortRows(rows, { field: "distance", direction: "desc" });
    expect(rows.map((row) => row.id)).toEqual(before);
  });
});

describe("wind now and best hour", () => {
  const FIVE_HOURS = [4.0, 5.0, -10.0, 3.0, -2.0];
  const climb = threeSegments({ 500: { wind_hours: windHours(FIVE_HOURS) } })[0];

  test("wind now is this hour's, and best hour the most helpful of all the hours", () => {
    const row = toRow(climb, THIS_HOUR, null);
    expect(row.wind_now?.percent).toBe(4.0);
    expect(row.wind_best).toMatchObject({ percent: -10.0, hour: hour(2) });
  });

  test("best hour comes only from hours inside the time range", () => {
    expect(toRow(climb, THIS_HOUR, [hour(3), hour(4)]).wind_best).toMatchObject({ percent: -2.0, hour: hour(4) });
    expect(toRow(climb, THIS_HOUR, [hour(0), hour(1)]).wind_best).toMatchObject({ percent: 4.0, hour: hour(0) });
  });

  test("wind now ignores the time range", () => {
    expect(toRow(climb, THIS_HOUR, [hour(3), hour(4)]).wind_now?.percent).toBe(4.0);
  });

  test("a range with no hours with numbers leaves best hour blank", () => {
    expect(toRow(climb, THIS_HOUR, [hour(20), hour(30)]).wind_best).toBeNull();
  });

  test("the earliest of equal hours is best", () => {
    const tied = { ...climb, wind_hours: windHours([-3, 1, -3]) };
    expect(toRow(tied, THIS_HOUR, null).wind_best?.hour).toBe(hour(0));
  });

  test("a segment with no stored hours has no wind now or best hour", () => {
    const row = toRow(threeSegments()[1], THIS_HOUR, null);
    expect(row.wind_now).toBeNull();
    expect(row.wind_best).toBeNull();
  });

  test("sorting and filtering by best hour follow the range", () => {
    const segments = threeSegments({
      500: { wind_hours: windHours([-1, -9]) },
      600: { wind_hours: windHours([-5, 0]) },
      700: { wind_hours: windHours([-3, -3]) },
    });
    const best = first("best_hour");
    expect(order(segments, best)).toEqual([500, 600, 700]);
    expect(order(segments, best, {}, [hour(0), hour(0)])).toEqual([600, 700, 500]);
    expect(order(segments, best, { best_hour: { max: -2 } }, [hour(0), hour(0)])).toEqual([600, 700]);
  });
});
