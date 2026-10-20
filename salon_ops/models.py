"""Typed tool results. TypedDict returns give each tool an output schema and structured_content."""

from typing import Literal, TypedDict

Service = Literal["cut", "colour", "blow_dry", "beard_trim"]
Studio = Literal["riverside", "old_town"]
VisitStatus = Literal["booked", "cancelled"]


class StylistInfo(TypedDict):
    stylist_id: str
    name: str
    studio: Studio
    services: list[Service]


class SearchStylistsResult(TypedDict):
    stylists: list[StylistInfo]
    count: int


class OpeningInfo(TypedDict):
    opening_id: str
    start: str
    end: str


class OpeningsResult(TypedDict):
    stylist_id: str
    start_date: str
    end_date: str
    openings: list[OpeningInfo]
    count: int
    truncated: bool


class VisitResult(TypedDict):
    visit_id: str
    status: VisitStatus
    customer_id: str
    stylist_id: str
    stylist_name: str
    studio: Studio
    service: Service
    opening_id: str
    start: str
    end: str


class CustomerVisitsResult(TypedDict):
    customer_id: str
    visits: list[VisitResult]
    count: int
