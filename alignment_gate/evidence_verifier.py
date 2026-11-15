"""Resolve exact references and check typed source support."""

from collections import defaultdict

from .contracts import Problem
from .prompt_compiler import required_keys


def verify_evidence(claim, vault):
    needed = required_keys(claim.key, vault.fields[claim.key.field])
    support = defaultdict(list)
    for ref in claim.evidence:
        record = vault.by_ref.get(ref)
        if record is None:
            raise Problem("UNKNOWN_CITATION", "Belegzeile fehlt im freigegebenen Quellenbestand.", (ref,))
        if record.key not in needed:
            raise Problem("IRRELEVANT_CITATION", "Beleg passt nicht zu Gegenstand, Feld oder Stichtag.", (ref,))
        support[record.key].append(record)
    if support.keys() != set(needed):
        raise Problem("MISSING_CITATION", "Mindestens eine benötigte Belegstelle fehlt.", claim.evidence)
    return support


def require_supported_value(claim, value):
    if claim.value != value:
        raise Problem("VALUE_MISMATCH", "Claimwert weicht vom belegten oder berechneten Wert ab.", claim.evidence)
