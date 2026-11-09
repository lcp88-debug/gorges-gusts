import { FIELDS } from "./fields";
import {
  FilterError,
  filterCount,
  numberBoxes,
  parseNumberFilter,
  tickChoices,
  toggleTick,
  withFilter,
} from "./filters";
import { buildList, toRow } from "./lists";
import { THIS_HOUR, threeSegments, withColumn, withWindNow } from "./testing";
import type { Filters, Segment } from "./types";

const BY_ATTEMPTS = { field: "your_attempts", direction: "desc" as const };

function kept(segments: Segment[], filters: Filters, fastestTime = false) {
  return buildList(segments, THIS_HOUR, { sort: BY_ATTEMPTS, filters, timeRange: null }, fastestTime).rows.map((row) => row.id);
}

/** The filter typed into a number field's boxes, as the Filter panel reads it. */
const typed = (key: string, min = "", max = "") => parseNumberFilter(key, min, max)!;

describe("number filters", () => {
  const miles = threeSegments(withColumn("distance_m", { 500: 1609.34, 600: 3218.68, 700: 800 }));

  test("a minimum keeps only segments at or above it", () => {
    expect(kept(miles, { distance: typed("distance", "1") })).toEqual([500, 600]);
  });

  test("a minimum and a maximum together make a range", () => {
    expect(kept(miles, { distance: typed("distance", "0.9", "1.5") })).toEqual([500]);
  });

  test("filters on several fields all apply", () => {
    const segments = threeSegments({
      500: { distance_m: 1609.34, average_grade: 5.0 },
      600: { distance_m: 3218.68, average_grade: 1.0 },
      700: { distance_m: 3218.68, average_grade: 6.0 },
    });
    expect(kept(segments, { distance: typed("distance", "1"), average_grade: typed("average_grade", "4") })).toEqual([500, 700]);
  });

  test("a blank value drops out only when its field is filtered", () => {
    const segments = threeSegments(withColumn("average_grade", { 500: 5.0, 600: null, 700: 6.0 }));
    expect(kept(segments, {})).toEqual([500, 600, 700]);
    expect(kept(segments, { average_grade: typed("average_grade", "", "10") })).toEqual([500, 700]);
  });

  test("entries use the units the list shows", () => {
    const feet = threeSegments(withColumn("elevation_gain_m", { 500: 100, 600: 200, 700: 300 })); // 328, 656, 984 ft
    expect(kept(feet, { elevation_gain: typed("elevation_gain", "600", "700") })).toEqual([600]);

    const wind = threeSegments(withWindNow({ 500: 4.0, 600: -6.0, 700: -2.0 }));
    expect(kept(wind, { wind_now: typed("wind_now", "", "-1") })).toEqual([600, 700]);
    expect(kept(wind, { wind_now: typed("wind_now", "", "−1") })).toEqual([600, 700]); // As the list writes it.
  });

  test("times are typed as minutes and seconds", () => {
    const times = threeSegments(withColumn("recent_best", { 500: 150, 600: 61, 700: 3700 }));
    expect(kept(times, { recent_best: typed("recent_best", "1:30", "10:00") })).toEqual([500]);
    expect(kept(times, { recent_best: typed("recent_best", "1:00:00") })).toEqual([700]);
  });

  test("fastest time filters in minutes and seconds", () => {
    const times = threeSegments(withColumn("fastest_time_s", { 500: 3725, 600: null, 700: 59 }));
    expect(kept(times, { fastest_time: typed("fastest_time", "", "2:00") }, true)).toEqual([700]);
  });

  test("filters compare values as the list shows them", () => {
    // Shown as 1.0 mi, 0.9 mi and 1.1 mi.
    const miles = threeSegments(withColumn("distance_m", { 500: 1580.0, 600: 1450.0, 700: 1770.0 }));
    expect(kept(miles, { distance: typed("distance", "1", "1") })).toEqual([500]);

    const wind = threeSegments(withWindNow({ 500: -2.6, 600: -3.4, 700: -2.4 })); // Shown as −3%, −3% and −2%.
    expect(kept(wind, { wind_now: typed("wind_now", "", "-3") })).toEqual([500, 600]);
  });

  test.each([
    [["distance", "abc", ""], "Distance: “abc” isn't a number."],
    [["distance", "3", "1"], "Distance: the minimum is above the maximum."],
    [["distance", "nan", ""], "Distance: “nan” isn't a number."],
    [["recent_best", "90", ""], "Recent best: “90” isn't a time like 1:30."],
    [["recent_best", "1:75", ""], "Recent best: “1:75” isn't a time like 1:30."],
    [["average_grade", "1e999", ""], "Avg grade: “1e999” isn't a usable number."],
    [["distance", "0x10", ""], "Distance: “0x10” isn't a number."],
  ] as [[string, string, string], string][])("%j is refused with a message", ([key, min, max], message) => {
    expect(() => parseNumberFilter(key, min, max)).toThrow(FilterError);
    expect(() => parseNumberFilter(key, min, max)).toThrow(message);
  });

  test("empty boxes mean the field isn't filtered", () => {
    expect(parseNumberFilter("distance", " ", "")).toBeNull();
  });

  test("boxes show what is saved in the units typed", () => {
    expect(numberBoxes(typed("distance", "1.5"), FIELDS.distance)).toEqual({ min: "1.5", max: "" });
    expect(numberBoxes(typed("recent_best", "", "2:05"), FIELDS.recent_best)).toEqual({ min: "", max: "2:05" });
    expect(numberBoxes(undefined, FIELDS.distance)).toEqual({ min: "", max: "" });
  });
});

describe("tick and search filters", () => {
  test("ticked climb categories keep only those segments, and none is a real category", () => {
    const segments = threeSegments(withColumn("climb_category", { 500: 5, 600: 0, 700: null }));
    expect(kept(segments, { climb_category: [5] })).toEqual([500]);
    expect(kept(segments, { climb_category: [0] })).toEqual([600]);
  });

  test("climb tick boxes run none, then 4 to HC", () => {
    expect(tickChoices("climb_category", []).map(([, label]) => label)).toEqual(["none", "4", "3", "2", "1", "HC"]);
  });

  test("town tick boxes list only towns on the list, ignoring capitals for the order", () => {
    const rows = threeSegments(withColumn("town", { 500: "ithaca", 600: "Dryden", 700: null }))
      .map((segment) => toRow(segment, THIS_HOUR, null));
    expect(tickChoices("town", rows)).toEqual([["Dryden", "Dryden"], ["ithaca", "ithaca"]]);
  });

  const towns = threeSegments(withColumn("town", { 500: "Ithaca", 600: "Dryden", 700: null }));

  test("ticking towns keeps only those towns and drops blanks", () => {
    expect(kept(towns, { town: ["Ithaca", "Dryden"] })).toEqual([500, 600]);
  });

  test("a ticked town no longer on the list is neither shown nor applied", () => {
    const built = buildList(towns, THIS_HOUR, { sort: BY_ATTEMPTS, filters: { town: ["Elmira"] }, timeRange: null }, false);
    expect(built.rows.map((row) => row.id)).toEqual([500, 600, 700]);
    expect(built.filters).toEqual({});
  });

  test("ticked towns still on the list keep applying without the missing one", () => {
    const built = buildList(towns, THIS_HOUR, { sort: BY_ATTEMPTS, filters: { town: ["Elmira", "Ithaca"] }, timeRange: null }, false);
    expect(built.rows.map((row) => row.id)).toEqual([500]);
    expect(built.filters).toEqual({ town: ["Ithaca"] });
  });

  test("unticking a box keeps a saved town that has no box", () => {
    let filters: Filters = { town: ["Elmira", "Ithaca"] };
    filters = toggleTick(filters, "town", "Ithaca", false);
    filters = toggleTick(filters, "town", "Dryden", true);
    expect(filters).toEqual({ town: ["Dryden", "Elmira"] });
  });

  test("unticking the last box leaves the field unfiltered", () => {
    expect(toggleTick({ climb_category: [5] }, "climb_category", 5, false)).toEqual({});
  });

  test("name search matches any part, ignoring capitals", () => {
    expect(kept(towns, { name: "ROAD" })).toEqual([600]);
    expect(kept(towns, { name: "o" })).toEqual([500, 600]);
  });

  test("these filters combine with number filters", () => {
    expect(kept(towns, { name: "o", distance: typed("distance", "1.5") })).toEqual([500]);
  });

  test("filters on fields the list doesn't have are ignored", () => {
    const times = threeSegments(withColumn("fastest_time_s", { 500: 3725, 600: null, 700: 59 }));
    expect(kept(times, { fastest_time: { max: 120 } }, false)).toEqual([500, 600, 700]);
  });
});

describe("changing filters", () => {
  test("withFilter sets or removes one field", () => {
    expect(withFilter({ name: "road" }, "distance", { min: 1 })).toEqual({ name: "road", distance: { min: 1 } });
    expect(withFilter({ name: "road" }, "name", null)).toEqual({});
  });

  test("the Filter button counts filtered fields", () => {
    expect(filterCount({ name: "road", town: ["Ithaca", "Dryden"] })).toBe(2);
  });
});
