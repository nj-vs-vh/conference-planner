import argparse
import datetime
import itertools
import json
import os
import random
import sys
from collections import defaultdict
from dataclasses import dataclass
from hashlib import md5
from pathlib import Path
from typing import cast

from icalendar import Calendar, Event
from pytz import UTC, timezone
from trueskill import Rating, quality_1vs1, rate_1vs1

TZ = UTC


def start_time(e: Event) -> datetime.datetime:
    dt = e.get("DTSTART").dt
    return dt.astimezone(TZ)


def end_time(e: Event) -> datetime.datetime:
    dt = e.get("DTEND").dt
    return dt.astimezone(TZ)


def format_talk(e: Event) -> str:
    return f"{start_time(talk)} - {end_time(talk).time()}: [{talk.get('LOCATION')}] {talk.get('SUMMARY')}"


class RatingStore:
    def __init__(self) -> None:
        self._file = Path("comparisons.json")
        if not self._file.exists():
            self._file.write_text(json.dumps(dict()))

    def _key(self, talk: Event) -> str:
        title: str = talk.get("SUMMARY", "")
        title_stem = "-".join(title.lower().split()[:6])
        hash = md5()
        hash.update(talk.get("SUMMARY", "").encode("utf-8"))
        hash.update(talk.get("DESCRIPTION", "").encode("utf-8"))
        return title_stem + "-" + hash.hexdigest()

    def _load_data(self) -> dict[str, dict[str, float]]:
        return json.loads(self._file.read_text())

    def get_rating(self, talk: Event) -> Rating:
        return self.get_ratings([talk])[0]

    def get_ratings(self, talks: list[Event]) -> list[Rating]:
        data = self._load_data()
        keys = [self._key(t) for t in talks]
        return [(Rating(mu=data[key]["mu"], sigma=data[key]["sigma"]) if key in data else Rating()) for key in keys]

    def record_better_than(self, better: Event, worse: Event) -> None:
        r1 = self.get_rating(better)
        r2 = self.get_rating(worse)
        new_r1, new_r2 = rate_1vs1(r1, r2)
        data = self._load_data()
        data[self._key(better)] = {"mu": new_r1.mu, "sigma": new_r1.sigma}
        data[self._key(worse)] = {"mu": new_r2.mu, "sigma": new_r2.sigma}
        self._file.write_text(json.dumps(data, indent=2))


def bold(t: str) -> str:
    return f"\033[1m{t}\033[0m"


def format_column(text: str, colwidth: int) -> list[str]:
    res: list[str] = []
    for paragraph in text.splitlines():
        if not paragraph:
            continue
        current_line = ""
        for word in paragraph.split():
            if len(current_line) + 1 + len(word) > colwidth:
                res.append(current_line.ljust(colwidth))
                current_line = ""
            if current_line:
                current_line += " "
            current_line += word
        if current_line:
            res.append(current_line.ljust(colwidth))
    # TODO: bold title
    # res[0] = bold(res[0])
    return res


def rank(talks: dict[str, list[Event]]) -> str:
    rating_store = RatingStore()

    while True:
        room_ratings = [
            (
                room,
                sum((float(r.mu) for r in rating_store.get_ratings(talks_)), start=0.0) / len(talks_),
            )
            for room, talks_ in talks.items()
        ]
        room_ratings.sort(key=lambda pair: pair[1], reverse=True)
        print("Current room ratings:")
        for i, (room, rating) in enumerate(room_ratings):
            print(f"{i + 1: >2}. {room} ({rating:.1f})")

        rooms = [room for room, _ in room_ratings]
        ratings = [rating for _, rating in room_ratings]

        for _ in range(100):
            compare_rooms = list(set(random.choices(rooms, weights=[r**2 for r in ratings], k=2)))
            if len(compare_rooms) == 2:
                break
        else:
            raise RuntimeError("Failed to pick rooms")

        room1, room2 = compare_rooms
        talks1 = talks[room1].copy()
        talks2 = talks[room2].copy()
        random.shuffle(talks1)
        random.shuffle(talks2)
        for talk1, talk2 in itertools.product(talks1, talks2):
            r1 = rating_store.get_rating(talk1)
            r2 = rating_store.get_rating(talk1)
            draw_prob = quality_1vs1(r1, r2)
            if draw_prob < 0.3:
                continue
            print(
                f"Preference: {'>' if r1.mu > r2.mu else ('=' if r1.mu == r2.mu else '<')}; draw probability: {draw_prob:.1%}"
            )

            term_size = os.get_terminal_size().columns
            column_width = term_size // 2 - 10
            left_lines = format_column(talk1.get("SUMMARY") + "\n" + talk1.get("DESCRIPTION"), column_width)
            right_lines = format_column(talk2.get("SUMMARY") + "\n" + talk2.get("DESCRIPTION"), column_width)
            print(("=" * column_width) + " | " + ("=" * column_width))
            filler = " " * column_width
            for left, right in itertools.zip_longest(left_lines, right_lines, fillvalue=None):
                print((left or filler) + " | " + (right or filler))
            while True:
                ctrl = input("[<>sq] ")
                if ctrl == "q":
                    return rooms[0]
                elif ctrl == "<":
                    rating_store.record_better_than(talk1, talk2)
                    break
                elif ctrl == ">":
                    rating_store.record_better_than(talk2, talk1)
                    break
                elif ctrl == "s":
                    print("Skipping")
                    break
                else:
                    print("Unexpected input!")

            break


def export_to_markdown(talks: list[Event], section_title: str, filename: str) -> None:
    talks = sorted(talks, key=start_time)
    paragraphs: list[str] = []

    for talk in talks:
        descr_lines = [line for line in talk.get("DESCRIPTION", "").splitlines() if line.strip()]
        quote_descr = ["> " + line for line in descr_lines if not line.startswith("http")]
        try:
            indico_link = [f"[indico]({line})" for line in descr_lines if "indico.cern.ch" in line][0]
        except Exception:
            indico_link = f"[indico]({talk.get('URL')})"
        paragraphs.append(f"### {talk.get('SUMMARY')}\n{indico_link}\n{'\n'.join(quote_descr)}\n\n- ")
    result = f"## {section_title}\n\n" + "\n".join(paragraphs)
    Path(filename).write_text(result)


Interval = tuple[datetime.datetime, datetime.datetime]


def _merge_intervals(
    a: Interval,
    b: Interval,
    tolerance: datetime.timedelta = datetime.timedelta(minutes=5),
) -> Interval | None:
    if a[1] + tolerance < b[0] or b[1] + tolerance < a[0]:
        return None
    else:
        return (min(a[0], b[0]), max(a[1], b[1]))


@dataclass
class Session:
    talks: list[Event]
    start: datetime.datetime
    end: datetime.datetime

    def __post_init__(self) -> None:
        talks_by_room: dict[str, list[Event]] = defaultdict(list)

        for talk in talks:
            if talk.get("SUMMARY") == ".":
                continue
            room: str = talk.get("LOCATION", "").strip()
            room = room.replace("Rooom", "Room")
            talks_by_room[room].append(talk)

        self.talks_by_room = talks_by_room

    def is_plenary(self) -> bool:
        return len(self.talks_by_room) == 1

    @staticmethod
    def detect(talks: list[Event]) -> "list[Session]":
        intervals: list[Interval] = []
        for t in talks:
            interval = (start_time(t), end_time(t))
            for i in range(len(intervals)):
                if merged := _merge_intervals(interval, intervals[i]):
                    intervals[i] = merged
                    break
            else:
                intervals.append(interval)
        intervals.sort(key=lambda interval: interval[0])

        res: list[Session] = []
        for start, end in intervals:
            res.append(
                Session(
                    talks=[t for t in talks if start <= start_time(t) <= end],
                    start=start,
                    end=end,
                )
            )
        return res


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ics_file", help="Conference schedule ICS file")
    parser.add_argument("--date", required=True, help="date in ISO format")
    parser.add_argument("--session", required=False, help="1-based # of the parallel session to analyze")
    parser.add_argument("--force", required=False, help="Force selection of a given room")
    parser.add_argument("--verbose", action="store_true", help="")
    parser.add_argument("--tz", help="Event timezone")
    args = parser.parse_args()

    date = datetime.date.fromisoformat(args.date)
    if args.tz is not None:
        TZ = timezone(args.tz)

    cal = cast(Calendar, Calendar.from_ical(Path(args.ics_file).read_text()))

    talks = list(cal.events)
    talks.sort(key=start_time)
    talks = [t for t in talks if start_time(t).date() == date]

    if not talks:
        print(f"No talks on {date}")
        sys.exit(1)

    if args.verbose:
        print(f"All events on {date}:")
        for talk in talks:
            print(format_talk(talk))

    sessions = Session.detect(talks)
    if args.verbose:
        for i, s in enumerate(sessions):
            print(
                f"Session #{i + 1}: {s.start.time()} - {s.end.time()}, "
                + f"{len(s.talks)} talks in {len(s.talks_by_room)} room(s)"
            )

    if args.session is None:
        print("No session specified, exporting plenary sessions")
        for idx, s in enumerate(sessions):
            if s.is_plenary():
                export_to_markdown(
                    next(iter(s.talks_by_room.values())),
                    section_title=f"Session #{idx + 1} (plenary)",
                    filename=f"{date}-session-{idx + 1}-plenary.md",
                )
        sys.exit(0)

    session_idx = int(args.session) - 1
    session = sessions[session_idx]
    if args.verbose:
        print("Session talks:")
        for talk in session.talks:
            print(format_talk(talk))

    if len(session.talks_by_room) == 1:
        print("This is a plenary session, exporting")
        export_to_markdown(
            next(iter(session.talks_by_room.values())),
            section_title=f"Session #{session_idx + 1} (plenary)",
            filename=f"{date}-session-{session_idx + 1}-plenary.md",
        )
        sys.exit()

    print("Talks by room:")
    for r, talks in session.talks_by_room.items():
        print(f"  {r}: {len(talks)}")

    if args.force is not None:
        winner_room = args.force
    else:
        winner_room = rank(session.talks_by_room)

    export_to_markdown(
        session.talks_by_room[winner_room],
        section_title=f"Session #{session_idx + 1} ({winner_room})",
        filename=f"{date}-session-{session_idx + 1}-{'-'.join(winner_room.split())}.md",
    )
