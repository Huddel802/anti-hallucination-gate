"""Check all in-scope snapshots for conflicts and compute declared sums."""

from .contracts import MAX_INT, Problem


def _check_conflicts(records):
    if len({record.value for record in records}) > 1:
        raise Problem("SOURCE_CONFLICT", "Freigegebene Quellen widersprechen sich beim selben Zielschlüssel.",
                      tuple(record.ref for record in records))


def check_consistency(claim, support, vault):
    key = claim.key
    field = vault.fields[key.field]
    for supported_key in support:
        _check_conflicts(vault.by_key[supported_key])
    citations = [
        (record, "operand" if field.sum_of else "support")
        for records in support.values() for record in records
    ]
    if field.sum_of:
        value = sum(records[0].value for records in support.values())
        if abs(value) > MAX_INT:
            raise Problem("CALCULATION_RANGE", "Geprüfte Summe liegt außerhalb des unterstützten Zahlenbereichs.")
        stated_totals = vault.by_key[key]
        _check_conflicts(stated_totals)
        if any(record.value != value for record in stated_totals):
            raise Problem("TOTAL_CONFLICT", "Die ausgewiesene Summe weicht von der nachgerechneten ab.",
                          tuple(record.ref for record in stated_totals) + claim.evidence)
        citations.extend((record, "cross_check") for record in stated_totals)
    else:
        value = support[key][0].value
    return value, citations
