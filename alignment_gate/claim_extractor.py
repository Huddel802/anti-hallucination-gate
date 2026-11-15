"""Extract all atomic assertions from the only accepted draft format."""

from typing import NamedTuple

from .contracts import (
    Key, Problem, Ref, Value, document, fact_key, identifier, items, shape, typed_value,
)


class Claim(NamedTuple):
    id: str
    key: Key
    value: Value
    evidence: tuple


def extract_claims(data, fields):
    document(data, ("claims",), "Entwurf")
    claims = {}
    ids = set()
    for row in items(data["claims"], "Claims", allow_empty=True):
        shape(row, ("id", *Key._fields, "value", "evidence"), "Claim")
        name = identifier(row["id"], "Claim-ID")
        key = fact_key(row, fields)
        if name in ids or key in claims:
            raise Problem("DUPLICATE_CLAIM", "Claim-ID oder Zielschlüssel wurde mehrfach eingereicht.")
        value = typed_value(row["value"], fields[key.field].kind)
        references = []
        for item in items(row["evidence"], "Belege", allow_empty=True):
            shape(item, Ref._fields, "Belegverweis")
            line = item["line"]
            if type(line) is not int or line < 1:
                raise Problem("INVALID_SCHEMA", "Belegzeile muss eine positive Ganzzahl sein.")
            references.append(Ref(identifier(item["source"], "Quellen-ID"), line))
        if len(set(references)) != len(references):
            raise Problem("DUPLICATE_CITATION", "Ein Belegverweis wurde innerhalb eines Claims wiederholt.")
        claims[key] = Claim(name, key, value, tuple(references))
        ids.add(name)
    return claims
