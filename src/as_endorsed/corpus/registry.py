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
    FormSpec(
        form_id="TWIA-DWELLING",
        edition="2023-08",
        kind="base",
        title="TWIA Dwelling Policy, Windstorm and Hail",
        source="Texas Windstorm Insurance Association, sample policy (Form TWDP, ed. Aug 31 2023)",
        url="https://www.twia.org/wp-content/uploads/TWIA-Dwelling-Policy-HB-3208.pdf",
        filename="twia/TWIA-Dwelling-Policy.pdf",
        license="Published openly by TWIA at twia.org/forms-sample-policies-endorsements-certificates; check terms before redistribution",
    ),
]

_TWIA = "https://www.twia.org/wp-content/uploads/"
_TWIA_ENDORSEMENTS = [
    ("800", "2015-11", "Amendatory Endorsement (storm doors notice)", "2015/11/800.pdf", "TWIA-800-Amendatory.pdf", "Page 2 is a scanned image; page 1 is a notice"),
    ("810", "2021-04", "Specified Building or Structure Exclusion Endorsement", "2021.04.30-TWIA-Endorsement-810.pdf", "TWIA-810-Specified-Exclusion.pdf", "Fill-in schedule form"),
    ("365", "2019-11", "Replacement Cost Coverage B (Personal Property)", "Endorsement-365_2019.11.08.pdf", "TWIA-365-Replacement-Cost-Personal.pdf", None),
    ("802", "2019-11", "Replacement Cost Coverage A (Dwelling)", "Endorsement-802_2019.11.08.pdf", "TWIA-802-Replacement-Cost-CovA.pdf", None),
    ("804", "2019-11", "Replacement Cost Coverage A (Dwelling), Actual Cash Value Roofs", "Endorsement-804_2019.11.08.pdf", "TWIA-804-RC-CovA-ACV-Roofs.pdf", None),
    ("420", "2015-10", "Cosmetic Damage to Roof Coverings Caused by Hail Exclusion", "2015/10/TWIA-420-Cosmetic-Damage-to-Roof-Coverings-Caused-by-Hail-Exclusion.pdf", "TWIA-420-Cosmetic-Hail-Exclusion.pdf", "Scanned image, no text layer"),
    ("311", "2021-04", "Extension of Coverage, Additional Living Expense", "2021.04.30-TWIA-Endorsement-311.pdf", "TWIA-311-ALE.pdf", None),
    ("321", "2021-04", "Extension of Coverage, Wind-Driven Rain", "2021.04.30-TWIA-Endorsement-321.pdf", "TWIA-321-Wind-Driven-Rain.pdf", None),
    ("331", "2021-09", "Extension of Coverage, Consequential Loss", "2021.09.13-TWIA-Endorsement-331.pdf", "TWIA-331-Consequential-Loss.pdf", None),
    ("431", "2008-04", "Extension of Coverage, Increased Cost of Construction (Residential)", "2015/10/TWIA-431-Increased-Cost-of-Construction-Endorsement-Residential.pdf", "TWIA-431-ICC-Residential.pdf", None),
    ("220", "2012-03", "Automatic Adjusted Building Cost Endorsement", "2015/10/TWIA-220-Automatic-Adjusted-Bulding-Cost-Endorsement.pdf", "TWIA-220-Auto-Adjusted-Building-Cost.pdf", None),
]
for _num, _ed, _title, _path, _fname, _note in _TWIA_ENDORSEMENTS:
    FORMS.append(FormSpec(
        form_id=f"TWIA-{_num}", edition=_ed, kind="endorsement", title=f"TWIA {_num}, {_title}",
        source="Texas Windstorm Insurance Association", url=_TWIA + _path, filename=f"twia/{_fname}",
        license="Published openly by TWIA; check terms before redistribution",
        base_form_id="TWIA-DWELLING", note=_note,
    ))


def endorsements_for(base_form_id: str) -> list[FormSpec]:
    return [f for f in FORMS if f.kind == "endorsement" and f.base_form_id == base_form_id]


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
