# Gorges Gusts

A free web app for a small group of friends who log in with Strava and see lists of cycling segments.

## Language

**Rider**:
A person who has connected their Strava account to the app.
_Avoid_: User, athlete

**Attempt**:
One time a rider rode a segment, as part of a ride.
_Avoid_: Effort

**Recent best**:
A rider's fastest attempt on a segment in their last 90 days of rides.
_Avoid_: PR, personal record, best effort

**Fastest time**:
The fastest time anyone has recorded on a segment on Strava, shown without who set it.
_Avoid_: KOM, QOM, course record

**Segment details**:
The facts about a segment that aren't about any one rider, such as its name, distance, grade and how many people have ridden it on Strava. They can be reused for any rider.
_Avoid_: Segment data, segment stats

**Contributor**:
A rider whose attempted segments can appear on other riders' Explore lists. Every rider is a contributor until they opt out.
_Avoid_: Sharer, participant

**My Segments**:
A list of every segment a rider attempted in their last 90 days of rides.
_Avoid_: Most Attempted

**Explore**:
A list of every segment in My Segments, plus every segment attempted by recently active contributors.
_Avoid_: Most Popular, wider list, global list

**Wind effect**:
The percentage change in a rider's time on a segment at the same effort, compared with no wind. Negative means the wind makes them faster.
_Avoid_: Wind rating, wind score, watts saved

**Rider profile**:
The weight and best 20-minute power a rider enters, used to work out their wind effect. It comes from the rider, not from Strava.
_Avoid_: Settings, athlete data

**Time range**:
The stretch of the next 48 hours a rider chooses to look at, such as Saturday 9am to 1pm. The 48 hours are as far ahead as the forecast goes.
_Avoid_: Window, ride window

**Best hour**:
The hour in the time range with a segment's lowest wind effect, including night hours.
_Avoid_: Best window, best time

**Less certain**:
A mark on a wind effect from steep terrain or a steep descent, where the estimate is more likely to be well off.
_Avoid_: Low confidence

**Region**:
The area around Tompkins County, NY, that the app covers. A segment with any part of its route outside it doesn't appear on any list.
_Avoid_: Service area, coverage
