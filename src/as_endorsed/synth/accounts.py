"""Synthetic NFIP flood accounts.

Real base forms, synthetic declarations. Every account is fully determined by
the master seed, so the golden Q&A set is reproducible and carries no personal
data. Roughly forty percent of accounts get a mid-term General Change
Endorsement, which is the real NFIP mechanism for changing limits or
deductibles during the term and the simplest possible case of "later effective
date controls".
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from as_endorsed.synth.endorsements import BY_ID, EDITION as END_EDITION, LIBRARY, SCHEDULE_VALUES

DWELLING_FORM = {"form_id": "NFIP-DWELLING", "edition": "2021-10", "title": "Standard Flood Insurance Policy, Dwelling Form (F-122)"}

# NFIP residential maximums and the deductible menu as of Risk Rating 2.0.
BUILDING_LIMIT_MAX = 250_000
CONTENTS_LIMIT_MAX = 100_000
DEDUCTIBLES = [1_000, 1_250, 1_500, 2_000, 3_000, 4_000, 5_000, 10_000]

STATES = {
    "FL": {"cities": [("St. Petersburg", "3370"), ("Naples", "3410"), ("Fort Myers", "3390"), ("Jacksonville Beach", "3225"), ("Cape Coral", "3390"), ("Pensacola", "3250")], "prefix": "12", "zones": ["AE", "AE", "VE", "X", "AH", "A"]},
    "TX": {"cities": [("Galveston", "7755"), ("Houston", "7702"), ("Corpus Christi", "7841"), ("Port Arthur", "7764")], "prefix": "48", "zones": ["AE", "AE", "X", "VE", "AO"]},
    "LA": {"cities": [("Metairie", "7000"), ("Houma", "7036"), ("Lake Charles", "7060"), ("Slidell", "7045")], "prefix": "22", "zones": ["AE", "AE", "A", "X", "VE"]},
    "NJ": {"cities": [("Toms River", "0875"), ("Hoboken", "0703"), ("Manasquan", "0873"), ("Atlantic City", "0840")], "prefix": "34", "zones": ["AE", "X", "AE", "VE"]},
    "SC": {"cities": [("Charleston", "2940"), ("Myrtle Beach", "2957"), ("Beaufort", "2990")], "prefix": "45", "zones": ["AE", "X", "VE", "AE"]},
    "NC": {"cities": [("Wilmington", "2840"), ("New Bern", "2856"), ("Nags Head", "2795")], "prefix": "37", "zones": ["AE", "X", "AE", "VE"]},
}
STREETS = ["Gulf Blvd", "Bayshore Dr", "Riverside Ave", "Ocean View Rd", "Seawall Blvd", "Marsh Landing Way", "Palmetto St", "Harbor Light Ln", "Canal St", "Estuary Ct", "Pelican Pt", "Tidewater Rd", "Lagoon Dr", "Shoreline Ave", "Delta Rd"]
FIRST = ["Maria", "James", "Aisha", "Robert", "Linh", "Daniel", "Priya", "Carlos", "Hannah", "Marcus", "Elena", "Samuel", "Fatima", "Thomas", "Grace", "Omar", "Nadia", "Victor", "Chloe", "Rafael"]
LAST = ["Alvarez", "Nguyen", "Okafor", "Patel", "Bennett", "Rossi", "Kowalski", "Haddad", "Lindqvist", "Moreau", "Delgado", "Ferreira", "Okonkwo", "Whitfield", "Castellano", "Brennan", "Ibrahim", "Sato", "Marchetti", "Dubois"]
AGENCIES = ["Coastal Risk Partners", "Harborline Insurance Agency", "Tidewater Brokerage", "Bayfront Insurance Services", "Palmetto Coverage Group"]
OCCUPANCY = ["Single-Family Home", "Single-Family Home", "Single-Family Home", "Two-to-Four Family Building", "Residential Unit in Condominium"]
BUILDING_DESC = [
    "One floor, no basement, slab on grade",
    "Two floors, no basement, crawlspace",
    "One floor, no basement, elevated on piers with enclosure below",
    "Two floors with finished basement",
    "Three or more floors, elevated on piles, no enclosure",
    "Manufactured home on permanent foundation",
]

ChangeField = Literal["building_limit", "contents_limit", "building_deductible", "contents_deductible"]


class Location(BaseModel):
    street: str
    city: str
    state: str
    zip: str
    flood_zone: str
    community_number: str
    occupancy: str
    building_description: str
    primary_residence: bool

    def one_line(self) -> str:
        return f"{self.street}, {self.city}, {self.state} {self.zip}"


class CoverageLine(BaseModel):
    coverage: Literal["building", "contents"]
    limit: int
    deductible: int


class ScheduledForm(BaseModel):
    form_id: str
    edition: str
    title: str


class ScheduledEndorsement(BaseModel):
    """A synthetic endorsement form attached to the policy."""

    form_id: str
    edition: str
    title: str
    effective_date: date
    schedule_values: dict[str, str] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.form_id}@{self.edition}"


class MidTermChange(BaseModel):
    """A General Change Endorsement: one field changes as of an effective date."""

    endorsement_number: str
    effective_date: date
    field: ChangeField
    old_value: int
    new_value: int
    reason: str


class FloodPolicy(BaseModel):
    policy_number: str
    term_start: date
    term_end: date
    named_insured: str
    co_insured: str | None
    mailing_address: str
    property_location: Location
    coverages: list[CoverageLine]
    annual_premium: int
    rating_method: str = "Risk Rating 2.0"
    agency: str
    forms_schedule: list[ScheduledForm]
    endorsements: list[MidTermChange] = Field(default_factory=list)
    endorsement_forms: list[ScheduledEndorsement] = Field(default_factory=list)

    def has_endorsement(self, form_id: str) -> bool:
        return any(e.form_id == form_id for e in self.endorsement_forms)

    def endorsement(self, form_id: str) -> ScheduledEndorsement | None:
        return next((e for e in self.endorsement_forms if e.form_id == form_id), None)

    def coverage(self, kind: str) -> CoverageLine:
        return next(c for c in self.coverages if c.coverage == kind)

    def value_at_issue(self, field: ChangeField) -> int:
        kind, attr = field.split("_", 1)
        return getattr(self.coverage(kind), attr)

    def value_as_of(self, field: ChangeField, on: date) -> int:
        """Value of a declarations field on a given date, applying changes in effective-date order."""
        value = self.value_at_issue(field)
        for ch in sorted(self.endorsements, key=lambda c: c.effective_date):
            if ch.field == field and ch.effective_date <= on:
                value = ch.new_value
        return value


class Account(BaseModel):
    account_id: str
    seed: int
    policy: FloodPolicy


def _premium(rng: random.Random, zone: str, building: int, contents: int, desc: str) -> int:
    zone_rate = {"X": 0.0028, "A": 0.0062, "AE": 0.0064, "AH": 0.0050, "AO": 0.0048, "VE": 0.0140}[zone]
    base = building * zone_rate + contents * zone_rate * 0.8
    if "basement" in desc.lower() and "no basement" not in desc.lower():
        base *= 1.25
    if "elevated" in desc.lower():
        base *= 0.7
    fees = 47 + 25  # federal policy fee + reserve fund assessment, rounded
    return int(round((base * rng.uniform(0.92, 1.08) + fees) / 5) * 5)


def generate_account(i: int, master_seed: int) -> Account:
    seed = master_seed * 1000 + i
    rng = random.Random(seed)
    account_id = f"SYN-{i:05d}"

    state = rng.choice(list(STATES))
    st = STATES[state]
    city, zip_prefix = rng.choice(st["cities"])
    zone = rng.choice(st["zones"])
    desc = rng.choice(BUILDING_DESC)
    loc = Location(
        street=f"{rng.randint(100, 9899)} {rng.choice(STREETS)}",
        city=city,
        state=state,
        zip=f"{zip_prefix}{rng.randint(0, 9)}",
        flood_zone=zone,
        community_number=f"{st['prefix']}{rng.randint(1000, 9999)}",
        occupancy=rng.choice(OCCUPANCY),
        building_description=desc,
        primary_residence=rng.random() < 0.7,
    )

    building_limit = rng.choice([100_000, 150_000, 200_000, 220_000, 250_000, 250_000, 250_000])
    contents_limit = rng.choice([0, 25_000, 40_000, 50_000, 75_000, 100_000])
    building_ded = rng.choice(DEDUCTIBLES)
    contents_ded = building_ded if rng.random() < 0.7 else rng.choice(DEDUCTIBLES)

    coverages = [CoverageLine(coverage="building", limit=building_limit, deductible=building_ded)]
    if contents_limit:
        coverages.append(CoverageLine(coverage="contents", limit=contents_limit, deductible=contents_ded))

    first, last = rng.choice(FIRST), rng.choice(LAST)
    named = f"{first} {last}"
    co = f"{rng.choice(FIRST)} {last}" if rng.random() < 0.4 else None
    mailing = loc.one_line() if loc.primary_residence else f"PO Box {rng.randint(100, 9999)}, {city}, {state} {loc.zip}"

    term_start = date(2025, 6, 1) + timedelta(days=rng.randint(0, 365))
    term_end = term_start.replace(year=term_start.year + 1) - timedelta(days=1)

    policy = FloodPolicy(
        policy_number=f"NFP-{term_start.year}-{rng.randint(1_000_000, 9_999_999)}",
        term_start=term_start,
        term_end=term_end,
        named_insured=named,
        co_insured=co,
        mailing_address=mailing,
        property_location=loc,
        coverages=coverages,
        annual_premium=_premium(rng, zone, building_limit, contents_limit, desc),
        agency=rng.choice(AGENCIES),
        forms_schedule=[ScheduledForm(**DWELLING_FORM)],
    )

    if rng.random() < 0.4:
        policy.endorsements.append(_mid_term_change(rng, policy))
    policy.endorsement_forms = _attach_endorsements(rng, policy)

    return Account(account_id=account_id, seed=seed, policy=policy)


def _attach_endorsements(rng: random.Random, policy: FloodPolicy) -> list[ScheduledEndorsement]:
    """Attach zero to three synthetic endorsements. A quarter of accounts get the
    conflicting basement pair (SYN-END-01 at issue, SYN-END-06 mid-term) so the
    later-date precedence rule is exercised on real data."""
    if rng.random() < 0.25:
        return []
    chosen: list[tuple[str, date]] = []
    if rng.random() < 0.25:
        chosen.append(("SYN-END-01", policy.term_start))
        chosen.append(("SYN-END-06", policy.term_start + timedelta(days=rng.randint(45, 240))))
    pool = [e.form_id for e in LIBRARY if e.form_id not in {c[0] for c in chosen} and e.form_id not in ("SYN-END-01", "SYN-END-06")]
    if not chosen or rng.random() < 0.5:
        pool = pool + ["SYN-END-01", "SYN-END-06"] if not chosen else pool
    for form_id in rng.sample(pool, k=min(len(pool), rng.randint(1, 3))):
        if form_id in ("SYN-END-01", "SYN-END-06") and any(c[0] in ("SYN-END-01", "SYN-END-06") for c in chosen):
            continue
        effective = policy.term_start if rng.random() < 0.65 else policy.term_start + timedelta(days=rng.randint(30, 200))
        chosen.append((form_id, effective))
    out = []
    for form_id, effective in chosen:
        spec = BY_ID[form_id]
        values = {spec.schedule_prompt: rng.choice(SCHEDULE_VALUES)} if spec.schedule_prompt else {}
        out.append(ScheduledEndorsement(form_id=form_id, edition=END_EDITION, title=spec.title, effective_date=effective, schedule_values=values))
    out.sort(key=lambda e: (e.effective_date, e.form_id))
    return out


def _mid_term_change(rng: random.Random, policy: FloodPolicy) -> MidTermChange:
    options: list[ChangeField] = ["building_limit", "building_deductible"]
    if any(c.coverage == "contents" for c in policy.coverages):
        options += ["contents_limit", "contents_deductible"]
    field = rng.choice(options)
    old = policy.value_at_issue(field)
    if field.endswith("_limit"):
        cap = BUILDING_LIMIT_MAX if field.startswith("building") else CONTENTS_LIMIT_MAX
        choices = [v for v in range(25_000, cap + 1, 25_000) if v != old]
        new = rng.choice(choices)
        reason = "Increase in coverage requested by insured" if new > old else "Reduction in coverage requested by insured"
    else:
        choices = [d for d in DEDUCTIBLES if d != old]
        new = rng.choice(choices)
        reason = "Deductible change requested by insured"
    effective = policy.term_start + timedelta(days=rng.randint(30, 270))
    return MidTermChange(
        endorsement_number=f"GCE-{rng.randint(100000, 999999)}",
        effective_date=effective,
        field=field,
        old_value=old,
        new_value=new,
        reason=reason,
    )


def generate_accounts(n: int, *, seed: int) -> list[Account]:
    return [generate_account(i + 1, seed) for i in range(n)]
