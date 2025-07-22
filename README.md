A script to plan attendance of parallel conference sessions based
on pairwise preference comparison inducing a probabilistic ranking.
Proof-of-concept version for ICRC2025.

Outline:
- parse .ics file with conference talks
- extract parallel session (ad hoc)
- obtain from user a series of choices (preferences) between talks in parallel sessions,
  updating session "ratings" with TrueSkill algorithm
- finally, select the highest-ranking session and export its timetable to Markdown
