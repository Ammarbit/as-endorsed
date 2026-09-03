"""Registry of public policy forms and where to fetch them.

Only public-domain or freely published material is listed here. ISO-owned forms
never enter the repository; see README § Licensing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from as_endorsed.models import FormSpec

FORMS: list[FormSpec] = [
    FormSpec(
        form_id="NFIP-DWELLING",
        edition="2021-10",
        kind="base",
        title="Standard Flood Insurance Policy, Dwelling Form",
        source="FEMA F-122, October 2021",
        url="https://www.fema.gov/sites/default/files/documents/fema_F-122-Dwelling-SFIP_2021.pdf",
        filename="nfip/F-122_Dwelling_2021.pdf",
        license="US Government work, public domain (44 CFR 61 App. A(1))",
    ),
    FormSpec(
        form_id="NFIP-GENERAL-PROPERTY",
        edition="2021-10",
        kind="base",
        title="Standard Flood Insurance Policy, General Property Form",
        source="FEMA F-123, October 2021",
        url="https://www.fema.gov/sites/default/files/documents/fema_F-123-general-property-SFIP_2021.pdf",
        filename="nfip/F-123_GeneralProperty_2021.pdf",
        license="US Government work, public domain (44 CFR 61 App. A(2))",
    ),
    FormSpec(
        form_id="NFIP-SFIP-BUNDLE",
        edition="2011-05",
        kind="bundle",
        title="Standard Flood Insurance Policy, all three forms (Dwelling, General Property, RCBAP)",
        source="NFIP Flood Insurance Manual, May 2012 edition, Policy section",
        url="https://www.fema.gov/pdf/nfip/manual201205/content/15_policy.pdf",
        filename="nfip/SFIP_AllForms_2011.pdf",
        license="US Government work, public domain",
        parse_supported=False,
        note="Bundle of three forms with word-per-line text extraction. Needs a splitter and "
        "line re-flow before the clause parser can run. Kept for cross-edition eval questions.",
    ),
]


def get(key: str) -> FormSpec:
    """Look up by `form_id@edition`, or by bare `form_id` when only one edition is registered."""
    if "@" in key:
        for f in FORMS:
            if f.key == key:
                return f
        raise KeyError(key)
    matches = [f for f in FORMS if f.form_id == key]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise KeyError(key)
    raise KeyError(f"{key} has {len(matches)} editions; specify form_id@edition")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(spec: FormSpec, raw_dir: Path, *, force: bool = False) -> Path:
    dest = raw_dir / spec.filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        return dest
    with httpx.stream("GET", spec.url, follow_redirects=True, timeout=60) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
    return dest
