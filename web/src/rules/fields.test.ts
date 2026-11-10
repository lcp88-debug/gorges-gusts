import { activeFields, show } from "./fields";
import { formatTime, parseTime } from "./times";

describe("how values are shown", () => {
  test("distance, grades, elevation and counts in the list's units", () => {
    expect(show.miles(3218.68)).toBe("2.0 mi");
    expect(show.percent(5.25)).toBe("5.3%");
    expect(show.percent(11.04)).toBe("11.0%");
    expect(show.feet(152.4)).toBe("500 ft");
    expect(show.feet(1000)).toBe("3,281 ft");
    expect(show.count(3456)).toBe("3,456");
  });

  test("blanks are a dash", () => {
    for (const shown of [show.miles(null), show.percent(null), show.feet(null), show.count(null),
      show.duration(null), show.climbCategory(null), show.blank(null), show.blank("")]) {
      expect(shown).toBe("–");
    }
  });

  test("climb category is shown as Strava names it", () => {
    expect([0, 1, 4, 5, 9].map(show.climbCategory)).toEqual(["none", "4", "1", "HC", "–"]);
  });

  test("times are m:ss, or h:mm:ss for an hour or more", () => {
    expect(show.duration(49)).toBe("0:49");
    expect(show.duration(125)).toBe("2:05");
    expect(show.duration(3723)).toBe("1:02:03");
  });

  test("wind effects carry a sign, with a true minus", () => {
    expect(show.wind(3.4)).toBe("+3%");
    expect(show.wind(-8.6)).toBe("−9%");
    expect(show.wind(0.3)).toBe("0%");
    expect(show.wind(-0.3)).toBe("0%");
  });

  test("wind speed and direction", () => {
    expect(show.mph(4.4704)).toBe("10 mph");
    expect(show.compass(225)).toBe("SW");
    expect(show.compass(355)).toBe("N");
  });

  test("hours are short day names and 12-hour clock times", () => {
    expect(show.hour("2026-11-07T14:00:00")).toBe("Sat 2pm");
    expect(show.clock("2026-11-07T00:00:00")).toBe("12am");
  });
});

describe("times", () => {
  test("are read as m:ss or h:mm:ss", () => {
    expect(parseTime("1:30")).toBe(90);
    expect(parseTime("1:00:00")).toBe(3600);
    for (const bad of ["90", "1:75", "1:60:00", "a:bc", "1:2:3:4", "-1:30", ""]) expect(parseTime(bad)).toBeNull();
  });

  test("format back as typed", () => {
    expect(formatTime(parseTime("2:05")!)).toBe("2:05");
  });
});

test("fastest time is only a field while its setting is on", () => {
  expect("fastest_time" in activeFields(false)).toBe(false);
  expect("fastest_time" in activeFields(true)).toBe(true);
});
