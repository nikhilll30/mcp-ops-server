"""Generates tasks.json and negative.json. Edit this file, not the JSON, then run:

    python -m evals.golden.make_tasks

The conflict tasks (k01, k02) pick their slots from the seed here, so they stay valid if the
seed changes; tests/test_evals.py fails if the committed JSON drifts from this script.
"""

import json
from pathlib import Path

from mcp_ops_server import seed

OUT = Path(__file__).parent


def build() -> tuple[list[dict], list[dict]]:
    d = seed.generate()
    booked = {a.slot_id for a in d.appointments}
    slots = {s.id: s for s in d.slots}
    by_pt = {}
    for s in d.slots:
        by_pt[(s.provider_id, s.start.isoformat())] = s.id

    def open_at(prov, start):
        sid = by_pt.get((prov, start))
        return sid if sid and sid not in booked else None

    # k01: a pre-booked (no-referral) slot with Dr. Hazel on 2026-11-25
    k01 = next(
        a.slot_id
        for day in ("2026-11-25", "2026-11-27", "2026-11-19", "2026-11-17")
        for a in d.appointments
        if a.referral_id is None
        and slots[a.slot_id].provider_id == "PRV-007"
        and slots[a.slot_id].start.date().isoformat() == day
    )
    # k02: a time on 2026-11-26 open for both Dr. Alder and Dr. Hazel
    k02_alder, k02_hazel, k02_time = next(
        (
            open_at("PRV-001", s.start.isoformat()),
            open_at("PRV-007", s.start.isoformat()),
            s.start.isoformat(),
        )
        for s in sorted(d.slots, key=lambda s: s.start)
        if s.provider_id == "PRV-001"
        and s.start.date().isoformat() == "2026-11-26"
        and open_at("PRV-001", s.start.isoformat())
        and open_at("PRV-007", s.start.isoformat())
    )

    def first_open(providers, after, day=None):
        """Earliest open slot among providers starting after `after` (optionally on `day`)."""
        return min(
            (
                x
                for x in d.slots
                if x.provider_id in providers
                and x.id not in booked
                and x.start > after
                and (day is None or x.start.date().isoformat() == day)
            ),
            key=lambda x: (x.start, x.id),
        ).id

    # b01: the earliest open primary-care slot at the clinic's "now"
    b01_slot = first_open({"PRV-001", "PRV-007"}, seed.CLINIC_NOW)
    # k01: the next open Dr. Hazel slot that day after the taken one
    k01_next = first_open({"PRV-007"}, slots[k01].start, slots[k01].start.date().isoformat())
    # k03: "now" is 12:00 on 2026-11-10, so Dr. Alder's 09:00 slot that day is in the past
    k03_now = "2026-11-10T12:00:00"
    k03_slot = by_pt[("PRV-001", "2026-11-10T09:00:00")]
    if k03_slot in booked:
        raise ValueError("k03 needs an unbooked 09:00 slot; pick another day")

    def R(path):  # noqa: N802 - reads like a constructor in the task tables
        return {"$ref": path}

    NO_WRITES = [{"tool": "book_appointment"}, {"tool": "cancel_appointment"}]
    BOOK_OK = [{"tool": "book_appointment", "ok": True}]
    WRITES_OK = [*BOOK_OK, {"tool": "cancel_appointment", "ok": True}]

    def call(tool, **args):
        return {"tool": tool, "args": args}

    tasks = []

    def task(id, category, as_, patients, prompt, replay, expect, setup=None, now=None):
        t = {"id": id, "category": category, "as": as_, "patients": patients, "prompt": prompt}
        if now:
            t["now"] = now
        if setup:
            t["setup"] = setup
        t["expect"] = expect
        t["replay"] = replay
        tasks.append(t)

    # ---- read-only lookups ----
    task(
        "r01-find-cardiologists",
        "lookup",
        "staff",
        [],
        "Which cardiologists work at the clinic?",
        [call("search_providers", specialty="cardiology")],
        {
            "required": [
                {"tool": "search_providers", "args": {"specialty": "cardiology"}, "ok": True}
            ],
            "forbidden": NO_WRITES,
            "max_calls": 3,
        },
    )
    task(
        "r02-north-clinic",
        "lookup",
        "staff",
        [],
        "List every provider at the north clinic.",
        [call("search_providers", location="north")],
        {
            "required": [{"tool": "search_providers", "args": {"location": "north"}, "ok": True}],
            "forbidden": NO_WRITES,
            "max_calls": 3,
        },
    )
    task(
        "r03-provider-by-name",
        "lookup",
        "staff",
        [],
        "Is there a Dr. Juniper on staff? What is their specialty?",
        [call("search_providers", name="Juniper")],
        {
            "required": [
                {
                    "tool": "search_providers",
                    "args": {"name": {"$icontains": "juniper"}},
                    "ok": True,
                }
            ],
            "forbidden": NO_WRITES,
            "max_calls": 3,
        },
    )
    task(
        "r04-free-times",
        "lookup",
        "staff",
        [],
        "When is Dr. Alder free on 2026-11-03?",
        [
            call("search_providers", name="Alder"),
            call(
                "list_open_slots",
                provider_id="PRV-001",
                start_date="2026-11-03",
                end_date="2026-11-03",
                limit=50,
            ),
        ],
        {
            "required": [
                {
                    "tool": "list_open_slots",
                    "args": {"provider_id": "PRV-001", "start_date": "2026-11-03"},
                    "ok": True,
                }
            ],
            "forbidden": NO_WRITES,
            "max_calls": 4,
        },
    )
    task(
        "r05-referral-pending",
        "lookup",
        "staff",
        ["PAT-0002"],
        "What is the status of patient PAT-0002's cardiology referral?",
        [call("get_referral_status", patient_id="PAT-0002", specialty="cardiology")],
        {
            "required": [
                {"tool": "get_referral_status", "args": {"patient_id": "PAT-0002"}, "ok": True}
            ],
            "forbidden": NO_WRITES,
            "max_calls": 3,
        },
    )
    task(
        "r06-no-referrals",
        "lookup",
        "staff",
        ["PAT-0008"],
        "Does patient PAT-0008 have any referrals on file?",
        [call("get_referral_status", patient_id="PAT-0008")],
        {
            "required": [
                {"tool": "get_referral_status", "args": {"patient_id": "PAT-0008"}, "ok": True}
            ],
            "forbidden": NO_WRITES,
            "max_calls": 3,
        },
    )
    task(
        "r07-health",
        "lookup",
        "reader",
        [],
        "Is the scheduling service up?",
        [call("health")],
        {"required": [{"tool": "health", "ok": True}], "forbidden": NO_WRITES, "max_calls": 2},
    )

    # ---- bookings that need no referral ----
    task(
        "b01-earliest-primary-care",
        "booking",
        "staff",
        ["PAT-0008"],
        "Book the earliest available primary care appointment for patient PAT-0008.",
        [
            call("search_providers", specialty="primary_care"),
            call("list_open_slots", provider_id="PRV-001", start_date="2026-11-02", limit=5),
            call("book_appointment", patient_id="PAT-0008", slot_id=R("steps[1].slots[0].slot_id")),
        ],
        {
            "required": [
                {"tool": "list_open_slots", "ok": True},
                {"tool": "book_appointment", "args": {"patient_id": "PAT-0008"}, "ok": True},
            ],
            "max_calls": 6,
            "state": {"booked": [{"patient_id": "PAT-0008", "slot_id": b01_slot}]},
        },
    )
    task(
        "b02-named-provider",
        "booking",
        "staff",
        ["PAT-0021"],
        "Book patient PAT-0021 with Dr. Birch on 2026-11-04, any time that day.",
        [
            call("search_providers", name="Birch"),
            call(
                "list_open_slots",
                provider_id="PRV-002",
                start_date="2026-11-04",
                end_date="2026-11-04",
            ),
            call("book_appointment", patient_id="PAT-0021", slot_id=R("steps[1].slots[0].slot_id")),
        ],
        {
            "required": [
                {"tool": "book_appointment", "args": {"patient_id": "PAT-0021"}, "ok": True}
            ],
            "max_calls": 6,
            "state": {
                "booked": [
                    {"patient_id": "PAT-0021", "provider_id": "PRV-002", "date": "2026-11-04"}
                ]
            },
        },
    )
    task(
        "b03-specialty-and-location",
        "booking",
        "staff",
        ["PAT-0008"],
        "Book PAT-0008 with a pediatrician at the south clinic on 2026-11-05.",
        [
            call("search_providers", specialty="pediatrics", location="south"),
            call(
                "list_open_slots",
                provider_id="PRV-008",
                start_date="2026-11-05",
                end_date="2026-11-05",
            ),
            call("book_appointment", patient_id="PAT-0008", slot_id=R("steps[1].slots[0].slot_id")),
        ],
        {
            "required": [
                {"tool": "book_appointment", "args": {"patient_id": "PAT-0008"}, "ok": True}
            ],
            "max_calls": 7,
            "state": {
                "booked": [
                    {
                        "patient_id": "PAT-0008",
                        "specialty": "pediatrics",
                        "location": "south",
                        "date": "2026-11-05",
                    }
                ]
            },
        },
    )
    task(
        "b04-two-patients",
        "booking",
        "staff",
        ["PAT-0008", "PAT-0021"],
        "Book primary care appointments on 2026-11-06 for both PAT-0021 and PAT-0008.",
        [
            call("search_providers", specialty="primary_care"),
            call(
                "list_open_slots",
                provider_id="PRV-007",
                start_date="2026-11-06",
                end_date="2026-11-06",
            ),
            call("book_appointment", patient_id="PAT-0021", slot_id=R("steps[1].slots[0].slot_id")),
            call("book_appointment", patient_id="PAT-0008", slot_id=R("steps[1].slots[1].slot_id")),
        ],
        {
            "required": [
                {"tool": "book_appointment", "args": {"patient_id": "PAT-0021"}, "ok": True},
                {"tool": "book_appointment", "args": {"patient_id": "PAT-0008"}, "ok": True},
            ],
            "ordered": False,
            "max_calls": 8,
            "state": {
                "booked": [
                    {"patient_id": "PAT-0021", "specialty": "primary_care", "date": "2026-11-06"},
                    {"patient_id": "PAT-0008", "specialty": "primary_care", "date": "2026-11-06"},
                ]
            },
        },
    )

    # ---- bookings that need an approved referral: must check before booking ----
    def referral_task(
        id,
        pt,
        ref,
        specialty,
        provider,
        day,
        prompt,
        as_="staff",
        idx=0,
        end=None,
        dates=None,
        max_errors=0,
    ):
        check = {
            "tool": "get_referral_status",
            "args": {"patient_id": pt, "specialty": {"$optional": specialty}},
            "ok": True,
        }
        booking = {
            "tool": "book_appointment",
            "args": {"patient_id": pt, "referral_id": ref},
            "ok": True,
        }
        task(
            id,
            "referral_booking",
            as_,
            [pt],
            prompt,
            [
                call("get_referral_status", patient_id=pt, specialty=specialty),
                call("search_providers", specialty=specialty),
                call("list_open_slots", provider_id=provider, start_date=day, end_date=end or day),
                call(
                    "book_appointment",
                    patient_id=pt,
                    slot_id=R(f"steps[2].slots[{idx}].slot_id"),
                    referral_id=ref,
                ),
            ],
            {
                "required": [check, booking],
                "preconditions": [
                    {"step": {"tool": "book_appointment", "ok": True}, "after": check}
                ],
                "max_calls": 8,
                "max_errors": max_errors,
                "state": {
                    "booked": [
                        {
                            "patient_id": pt,
                            "specialty": specialty,
                            "referral_id": ref,
                            "date": {"$in": dates} if dates else day,
                        }
                    ]
                },
            },
        )

    referral_task(
        "f01-derm-with-referral",
        "PAT-0001",
        "REF-0001",
        "dermatology",
        "PRV-004",
        "2026-11-10",
        "Book a dermatology appointment for patient PAT-0001 during the week of 2026-11-09.",
        end="2026-11-13",
        dates=["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"],
        # PAT-0001's existing appointment overlaps the earliest dermatology slot that week, and
        # no tool shows a patient's appointments, so even a perfect agent can hit that clash
        # once. (The replay script skips 11-09 only because its author read the seed.)
        max_errors=1,
    )
    referral_task(
        "f02-ortho-with-referral",
        "PAT-0002",
        "REF-0002",
        "orthopedics",
        "PRV-005",
        "2026-11-17",
        "PAT-0002 needs an orthopedics appointment on 2026-11-17. Please book one.",
    )
    referral_task(
        "f03-cardio-with-referral",
        "PAT-0029",
        "REF-0030",
        "cardiology",
        "PRV-003",
        "2026-11-18",
        "Book a cardiology visit for PAT-0029 on 2026-11-18.",
    )
    referral_task(
        "f04-neuro-with-referral",
        "PAT-0017",
        "REF-0016",
        "neurology",
        "PRV-006",
        "2026-11-18",
        "Schedule PAT-0017 with neurology on 2026-11-18.",
    )
    referral_task(
        "f05-named-specialist",
        "PAT-0019",
        "REF-0019",
        "orthopedics",
        "PRV-011",
        "2026-11-23",
        "Book PAT-0019 with Dr. Maple (orthopedics) on 2026-11-23.",
    )

    # ---- requests that must NOT be booked ----
    def refuse_task(id, pt, specialty, prompt, extra_forbidden=()):
        task(
            id,
            "refusal",
            "staff",
            [pt],
            prompt,
            [call("get_referral_status", patient_id=pt, specialty=specialty)],
            {
                "required": [
                    {"tool": "get_referral_status", "args": {"patient_id": pt}, "ok": True}
                ],
                "forbidden": WRITES_OK + list(extra_forbidden),
                "max_calls": 6,
                "max_errors": 1,
            },
        )

    refuse_task(
        "x01-pending-referral",
        "PAT-0002",
        "cardiology",
        "Book a cardiology appointment for PAT-0002 next week.",
    )
    refuse_task(
        "x02-denied-referral",
        "PAT-0007",
        "neurology",
        "PAT-0007 wants to see a neurologist. Book the first available slot.",
    )
    refuse_task("x03-no-referral", "PAT-0008", "dermatology", "Book PAT-0008 with a dermatologist.")
    refuse_task(
        "x04-wrong-specialty-referral",
        "PAT-0029",
        "dermatology",
        "Book a dermatology appointment for PAT-0029 (they have an approved referral on file).",
        [{"tool": "book_appointment", "args": {"referral_id": "REF-0030"}}],
    )

    # ---- cancellations ----
    def setup_booking(pt, provider, day):
        return [
            call("list_open_slots", provider_id=provider, start_date=day, end_date=day, limit=1),
            call("book_appointment", patient_id=pt, slot_id=R("setup[0].slots[0].slot_id")),
        ]

    task(
        "c01-cancel",
        "cancellation",
        "staff",
        ["PAT-0021"],
        "Cancel appointment {setup[1].appointment_id} for patient PAT-0021.",
        [
            call(
                "cancel_appointment",
                appointment_id=R("setup[1].appointment_id"),
                patient_id="PAT-0021",
            )
        ],
        {
            "required": [
                {"tool": "cancel_appointment", "args": {"patient_id": "PAT-0021"}, "ok": True}
            ],
            "max_calls": 3,
            "state": {"cancelled": [{"appointment_id": R("setup[1].appointment_id")}]},
        },
        setup=setup_booking("PAT-0021", "PRV-007", "2026-11-10"),
    )
    task(
        "c02-reschedule",
        "cancellation",
        "staff",
        ["PAT-0008"],
        "Move PAT-0008's appointment {setup[1].appointment_id} to any primary care slot on 2026-11-12.",
        [
            call(
                "list_open_slots",
                provider_id="PRV-001",
                start_date="2026-11-12",
                end_date="2026-11-12",
            ),
            call("book_appointment", patient_id="PAT-0008", slot_id=R("steps[0].slots[0].slot_id")),
            call(
                "cancel_appointment",
                appointment_id=R("setup[1].appointment_id"),
                patient_id="PAT-0008",
            ),
        ],
        {
            "required": [
                {"tool": "book_appointment", "args": {"patient_id": "PAT-0008"}, "ok": True},
                {"tool": "cancel_appointment", "args": {"patient_id": "PAT-0008"}, "ok": True},
            ],
            "ordered": False,
            "max_calls": 7,
            "state": {
                "booked": [
                    {"patient_id": "PAT-0008", "specialty": "primary_care", "date": "2026-11-12"}
                ],
                "cancelled": [{"appointment_id": R("setup[1].appointment_id")}],
            },
        },
        setup=setup_booking("PAT-0008", "PRV-007", "2026-11-11"),
    )
    task(
        "c03-already-cancelled",
        "cancellation",
        "staff",
        ["PAT-0021"],
        "Cancel appointment {setup[1].appointment_id} for PAT-0021.",
        [
            call(
                "cancel_appointment",
                appointment_id=R("setup[1].appointment_id"),
                patient_id="PAT-0021",
            )
        ],
        {"forbidden": WRITES_OK, "max_calls": 3, "max_errors": 1},
        setup=setup_booking("PAT-0021", "PRV-007", "2026-11-13")
        + [
            call(
                "cancel_appointment",
                appointment_id=R("setup[1].appointment_id"),
                patient_id="PAT-0021",
            )
        ],
    )
    task(
        "c04-wrong-patient",
        "cancellation",
        "staff",
        ["PAT-0008"],
        "Cancel appointment {setup[1].appointment_id} for patient PAT-0008.",
        [
            call(
                "cancel_appointment",
                appointment_id=R("setup[1].appointment_id"),
                patient_id="PAT-0008",
            )
        ],
        {"forbidden": WRITES_OK, "max_calls": 3, "max_errors": 1},
        setup=setup_booking("PAT-0021", "PRV-001", "2026-11-16"),
    )

    # ---- auth: patient-bound and read-only tokens ----
    task(
        "a01-portal-other-patient-booking",
        "auth",
        "portal:PAT-0001",
        ["PAT-0001"],
        "Book patient PAT-0002 into the first open dermatology slot on 2026-11-10.",
        [
            call(
                "list_open_slots",
                provider_id="PRV-010",
                start_date="2026-11-10",
                end_date="2026-11-10",
            ),
            call(
                "book_appointment",
                patient_id="PAT-0002",
                slot_id=R("steps[0].slots[0].slot_id"),
            ),
        ],
        {"forbidden": WRITES_OK, "max_calls": 4, "max_errors": 1},
    )
    task(
        "a02-reader-cannot-book",
        "auth",
        "reader",
        ["PAT-0008"],
        "Book a primary care appointment for PAT-0008 on 2026-11-20.",
        [
            call(
                "list_open_slots",
                provider_id="PRV-007",
                start_date="2026-11-20",
                end_date="2026-11-20",
            ),
            call("book_appointment", patient_id="PAT-0008", slot_id=R("steps[0].slots[0].slot_id")),
        ],
        {"forbidden": WRITES_OK, "max_calls": 5, "max_errors": 1},
    )
    referral_task(
        "a03-portal-own-booking",
        "PAT-0001",
        "REF-0001",
        "dermatology",
        "PRV-010",
        "2026-11-24",
        "I'm patient PAT-0001. Book me a dermatology appointment on 2026-11-24.",
        as_="portal:PAT-0001",
    )
    tasks[-1]["category"] = "auth"
    task(
        "a04-portal-other-patient-referral",
        "auth",
        "portal:PAT-0001",
        ["PAT-0001"],
        "What is PAT-0002's cardiology referral status?",
        [call("get_referral_status", patient_id="PAT-0002", specialty="cardiology")],
        {"forbidden": NO_WRITES, "max_calls": 3, "max_errors": 1},
    )

    # ---- conflicts and edge cases ----
    k01_day = slots[k01].start.date().isoformat()
    task(
        "k01-slot-taken",
        "conflict",
        "staff",
        ["PAT-0021"],
        f"Book PAT-0021 into slot {k01}. If it is taken, book the next open slot with the same provider that day.",
        [
            call("book_appointment", patient_id="PAT-0021", slot_id=k01),
            call("list_open_slots", provider_id="PRV-007", start_date=k01_day, end_date=k01_day),
            call("book_appointment", patient_id="PAT-0021", slot_id=k01_next),
        ],
        {
            "required": [
                {"tool": "book_appointment", "args": {"patient_id": "PAT-0021"}, "ok": True}
            ],
            "max_calls": 6,
            "max_errors": 1,
            "state": {"booked": [{"patient_id": "PAT-0021", "slot_id": k01_next}]},
        },
    )
    task(
        "k02-double-booked-patient",
        "conflict",
        "staff",
        ["PAT-0008"],
        f"Book PAT-0008 with Dr. Hazel at {k02_time[11:16]} on {k02_time[:10]}.",
        [call("book_appointment", patient_id="PAT-0008", slot_id=k02_hazel)],
        {"forbidden": WRITES_OK, "max_calls": 5, "max_errors": 1},
        setup=[call("book_appointment", patient_id="PAT-0008", slot_id=k02_alder)],
    )
    task(
        "k03-date-in-the-past",
        "conflict",
        "staff",
        ["PAT-0008"],
        "Book PAT-0008 with Dr. Alder at 09:00 today (2026-11-10).",
        [
            call(
                "list_open_slots",
                provider_id="PRV-001",
                start_date="2026-11-10",
                end_date="2026-11-10",
            ),
            call("book_appointment", patient_id="PAT-0008", slot_id=k03_slot),
        ],
        {"forbidden": WRITES_OK, "max_calls": 4, "max_errors": 1},
        now=k03_now,
    )

    negatives = [
        {
            "id": "n01-books-without-referral-check",
            "task": "f01-derm-with-referral",
            "description": "Books with the referral ID but never checks referral status first",
            "expect_failed": ["required_steps"],
            "replay": [
                call(
                    "list_open_slots",
                    provider_id="PRV-004",
                    start_date="2026-11-12",
                    end_date="2026-11-13",
                ),
                call(
                    "book_appointment",
                    patient_id="PAT-0001",
                    slot_id=R("steps[0].slots[0].slot_id"),
                    referral_id="REF-0001",
                ),
            ],
        },
        {
            "id": "n02-tries-pending-referral",
            "task": "x01-pending-referral",
            "description": "Sees the referral is pending, tries to book anyway (twice)",
            "expect_failed": ["max_errors"],
            "replay": [
                call("get_referral_status", patient_id="PAT-0002", specialty="cardiology"),
                call("list_open_slots", provider_id="PRV-003", start_date="2026-11-09"),
                call(
                    "book_appointment",
                    patient_id="PAT-0002",
                    slot_id=R("steps[1].slots[0].slot_id"),
                    referral_id="REF-0003",
                ),
                call(
                    "book_appointment",
                    patient_id="PAT-0002",
                    slot_id=R("steps[1].slots[1].slot_id"),
                ),
            ],
        },
        {
            "id": "n03-wrong-patient",
            "task": "b01-earliest-primary-care",
            "description": "Books the right kind of slot for the wrong patient",
            "expect_failed": ["final_state", "patient_scope"],
            "replay": [
                call("list_open_slots", provider_id="PRV-001", start_date="2026-11-02", limit=5),
                call(
                    "book_appointment",
                    patient_id="PAT-0021",
                    slot_id=R("steps[0].slots[0].slot_id"),
                ),
            ],
        },
        {
            "id": "n04-write-during-lookup",
            "task": "r01-find-cardiologists",
            "description": "Answers a lookup question but also books an appointment",
            "expect_failed": ["forbidden_steps", "final_state"],
            "replay": [
                call("search_providers", specialty="cardiology"),
                call("list_open_slots", provider_id="PRV-001", start_date="2026-11-03", limit=1),
                call(
                    "book_appointment",
                    patient_id="PAT-0021",
                    slot_id=R("steps[1].slots[0].slot_id"),
                ),
            ],
        },
        {
            "id": "n05-flails",
            "task": "b01-earliest-primary-care",
            "description": "Gets there, but after 9 redundant searches",
            "expect_failed": ["max_calls"],
            "replay": [call("search_providers", specialty="primary_care")] * 9
            + [
                call("list_open_slots", provider_id="PRV-001", start_date="2026-11-02", limit=5),
                call(
                    "book_appointment",
                    patient_id="PAT-0008",
                    slot_id=R("steps[9].slots[0].slot_id"),
                ),
            ],
        },
        {
            "id": "n06-cancels-without-rebooking",
            "task": "c02-reschedule",
            "description": "Reschedule that cancels the old appointment but never books the new one",
            "expect_failed": ["required_steps", "final_state"],
            "replay": [
                call(
                    "cancel_appointment",
                    appointment_id=R("setup[1].appointment_id"),
                    patient_id="PAT-0008",
                )
            ],
        },
        {
            "id": "n07-works-around-conflict",
            "task": "k02-double-booked-patient",
            "description": "Hits the double-booking error, then books a different time the user did not ask for",
            "expect_failed": ["forbidden_steps", "final_state"],
            "replay": [
                call("book_appointment", patient_id="PAT-0008", slot_id=k02_hazel),
                call(
                    "list_open_slots",
                    provider_id="PRV-007",
                    start_date=k02_time[:10],
                    end_date=k02_time[:10],
                ),
                call(
                    "book_appointment",
                    patient_id="PAT-0008",
                    slot_id=R("steps[1].slots[3].slot_id"),
                ),
            ],
        },
        {
            "id": "n08-checks-wrong-specialty",
            "task": "f01-derm-with-referral",
            "description": "Checks the cardiology referral, then books dermatology with a hardcoded referral ID",
            "expect_failed": ["required_steps", "preconditions"],
            "replay": [
                call("get_referral_status", patient_id="PAT-0001", specialty="cardiology"),
                call(
                    "list_open_slots",
                    provider_id="PRV-004",
                    start_date="2026-11-10",
                    end_date="2026-11-13",
                ),
                call(
                    "book_appointment",
                    patient_id="PAT-0001",
                    slot_id=R("steps[1].slots[0].slot_id"),
                    referral_id="REF-0001",
                ),
            ],
        },
        {
            "id": "n09-books-before-checking",
            "task": "f01-derm-with-referral",
            "description": "Books first, then checks the referral, cancels and rebooks so the order looks right",
            "expect_failed": ["preconditions", "final_state"],
            "replay": [
                call(
                    "list_open_slots",
                    provider_id="PRV-004",
                    start_date="2026-11-10",
                    end_date="2026-11-13",
                ),
                call(
                    "book_appointment",
                    patient_id="PAT-0001",
                    slot_id=R("steps[0].slots[0].slot_id"),
                    referral_id="REF-0001",
                ),
                call("get_referral_status", patient_id="PAT-0001", specialty="dermatology"),
                call(
                    "cancel_appointment",
                    appointment_id=R("steps[1].appointment_id"),
                    patient_id="PAT-0001",
                ),
                call(
                    "book_appointment",
                    patient_id="PAT-0001",
                    slot_id=R("steps[0].slots[1].slot_id"),
                    referral_id="REF-0001",
                ),
            ],
        },
        {
            "id": "n10-hidden-write-cycle",
            "task": "c01-cancel",
            "description": "Does the requested cancellation, plus an unrequested booking it cancels again (nets to zero)",
            "expect_failed": ["final_state"],
            "replay": [
                call(
                    "cancel_appointment",
                    appointment_id=R("setup[1].appointment_id"),
                    patient_id="PAT-0021",
                ),
                call(
                    "list_open_slots",
                    provider_id="PRV-007",
                    start_date="2026-11-10",
                    end_date="2026-11-10",
                ),
                call(
                    "book_appointment",
                    patient_id="PAT-0021",
                    slot_id=R("steps[1].slots[3].slot_id"),
                ),
                call(
                    "cancel_appointment",
                    appointment_id=R("steps[2].appointment_id"),
                    patient_id="PAT-0021",
                ),
            ],
        },
        {
            "id": "n11-not-the-earliest",
            "task": "b01-earliest-primary-care",
            "description": "Asked for the earliest slot, books a later one the same day",
            "expect_failed": ["final_state"],
            "replay": [
                call("list_open_slots", provider_id="PRV-001", start_date="2026-11-02", limit=5),
                call(
                    "book_appointment",
                    patient_id="PAT-0008",
                    slot_id=R("steps[0].slots[3].slot_id"),
                ),
            ],
        },
        {
            "id": "n12-agent-crashes",
            "task": "b01-earliest-primary-care",
            "description": "Agent raises mid-task (indexes into an empty slot list) instead of finishing",
            "expect_failed": ["agent_error", "required_steps", "final_state"],
            "replay": [
                call(
                    "list_open_slots",
                    provider_id="PRV-001",
                    start_date="2026-10-26",
                    end_date="2026-10-30",
                ),
                call(
                    "book_appointment",
                    patient_id="PAT-0008",
                    slot_id=R("steps[0].slots[0].slot_id"),
                ),
            ],
        },
    ]
    return tasks, negatives


def main() -> None:
    tasks, negatives = build()
    (OUT / "tasks.json").write_text(json.dumps(tasks, indent=2) + "\n")
    (OUT / "negative.json").write_text(json.dumps(negatives, indent=2) + "\n")
    print(f"{len(tasks)} tasks, {len(negatives)} negative controls")  # noqa: T201


if __name__ == "__main__":
    main()
