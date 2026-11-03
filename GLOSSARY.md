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
A list of a rider's own segments from their last 90 days of rides, ranked by how many times they attempted each one.

**Most Popular**:
A list of a rider's own segments and those attempted by recently active contributors, ranked together by how many people have ridden each one on Strava.
_Avoid_: Wider list, global list
