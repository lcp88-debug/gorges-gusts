# Gorges Gusts

A free web app for a small group of friends who log in with Strava and see lists of cycling segments.

## Language

**Rider**:
A person who has connected their Strava account to the app.
_Avoid_: User, athlete

**Attempt**:
One time a rider rode a segment, as part of a ride.
_Avoid_: Effort

**Segment details**:
The facts about a segment that aren't about any one rider, such as its name, distance, grade and how many people have ridden it on Strava. They can be reused for any rider.
_Avoid_: Segment data, segment stats

**Contributor**:
A rider whose attempted segments can appear on other riders' Most Popular lists. Every rider is a contributor until they opt out.
_Avoid_: Sharer, participant

**Most Attempted**:
A list of a rider's own segments from their last 90 days of rides. It can be ranked by how many times they attempted each one, or by wind effect.

**Most Popular**:
A list of a rider's own segments and those attempted by recently active contributors. It can be ranked by how many people have ridden each one on Strava, or by wind effect.
_Avoid_: Wider list, global list

**Wind effect**:
The percentage change in a rider's time on a segment at the same effort, compared with no wind. Negative means the wind makes them faster.
_Avoid_: Wind rating, wind score, watts saved

**Rider profile**:
The weight and best 20-minute power a rider enters, used to work out their wind effect. It comes from the rider, not from Strava.
_Avoid_: Settings, athlete data

**Best hour**:
The hour in the next 48 with a segment's lowest wind effect, including night hours.
_Avoid_: Best window, best time

**Less certain**:
A mark on a wind effect from steep terrain or a steep descent, where the estimate is more likely to be well off.
_Avoid_: Low confidence

**Region**:
The area around Tompkins County, NY, that the app covers. A segment with any part of its route outside it doesn't appear on any list.
_Avoid_: Service area, coverage
