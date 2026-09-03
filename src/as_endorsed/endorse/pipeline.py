"""Glue between the corpus, the parser, the extractor and the resolver.

Everything here reads and writes the data/ tree so the CLI stays thin and the
same functions serve the tests.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from as_endorsed.config import settings
from as_endorsed.corpus import registry
from as_endorsed.endorse.extract import extract_ops
from as_endorsed.endorse.models import ExtractionResult, ResolvedPolicy
from as_endorsed.endorse.resolve import ScheduledEndorsement, resolve_policy
from as_endorsed.ingest.clauses import parse_form
from as_endorsed.models import FormSpec, ParsedForm
from as_endorsed.synth.accounts import Account
from as_endorsed.synth.endorsements import EDITION as SYN_EDITION, LIBRARY, compare_ops, render_library


def load_parsed(key: str) -> ParsedForm:
    """Parsed base form from data/parsed, parsing the registry PDF if needed."""
    path = settings.parsed_dir / f"{key}.json"
    if path.exists():
        return ParsedForm.model_validate_json(path.read_text(encoding="utf-8"))
    spec = registry.get(key)
    form = parse_form(settings.raw_dir / spec.filename, form_id=spec.form_id, edition=spec.edition, title=spec.title)
    settings.parsed_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(form.model_dump_json(indent=2), encoding="utf-8")
    return form


def parse_endorsement(pdf: Path, *, form_id: str, edition: str, title: str) -> ParsedForm:
    return parse_form(pdf, form_id=form_id, edition=edition, title=title, strict_sequence=False, root_paragraphs=True)


def extract_registry_endorsement(spec: FormSpec, *, use_llm: bool = False) -> ExtractionResult:
    assert spec.kind == "endorsement" and spec.base_form_id
    base = load_parsed(registry.get(spec.base_form_id).key)
    end = parse_endorsement(settings.raw_dir / spec.filename, form_id=spec.form_id, edition=spec.edition, title=spec.title)
    result = extract_ops(end, base)
    if use_llm and not any(o.status == "resolved" for o in result.ops) and not result.scanned:
        from as_endorsed.endorse.llm import extract_ops_llm, llm_available

        if llm_available():
            llm_ops = extract_ops_llm(end, base)
            if llm_ops:
                result.notes.append("rule extraction found nothing resolvable; LLM ops appended")
                result.ops = [o for o in result.ops if o.status != "held" or o.new_text] + llm_ops
    _save_extraction(result)
    return result


def extract_synthetic_library(*, out_dir: Path | None = None) -> list[dict]:
    """Render, parse and extract every synthetic endorsement; return comparison rows."""
    out_dir = out_dir or (settings.synthetic_dir / "endorsements")
    base = load_parsed("NFIP-DWELLING@2021-10")
    pdfs = render_library(out_dir)
    rows: list[dict] = []
    for spec in LIBRARY:
        end = parse_endorsement(pdfs[spec.form_id], form_id=spec.form_id, edition=SYN_EDITION, title=spec.title)
        result = extract_ops(end, base)
        _save_extraction(result)
        rows.extend(compare_ops(spec, result.ops))
    return rows


def _save_extraction(result: ExtractionResult) -> Path:
    settings.endorse_dir.mkdir(parents=True, exist_ok=True)
    path = settings.endorse_dir / f"{result.endorsement_form_id}@{result.endorsement_edition}.json"
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_extraction(key: str) -> ExtractionResult:
    path = settings.endorse_dir / f"{key}.json"
    return ExtractionResult.model_validate_json(path.read_text(encoding="utf-8"))


def load_accounts(path: Path | None = None) -> list[Account]:
    path = path or (settings.synthetic_dir / "accounts.json")
    return [Account.model_validate(a) for a in json.loads(path.read_text(encoding="utf-8"))]


def resolve_account(acct: Account, base: ParsedForm, *, as_of: date | None = None,
                    extractions: dict[str, ExtractionResult] | None = None) -> ResolvedPolicy:
    attached = []
    for i, e in enumerate(acct.policy.endorsement_forms):
        ext = (extractions or {}).get(e.key) or load_extraction(e.key)
        attached.append(ScheduledEndorsement(extraction=ext, effective_date=e.effective_date, order=i, schedule_values=e.schedule_values))
    return resolve_policy(account_id=acct.account_id, base=base, attached=attached, as_of=as_of or acct.policy.term_end)


def resolve_all(*, as_of: date | None = None) -> list[ResolvedPolicy]:
    base = load_parsed("NFIP-DWELLING@2021-10")
    extractions = {e.key: load_extraction(e.key) for e in LIBRARY}
    settings.resolved_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for acct in load_accounts():
        rp = resolve_account(acct, base, as_of=as_of, extractions=extractions)
        (settings.resolved_dir / f"{acct.account_id}.json").write_text(rp.model_dump_json(indent=2), encoding="utf-8")
        out.append(rp)
    return out
