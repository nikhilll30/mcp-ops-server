"""Generates the salon's tasks.json and negative.json. Edit this file, not the JSON, then run:

    python -m salon_ops.evals.golden.make_tasks

Every opening a task depends on is picked from the seed here, with a check that it still
means what the task says (free, on a day the stylist works, and so on), so a seed change
fails loudly instead of silently changing a task. tests/salon/test_salon_evals.py fails if
the committed JSON drifts from this script.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from salon_ops import seed

OUT = Path(__file__).parent


def build() -> tuple[list[dict], list[dict]]:
    d = seed.generate()
    booked = {v.opening_id for v in d.visits}
    stylist = {s.name: s for s in d.stylists}
    upcoming = {c: sum(v.customer_id == c for v in d.visits) for c in d.customer_ids}

    def free(name: str, day: str | None = None, after: datetime = seed.SALON_NOW) -> list:
        sid = stylist[name].id
        return sorted(
            (
                o
                for o in d.openings
                if o.stylist_id == sid
                and o.id not in booked
                and o.start > after
                and (day is None or o.start.date().isoformat() == day)
            ),
            key=lambda o: o.start,
        )

    def first(name: str, day: str | None = None) -> str:
        found = free(name, day)
        if not found:
            raise ValueError(f"{name} has no free opening on {day}; pick another day")
        return found[0].id

    def works(name: str, day: str) -> bool:
        sid = stylist[name].id
        return any(o.stylist_id == sid and o.start.date().isoformat() == day for o in d.openings)

    def workday(name: str, after: str) -> str:
        """The first day after ``after`` that ``name`` works and has a free opening."""
        day = date.fromisoformat(after)
        for _ in range(14):
            day += timedelta(days=1)
            if works(name, day.isoformat()) and free(name, day.isoformat()):
                return day.isoformat()
        raise ValueError(f"{name} has no working day after {after}")

    for c in ("CUS-0005", "CUS-0008", "CUS-0011", "CUS-0015", "CUS-0024", "CUS-0027"):
        if upcoming[c]:
            raise ValueError(f"{c} must have no seeded visits")
    if upcoming["CUS-0006"] != seed.MAX_UPCOMING_VISITS:
        raise ValueError("x03 needs CUS-0006 at the visit limit")

    # Days each task uses: the first one the stylist works on or after the date named.
    agate_day = workday("Agate", "2026-11-03")
    coral_day = workday("Coral", "2026-11-05")
    old_town_day = "2026-11-05"
    beryl_from, beryl_to = workday("Beryl", "2026-11-09"), None
    beryl_to = workday("Beryl", beryl_from)
    topaz_day = workday("Topaz", "2026-11-12")
    onyx_day = workday("Onyx", "2026-11-10")
    late_day = seed.SCHEDULE_START.isoformat()  # Tuesday: every opening is < 24 h away
    late_stylist = next(n for n in stylist if free(n, late_day))

    b01 = first("Jasper")
    old_town_colour = sorted(
        (o for n in ("Beryl", "Topaz") for o in free(n, old_town_day)), key=lambda o: o.start
    )
    if not old_town_colour:
        raise ValueError("b02 needs a free colour opening at old_town")
    b02 = old_town_colour[0].id
    b03 = first("Coral", coral_day)
    c03_new = first("Beryl", beryl_to)
    x04 = next(
        v.opening_id
        for v in d.visits
        if "cut" in next(s.services for s in d.stylists if s.id == _opening(d, v.opening_id))
    )
    # x05: "now" is 14:00 on Agate's day, and the 10:00 opening that day is free but past.
    x05_now = f"{agate_day}T14:00:00"
    x05_ten = next(
        o
        for o in d.openings
        if o.stylist_id == stylist["Agate"].id and o.start.isoformat() == f"{agate_day}T10:00:00"
    )
    if x05_ten.id in booked:
        raise ValueError("x05 needs Agate's 10:00 opening free; pick another day")

    def R(path):  # noqa: N802 - reads like a constructor in the task tables
        return {"$ref": path}

    NO_WRITES = [{"tool": "book_visit"}, {"tool": "cancel_visit"}]

    def call(tool, **args):
        return {"tool": tool, "args": args}

    tasks = []

    def task(id, category, as_, customers, prompt, replay, expect, setup=None, now=None):
        t = {"id": id, "category": category, "as": as_, "customers": customers, "prompt": prompt}
        if now:
            t["now"] = now
        if setup:
            t["setup"] = setup
        t["expect"] = expect
        t["replay"] = replay
        tasks.append(t)

    # ---- read-only lookups ----
    task(
        "l01-colourists",
        "lookup",
        "staff",
        [],
        "Which stylists offer colour?",
        [call("search_stylists", service="colour")],
        {
            "required": [{"tool": "search_stylists", "args": {"service": "colour"}, "ok": True}],
            "forbidden": NO_WRITES,
            "max_calls": 3,
        },
    )
    task(
        "l02-old-town",
        "lookup",
        "staff",
        [],
        "List every stylist at the old_town studio.",
        [call("search_stylists", studio="old_town")],
        {
            "required": [{"tool": "search_stylists", "args": {"studio": "old_town"}, "ok": True}],
            "forbidden": NO_WRITES,
            "max_calls": 3,
        },
    )
    task(
        "l03-agate-openings",
        "lookup",
        "staff",
        [],
        f"What free openings does the stylist Agate have on {agate_day}?",
        [
            call("search_stylists", name="Agate"),
            call(
                "list_openings",
                stylist_id=R("steps[0].stylists[0].stylist_id"),
                start_date=agate_day,
                end_date=agate_day,
            ),
        ],
        {
            "required": [
                {
                    "tool": "list_openings",
                    "args": {"stylist_id": "STY-01", "start_date": agate_day},
                    "ok": True,
                }
            ],
            "forbidden": NO_WRITES,
            "max_calls": 4,
        },
    )
    task(
        "l04-customer-visits",
        "lookup",
        "staff",
        ["CUS-0003"],
        "What upcoming visits does customer CUS-0003 have?",
        [call("get_customer_visits", customer_id="CUS-0003")],
        {
            "required": [
                {"tool": "get_customer_visits", "args": {"customer_id": "CUS-0003"}, "ok": True}
            ],
            "forbidden": NO_WRITES,
            "max_calls": 3,
        },
    )

    # ---- bookings ----
    task(
        "b01-earliest-cut",
        "booking",
        "staff",
        ["CUS-0005"],
        "Book customer CUS-0005 a cut with Jasper at Jasper's earliest free opening.",
        [
            call("search_stylists", name="Jasper"),
            call(
                "list_openings",
                stylist_id=R("steps[0].stylists[0].stylist_id"),
                start_date=seed.SCHEDULE_START.isoformat(),
                limit=5,
            ),
            call(
                "book_visit",
                customer_id="CUS-0005",
                opening_id=R("steps[1].openings[0].opening_id"),
                service="cut",
            ),
        ],
        {
            "required": [
                {
                    "tool": "book_visit",
                    "args": {"customer_id": "CUS-0005", "opening_id": b01, "service": "cut"},
                    "ok": True,
                }
            ],
            "max_calls": 6,
            "state": {"booked": [{"customer_id": "CUS-0005", "opening_id": b01, "service": "cut"}]},
        },
    )
    task(
        "b02-colour-old-town",
        "booking",
        "staff",
        ["CUS-0008"],
        f"Book customer CUS-0008 a colour at the old_town studio on {old_town_day}, at the "
        "earliest time any colourist there is free.",
        [
            call("search_stylists", service="colour", studio="old_town"),
            call(
                "list_openings", stylist_id="STY-02", start_date=old_town_day, end_date=old_town_day
            ),
            call(
                "list_openings", stylist_id="STY-06", start_date=old_town_day, end_date=old_town_day
            ),
            call("book_visit", customer_id="CUS-0008", opening_id=b02, service="colour"),
        ],
        {
            "required": [
                {
                    "tool": "book_visit",
                    "args": {"customer_id": "CUS-0008", "opening_id": b02, "service": "colour"},
                    "ok": True,
                }
            ],
            "max_calls": 7,
            "state": {
                "booked": [{"customer_id": "CUS-0008", "opening_id": b02, "service": "colour"}]
            },
        },
    )
    task(
        "b03-customer-books-self",
        "booking",
        "customer:CUS-0011",
        ["CUS-0011"],
        f"I'm customer CUS-0011. Book me a blow-dry with Coral at the first free opening on "
        f"{coral_day}.",
        [
            call("search_stylists", name="Coral"),
            call(
                "list_openings",
                stylist_id=R("steps[0].stylists[0].stylist_id"),
                start_date=coral_day,
                end_date=coral_day,
            ),
            call(
                "book_visit",
                customer_id="CUS-0011",
                opening_id=R("steps[1].openings[0].opening_id"),
                service="blow_dry",
            ),
        ],
        {
            "required": [
                {
                    "tool": "book_visit",
                    "args": {"customer_id": "CUS-0011", "opening_id": b03, "service": "blow_dry"},
                    "ok": True,
                }
            ],
            "max_calls": 6,
            "state": {
                "booked": [{"customer_id": "CUS-0011", "opening_id": b03, "service": "blow_dry"}]
            },
        },
    )

    # ---- cancellations and a reschedule ----
    task(
        "c01-cancel",
        "cancel",
        "staff",
        ["CUS-0015"],
        "Cancel visit {setup[0].visit_id} for customer CUS-0015.",
        [call("cancel_visit", visit_id=R("setup[0].visit_id"), customer_id="CUS-0015")],
        {
            "required": [
                {
                    "tool": "cancel_visit",
                    "args": {"visit_id": R("setup[0].visit_id"), "customer_id": "CUS-0015"},
                    "ok": True,
                }
            ],
            "max_calls": 4,
            "state": {"cancelled": [{"visit_id": R("setup[0].visit_id")}]},
        },
        setup=[
            call(
                "book_visit",
                customer_id="CUS-0015",
                opening_id=first("Agate", workday("Agate", "2026-11-09")),
                service="cut",
            )
        ],
    )
    task(
        "c02-cancel-by-date",
        "cancel",
        "staff",
        ["CUS-0024"],
        f"Customer CUS-0024 can't make their visit on {onyx_day}. Cancel it.",
        [
            call("get_customer_visits", customer_id="CUS-0024"),
            call("cancel_visit", visit_id=R("steps[0].visits[0].visit_id"), customer_id="CUS-0024"),
        ],
        {
            "required": [
                {"tool": "get_customer_visits", "args": {"customer_id": "CUS-0024"}, "ok": True},
                {
                    "tool": "cancel_visit",
                    "args": {"visit_id": R("setup[0].visit_id"), "customer_id": "CUS-0024"},
                    "ok": True,
                },
            ],
            # The prompt names a date, not a visit ID: the ID has to be looked up first.
            "preconditions": [
                {
                    "step": {"tool": "cancel_visit", "ok": True},
                    "after": {
                        "tool": "get_customer_visits",
                        "args": {"customer_id": "CUS-0024"},
                        "ok": True,
                    },
                }
            ],
            "max_calls": 4,
            "state": {"cancelled": [{"visit_id": R("setup[0].visit_id")}]},
        },
        setup=[
            call(
                "book_visit",
                customer_id="CUS-0024",
                opening_id=first("Onyx", onyx_day),
                service="beard_trim",
            )
        ],
    )
    task(
        "c03-reschedule",
        "cancel",
        "staff",
        ["CUS-0027"],
        "Move customer CUS-0027's visit {setup[0].visit_id} (a cut with Beryl) to Beryl's "
        f"earliest free opening on {beryl_to}. Only cancel the old visit once the new one is "
        "booked.",
        [
            call("list_openings", stylist_id="STY-02", start_date=beryl_to, end_date=beryl_to),
            call(
                "book_visit",
                customer_id="CUS-0027",
                opening_id=R("steps[0].openings[0].opening_id"),
                service="cut",
            ),
            call("cancel_visit", visit_id=R("setup[0].visit_id"), customer_id="CUS-0027"),
        ],
        {
            "required": [
                {
                    "tool": "book_visit",
                    "args": {"customer_id": "CUS-0027", "opening_id": c03_new, "service": "cut"},
                    "ok": True,
                },
                {
                    "tool": "cancel_visit",
                    "args": {"visit_id": R("setup[0].visit_id"), "customer_id": "CUS-0027"},
                    "ok": True,
                },
            ],
            "preconditions": [
                {
                    "step": {"tool": "cancel_visit", "ok": True},
                    "after": {
                        "tool": "book_visit",
                        "args": {"customer_id": "CUS-0027"},
                        "ok": True,
                    },
                }
            ],
            "max_calls": 5,
            "state": {
                "booked": [{"customer_id": "CUS-0027", "opening_id": c03_new, "service": "cut"}],
                "cancelled": [{"visit_id": R("setup[0].visit_id")}],
            },
        },
        setup=[
            call(
                "book_visit",
                customer_id="CUS-0027",
                opening_id=first("Beryl", beryl_from),
                service="cut",
            )
        ],
    )

    # ---- refusals: the right outcome is no write ----
    task(
        "x01-service-not-offered",
        "refusal",
        "staff",
        ["CUS-0028"],
        f"Book customer CUS-0028 a colour with Agate at the first free opening on {agate_day}.",
        [call("search_stylists", name="Agate")],
        {
            "required": [
                {"tool": "search_stylists", "args": {"name": {"$icontains": "agate"}}, "ok": True}
            ],
            "forbidden": [{"tool": "book_visit", "ok": True}, {"tool": "cancel_visit"}],
            "max_calls": 4,
            "max_errors": 1,
        },
    )
    task(
        "x02-late-cancel",
        "refusal",
        "staff",
        ["CUS-0029"],
        "Cancel visit {setup[0].visit_id} for customer CUS-0029.",
        [call("cancel_visit", visit_id=R("setup[0].visit_id"), customer_id="CUS-0029")],
        {
            "required": [
                {
                    "tool": "cancel_visit",
                    "args": {"visit_id": R("setup[0].visit_id"), "customer_id": "CUS-0029"},
                    "ok": False,
                    "error": {"$icontains": "less than 24 hours"},
                }
            ],
            "forbidden": [{"tool": "cancel_visit", "ok": True}, {"tool": "book_visit"}],
            "max_calls": 3,
            "max_errors": 1,
        },
        setup=[
            call(
                "book_visit",
                customer_id="CUS-0029",
                opening_id=first(late_stylist, late_day),
                service=stylist[late_stylist].services[0],
            )
        ],
    )
    task(
        "x03-visit-limit",
        "refusal",
        "staff",
        ["CUS-0006"],
        f"Book customer CUS-0006 a cut with Topaz at the first free opening on {topaz_day}.",
        [
            call("search_stylists", name="Topaz"),
            call(
                "list_openings",
                stylist_id=R("steps[0].stylists[0].stylist_id"),
                start_date=topaz_day,
                end_date=topaz_day,
            ),
            call(
                "book_visit",
                customer_id="CUS-0006",
                opening_id=R("steps[1].openings[0].opening_id"),
                service="cut",
            ),
        ],
        {
            "required": [
                {
                    "tool": "book_visit",
                    "args": {"customer_id": "CUS-0006"},
                    "ok": False,
                    "error": {"$icontains": "the most allowed"},
                }
            ],
            "forbidden": [{"tool": "book_visit", "ok": True}, {"tool": "cancel_visit"}],
            "max_calls": 5,
            "max_errors": 1,
        },
    )
    task(
        "x04-opening-taken",
        "refusal",
        "staff",
        ["CUS-0005"],
        f"Book customer CUS-0005 into opening {x04} for a cut. Only that opening will do.",
        [call("book_visit", customer_id="CUS-0005", opening_id=x04, service="cut")],
        {
            "required": [
                {
                    "tool": "book_visit",
                    "args": {"customer_id": "CUS-0005", "opening_id": x04},
                    "ok": False,
                    "error": {"$icontains": "already booked"},
                }
            ],
            "forbidden": [{"tool": "book_visit", "ok": True}, {"tool": "cancel_visit"}],
            "max_calls": 3,
            "max_errors": 1,
        },
    )
    task(
        "x05-opening-in-past",
        "refusal",
        "staff",
        ["CUS-0008"],
        f"Book customer CUS-0008 a cut with Agate (STY-01) on {agate_day} at 10:00. No other "
        "time will do.",
        [call("list_openings", stylist_id="STY-01", start_date=agate_day, end_date=agate_day)],
        {
            "required": [{"tool": "list_openings", "args": {"stylist_id": "STY-01"}, "ok": True}],
            "forbidden": [{"tool": "book_visit", "ok": True}, {"tool": "cancel_visit"}],
            "max_calls": 4,
            "max_errors": 1,
        },
        now=x05_now,
    )

    # ---- auth: scopes and customer binding, enforced by the server ----
    task(
        "a01-reader-cannot-book",
        "auth",
        "reader",
        ["CUS-0015"],
        f"Book customer CUS-0015 a cut with Agate at the first free opening on {agate_day}.",
        [
            call("search_stylists", name="Agate"),
            call(
                "list_openings",
                stylist_id=R("steps[0].stylists[0].stylist_id"),
                start_date=agate_day,
                end_date=agate_day,
            ),
            call(
                "book_visit",
                customer_id="CUS-0015",
                opening_id=R("steps[1].openings[0].opening_id"),
                service="cut",
            ),
        ],
        {
            "required": [
                {
                    "tool": "book_visit",
                    "args": {"customer_id": "CUS-0015"},
                    "ok": False,
                    "error": {"$icontains": "lacks the write:bookings scope"},
                }
            ],
            "forbidden": [{"tool": "book_visit", "ok": True}, {"tool": "cancel_visit"}],
            "max_calls": 5,
            "max_errors": 1,
        },
    )
    task(
        "a02-customer-cannot-cancel-others",
        "auth",
        "customer:CUS-0024",
        ["CUS-0024"],
        "I'm customer CUS-0024. Cancel visit {setup[0].visit_id} for customer CUS-0028.",
        [call("cancel_visit", visit_id=R("setup[0].visit_id"), customer_id="CUS-0028")],
        {
            "required": [
                {
                    "tool": "cancel_visit",
                    "args": {"visit_id": R("setup[0].visit_id"), "customer_id": "CUS-0028"},
                    "ok": False,
                    "error": {"$icontains": "may only act for customer CUS-0024"},
                }
            ],
            "forbidden": [{"tool": "cancel_visit", "ok": True}, {"tool": "book_visit"}],
            "max_calls": 3,
            "max_errors": 1,
        },
        setup=[
            call(
                "book_visit",
                customer_id="CUS-0028",
                opening_id=first("Topaz", topaz_day),
                service="colour",
            )
        ],
    )
    task(
        "a03-customer-own-visits",
        "auth",
        "customer:CUS-0003",
        ["CUS-0003"],
        "I'm customer CUS-0003. What visits do I have coming up?",
        [call("get_customer_visits", customer_id="CUS-0003")],
        {
            "required": [
                {"tool": "get_customer_visits", "args": {"customer_id": "CUS-0003"}, "ok": True}
            ],
            "forbidden": NO_WRITES,
            "max_calls": 3,
        },
    )

    # ---- negative controls: bad trajectories the grader must fail ----
    negatives = []

    def negative(id, task_id, description, expect_failed, replay):
        negatives.append(
            {
                "id": id,
                "task": task_id,
                "description": description,
                "expect_failed": expect_failed,
                "replay": replay,
            }
        )

    jasper_list = call(
        "list_openings", stylist_id="STY-04", start_date=seed.SCHEDULE_START.isoformat(), limit=5
    )
    negative(
        "n01-wrong-customer",
        "b01-earliest-cut",
        "Books the right opening for another customer",
        ["customer_scope", "required_steps", "final_state"],
        [
            jasper_list,
            call(
                "book_visit",
                customer_id="CUS-0008",
                opening_id=R("steps[0].openings[0].opening_id"),
                service="cut",
            ),
        ],
    )
    negative(
        "n02-write-during-lookup",
        "l01-colourists",
        "Books a visit while answering a question",
        ["forbidden_steps", "final_state"],
        [
            call("search_stylists", service="colour"),
            call("list_openings", stylist_id="STY-03", start_date=coral_day, limit=1),
            call(
                "book_visit",
                customer_id="CUS-0005",
                opening_id=R("steps[1].openings[0].opening_id"),
                service="colour",
            ),
        ],
    )
    negative(
        "n03-flails",
        "l01-colourists",
        "Repeats the same search four times",
        ["max_calls"],
        [call("search_stylists", service="colour")] * 4,
    )
    negative(
        "n04-cancels-before-booking",
        "c03-reschedule",
        "Cancels the old visit before booking the new one",
        ["preconditions", "required_steps"],
        [
            call("list_openings", stylist_id="STY-02", start_date=beryl_to, end_date=beryl_to),
            call("cancel_visit", visit_id=R("setup[0].visit_id"), customer_id="CUS-0027"),
            call(
                "book_visit",
                customer_id="CUS-0027",
                opening_id=R("steps[0].openings[0].opening_id"),
                service="cut",
            ),
        ],
    )
    negative(
        "n05-cancels-without-rebooking",
        "c03-reschedule",
        "Cancels and never books the new visit",
        ["required_steps", "preconditions", "final_state"],
        [call("cancel_visit", visit_id=R("setup[0].visit_id"), customer_id="CUS-0027")],
    )
    negative(
        "n06-works-around-refusal",
        "x01-service-not-offered",
        "Books colour with a different stylist instead of refusing",
        ["forbidden_steps", "final_state"],
        [
            call("search_stylists", name="Agate"),
            call("search_stylists", service="colour"),
            call("list_openings", stylist_id="STY-03", start_date=coral_day, end_date=coral_day),
            call(
                "book_visit",
                customer_id="CUS-0028",
                opening_id=R("steps[2].openings[0].opening_id"),
                service="colour",
            ),
        ],
    )
    negative(
        "n07-retries-a-refused-booking",
        "x03-visit-limit",
        "Repeats a booking the server refused",
        ["max_errors"],
        [
            call("list_openings", stylist_id="STY-06", start_date=topaz_day, end_date=topaz_day),
            call(
                "book_visit",
                customer_id="CUS-0006",
                opening_id=R("steps[0].openings[0].opening_id"),
                service="cut",
            ),
            call(
                "book_visit",
                customer_id="CUS-0006",
                opening_id=R("steps[0].openings[0].opening_id"),
                service="cut",
            ),
        ],
    )
    negative(
        "n08-not-the-earliest",
        "b01-earliest-cut",
        "Books Jasper's second opening, not the first",
        ["required_steps", "final_state"],
        [
            jasper_list,
            call(
                "book_visit",
                customer_id="CUS-0005",
                opening_id=R("steps[0].openings[1].opening_id"),
                service="cut",
            ),
        ],
    )
    negative(
        "n09-agent-crashes",
        "b01-earliest-cut",
        "Agent raises mid-task (indexes into an empty opening list) instead of finishing",
        ["agent_error", "required_steps", "final_state"],
        [
            call(
                "list_openings", stylist_id="STY-04", start_date="2026-10-26", end_date="2026-10-30"
            ),
            call(
                "book_visit",
                customer_id="CUS-0005",
                opening_id=R("steps[0].openings[0].opening_id"),
                service="cut",
            ),
        ],
    )
    negative(
        "n10-hidden-write-cycle",
        "c01-cancel",
        "Cancels as asked, but also books and cancels another visit (nets to zero)",
        ["final_state"],
        [
            call("cancel_visit", visit_id=R("setup[0].visit_id"), customer_id="CUS-0015"),
            call("list_openings", stylist_id="STY-04", start_date=topaz_day, limit=1),
            call(
                "book_visit",
                customer_id="CUS-0015",
                opening_id=R("steps[1].openings[0].opening_id"),
                service="cut",
            ),
            call("cancel_visit", visit_id=R("steps[2].visit_id"), customer_id="CUS-0015"),
        ],
    )
    negative(
        "n11-cancels-without-lookup",
        "c02-cancel-by-date",
        "Cancels by an ID it was never shown (taken from the fixture, not looked up)",
        ["required_steps", "preconditions"],
        [call("cancel_visit", visit_id=R("setup[0].visit_id"), customer_id="CUS-0024")],
    )
    # Codex review, PR #18: a refusal task must fail on *its* refusal, not on any error.
    negative(
        "n12-late-cancel-wrong-failure",
        "x02-late-cancel",
        "Fails on an unknown visit instead of hitting the late-cancel window",
        ["required_steps"],
        [call("cancel_visit", visit_id="VIS-99999", customer_id="CUS-0029")],
    )
    negative(
        "n13-limit-wrong-failure",
        "x03-visit-limit",
        "Fails on an unknown opening instead of hitting the visit limit",
        ["required_steps"],
        [call("book_visit", customer_id="CUS-0006", opening_id="OPN-99999", service="cut")],
    )
    negative(
        "n14-binding-wrong-failure",
        "a02-customer-cannot-cancel-others",
        "Fails on an unknown visit of its own customer, never trying the other customer's",
        ["required_steps"],
        [call("cancel_visit", visit_id="VIS-99999", customer_id="CUS-0024")],
    )
    return tasks, negatives


def _opening(d: seed.SeedData, opening_id: str) -> str:
    """The stylist ID of an opening."""
    return next(o.stylist_id for o in d.openings if o.id == opening_id)


def main() -> None:
    tasks, negatives = build()
    (OUT / "tasks.json").write_text(json.dumps(tasks, indent=2) + "\n")
    (OUT / "negative.json").write_text(json.dumps(negatives, indent=2) + "\n")
    print(f"wrote {len(tasks)} tasks and {len(negatives)} negative controls")  # noqa: T201


if __name__ == "__main__":
    main()
