# confplan

A script to choose parallel conference sessions to attend based
on pairwise talk preference inducing an implicit ranking.

How to use:
```bash
python confplan.py myconf.ics --date 2026-09-03 --session 4
```

- feed an .ics file with conference talks to the script
- choose session to plan
- make a series of choices preferring one talk over another; the TrueSkill algorithm
  is used to update a probabilistic preference score for every parallel session,
  computed as an average preference of the talks in it
- after some number of choices the preferences converge and it's safe to select
  the highest-ranking session to attend
- the script then exports the chosen session's talks to a Markdown doc
