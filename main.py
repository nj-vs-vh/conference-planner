import argparse
from collections import defaultdict
import datetime
from hashlib import md5
import itertools
import json
import os
import random
from typing import cast
from icalendar import Calendar, Event
from pathlib import Path

from trueskill import Rating, quality_1vs1, rate_1vs1


def start_time(e: Event) -> datetime.datetime:
    return e.get("DTSTART").dt


def end_time(e: Event) -> datetime.datetime:
    return e.get("DTEND").dt


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
        return [
            (
                Rating(mu=data[key]["mu"], sigma=data[key]["sigma"])
                if key in data
                else Rating()
            )
            for key in keys
        ]

    def record_better_than(self, better: Event, worse: Event) -> None:
        r1 = self.get_rating(better)
        r2 = self.get_rating(worse)
        new_r1, new_r2 = rate_1vs1(r1, r2)
        data = self._load_data()
        data[self._key(better)] = {"mu": new_r1.mu, "sigma": new_r1.sigma}
        data[self._key(worse)] = {"mu": new_r2.mu, "sigma": new_r2.sigma}
        self._file.write_text(json.dumps(data, indent=2))


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
    return res


def rank(talks: dict[str, list[Event]]) -> str:
    rating_store = RatingStore()

    while True:
        room_ratings = [
            (
                room,
                sum((float(r.mu) for r in rating_store.get_ratings(talks_)), start=0.0)
                / len(talks_),
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
            compare_rooms = list(set(random.choices(rooms, weights=ratings, k=2)))
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
            left_lines = format_column(
                talk1.get("SUMMARY") + "\n" + talk1.get("DESCRIPTION"), column_width
            )
            right_lines = format_column(
                talk2.get("SUMMARY") + "\n" + talk2.get("DESCRIPTION"), column_width
            )
            print(("=" * column_width) + " | " + ("=" * column_width))
            filler = " " * column_width
            for left, right in itertools.zip_longest(
                left_lines, right_lines, fillvalue=None
            ):
                print((left or filler) + " | " + (right or filler))
            ctrl = input("[<>sq] ")
            if ctrl == "q":
                return rooms[0]
            elif ctrl == "<":
                rating_store.record_better_than(talk1, talk2)
            elif ctrl == ">":
                rating_store.record_better_than(talk2, talk1)
            elif ctrl == "s":
                print("Skipping")
            else:
                print("Unexpected input, ignoring!")
            break


COFFEE_BREAKS = [
    datetime.time(hour=11),
    datetime.time(hour=13),
    datetime.time(hour=15),
    datetime.time(hour=19),
]


def split_by_coffee_breaks(talks: list[Event]):
    talks = talks.copy()
    sessions: list[list[Event]] = []
    for cb1, cb2 in itertools.pairwise(COFFEE_BREAKS):
        session_talks = [t for t in talks if cb1 < start_time(t).time() < cb2]
        sessions.append(session_talks)
    return sessions


def export_to_markdown(talks: list[Event], section_title: str, filename: str) -> None:
    talks = sorted(talks, key=start_time)
    paragraphs: list[str] = []

    for talk in talks:
        descr_lines = [
            line for line in talk.get("DESCRIPTION", "").splitlines() if line.strip()
        ]
        quote_descr = [
            "> " + line for line in descr_lines if not line.startswith("http")
        ]
        indico_link = [
            f"[indico]({line})" for line in descr_lines if "indico.cern.ch" in line
        ][0]
        paragraphs.append(
            f"### {talk.get('SUMMARY')}\n{indico_link}\n{'\n'.join(quote_descr)}\n\n- "
        )
    result = f"## {section_title}\n\n" + "\n".join(paragraphs)
    Path(filename).write_text(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="date in ISO format")
    parser.add_argument("--session", required=True, help="1-based number of session")
    parser.add_argument(
        "--force", required=False, help="Force selection of a given room"
    )
    args = parser.parse_args()
    date = datetime.date.fromisoformat(args.date)
    session_idx = int(args.session) - 1

    cal = cast(Calendar, Calendar.from_ical(Path("icrc2025.ics").read_text()))
    talks_by_room: dict[str, list[Event]] = defaultdict(list)

    plenary_talks: list[Event] = []
    for talk in cal.events:
        if start_time(talk).date() != date:
            continue
        if talk.get("SUMMARY") == ".":
            continue
        room: str = talk.get("LOCATION", "").strip()
        room = room.replace("Rooom", "Room")
        if room.startswith("Plenary") or room.startswith("CICG"):
            plenary_talks.append(talk)
            continue
        talks_by_room[room].append(talk)
    plenary_talks.sort(key=start_time)
    export_to_markdown(
        plenary_talks,
        section_title="Plenary talks",
        filename=f"{date}-plenary.md",
    )

    # for room, talks in talks_by_room.items():
    #     print(room)
    #     for talk in sorted(talks, key=start_time):
    #         print(start_time(talk), talk.get("SUMMARY"))
    # print()

    talks_by_room = {
        room: sorted(talks, key=start_time) for room, talks in talks_by_room.items()
    }

    all_session_rooms: list[dict[str, list[Event]]] = [
        {} for _ in range(len(COFFEE_BREAKS) - 1)
    ]
    for room, talks in sorted(talks_by_room.items()):
        sessions = split_by_coffee_breaks(talks)
        assert len(sessions) == len(all_session_rooms), (
            f"Length mismatch {len(sessions)} vs {len(all_session_rooms)}"
        )
        for session_rooms, session in zip(all_session_rooms, sessions):
            if session:
                session_rooms[room] = session

    session_rooms = all_session_rooms[session_idx]
    for session in all_session_rooms:
        print()
        print(f"Session of {len(session)} rooms")
        for room, talks in session.items():
            print(room)
            for talk in talks:
                print(talk.get("SUMMARY"))

    start = min(
        start_time(t) for t in itertools.chain.from_iterable(session_rooms.values())
    )
    end = max(
        end_time(t) for t in itertools.chain.from_iterable(session_rooms.values())
    )

    print("\n\n")
    print(f"{start} - {end}")
    for room in session_rooms.keys():
        print()
        print(room)
        for talk in session_rooms[room]:
            print(start_time(talk))
            print(talk.get("SUMMARY"))

    if args.force is not None:
        winner_room = args.force
    else:
        winner_room = rank(session_rooms)
    export_to_markdown(
        session_rooms[winner_room],
        section_title=f"Session #{session_idx + 1} ({winner_room})",
        filename=f"{date}-session-{session_idx + 1}-{'-'.join(winner_room.split())}.md",
    )
