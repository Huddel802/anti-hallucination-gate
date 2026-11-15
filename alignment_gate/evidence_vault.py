"""Verify pinned local snapshots and index complete fact lines."""

import re
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple, Optional

from .contracts import (
    Key, Problem, Ref, Value, document, fact_key, identifier, iso_day,
    items, parse_json, plain_text, read_local, shape, typed_value,
)


class Field(NamedTuple):
    kind: str
    unit: Optional[str]
    sum_of: tuple


class Source(NamedTuple):
    id: str
    title: str
    origin: str
    captured_on: str
    sha256: str


class Record(NamedTuple):
    ref: Ref
    key: Key
    value: Value
    origin_ref: str
    exact_line: str


def load_manifest(data):
    document(data, ("fields", "sources"), "Vault")
    fields = {}
    for row in items(data["fields"], "Felder"):
        shape(row, ("id", "kind"), "Feld", ("unit", "sum_of"))
        name = identifier(row["id"], "Feld-ID")
        kind = row["kind"]
        if name in fields or kind not in ("integer", "date", "text"):
            raise Problem("INVALID_POLICY", "Doppeltes Feld oder unbekannter Feldtyp.")
        unit = plain_text(row["unit"], "Einheit", 80) if "unit" in row else None
        if (kind == "integer") != (unit is not None):
            raise Problem("INVALID_POLICY", "Genau Zahlenfelder erfordern eine Einheit.")
        terms = tuple(
            identifier(term, "Summand") for term in items(row["sum_of"], "Summanden")
        ) if "sum_of" in row else ()
        if terms and (len(terms) < 2 or len(set(terms)) != len(terms) or kind != "integer"):
            raise Problem("INVALID_POLICY", "Eine Summe erfordert mindestens zwei verschiedene Zahlenfelder.")
        fields[name] = Field(kind, unit, terms)
    for field in fields.values():
        for term in field.sum_of:
            if fields.get(term) != Field("integer", field.unit, ()):
                raise Problem("INVALID_POLICY", "Summanden müssen direkte Zahlenfelder mit derselben Einheit sein.")

    sources = {}
    for row in items(data["sources"], "Quellen"):
        shape(row, ("id", "title", "origin", "captured_on", "sha256"), "Quelle")
        name = identifier(row["id"], "Quellen-ID")
        digest = row["sha256"]
        if name in sources or not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
            raise Problem("INVALID_POLICY", "Doppelte Quellen-ID oder ungültiger SHA-256.")
        sources[name] = Source(
            name, plain_text(row["title"], "Quellentitel"),
            plain_text(row["origin"], "Herkunft"),
            iso_day(row["captured_on"], "Snapshot-Datum"), digest,
        )
    return fields, sources


class Vault:
    def __init__(self, root: Path, fields, sources, allowed):
        self.fields = fields
        self.sources = {name: sources[name] for name in allowed}
        self.loaded_hashes = {}
        self.by_ref = {}
        self.by_key = defaultdict(list)
        for source in self.sources.values():
            self._load_source(root / "sources" / f"{source.id}.jsonl", source)

    def _load_source(self, path, source):
        try:
            raw, digest = read_local(path)
        except Problem as exc:
            raise Problem(exc.code, f"Snapshot {source.id}. {exc}") from exc
        if digest != source.sha256:
            raise Problem("HASH_MISMATCH", f"Snapshot-Hash stimmt nicht: {source.id}.")
        try:
            # LF is the line separator. CRLF is supported without changing the byte hash.
            lines = raw.decode("utf-8").split("\n")
        except UnicodeError as exc:
            raise Problem("INVALID_SOURCE", f"Snapshot ist kein UTF-8: {source.id}.") from exc
        if lines[-1] == "":
            lines.pop()
        if not lines:
            raise Problem("INVALID_SOURCE", f"Snapshot enthält keine Belegstellen: {source.id}.")
        for number, line in enumerate(lines, 1):
            line = line.removesuffix("\r")
            ref = Ref(source.id, number)
            try:
                row = parse_json(line)
                shape(row, ("subject", "field", "as_of", "value", "origin_ref"), "Belegzeile")
                key = fact_key(row, self.fields)
                value = typed_value(row["value"], self.fields[key.field].kind)
                origin_ref = plain_text(row["origin_ref"], "Originalfundstelle")
            except Problem as exc:
                raise Problem("INVALID_SOURCE", "Snapshot enthält eine ungültige Belegzeile.", (ref,)) from exc
            record = Record(ref, key, value, origin_ref, line)
            self.by_ref[ref] = record
            self.by_key[key].append(record)
        self.loaded_hashes[source.id] = digest

    def cite(self, record, role):
        return {
            **record.ref._asdict(),
            "snapshot": f"sources/{record.ref.source}.jsonl",
            "sha256": self.loaded_hashes[record.ref.source],
            "title": self.sources[record.ref.source].title,
            "origin": self.sources[record.ref.source].origin,
            "origin_ref": record.origin_ref,
            "captured_on": self.sources[record.ref.source].captured_on,
            "role": role,
            "exact_line": record.exact_line,
        }
