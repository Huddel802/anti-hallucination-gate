"""Strict JSON boundaries and shared types for the seven modules."""

import hashlib
import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import NamedTuple, Union

MAX_FILE = 1024 * 1024
MAX_COUNT = 100
MAX_INT = 10**15 - 1
Value = Union[int, str]


class Problem(Exception):
    def __init__(self, code, message, references=()):
        super().__init__(message)
        self.code = code
        self.references = references


class Key(NamedTuple):
    subject: str
    field: str
    as_of: str


class Ref(NamedTuple):
    source: str
    line: int


def read_local(path: Path):
    try:
        if not path.is_file():
            raise OSError("expected regular file")
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE + 1)
    except OSError as exc:
        raise Problem("FILE_UNAVAILABLE", "Lokale Datei fehlt oder ist nicht lesbar.") from exc
    if len(raw) > MAX_FILE:
        raise Problem("FILE_TOO_LARGE", "Datei überschreitet 1 MiB.")
    return raw, hashlib.sha256(raw).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _integer(raw):
    if len(raw.lstrip("-")) > 15:
        raise ValueError("integer out of range")
    return int(raw)


def _unsupported_number(_):
    raise ValueError("unsupported JSON number")


def parse_json(raw):
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(
            raw, object_pairs_hook=_object, parse_int=_integer,
            parse_float=_unsupported_number, parse_constant=_unsupported_number,
        )
    except (ValueError, RecursionError) as exc:
        raise Problem("INVALID_JSON", "Ungültiges UTF-8-JSON, doppelte Schlüssel oder unzulässige Zahl.") from exc


def shape(data, required, label, optional=()):
    if not isinstance(data, dict) or not set(required) <= data.keys() <= set(required) | set(optional):
        raise Problem("INVALID_SCHEMA", f"{label}: Fehlende oder unerwartete Felder.")


def document(data, fields, label, optional=()):
    shape(data, ("schema", *fields), label, optional)
    if type(data["schema"]) is not int or data["schema"] != 1:
        raise Problem("INVALID_SCHEMA", f"{label}: Erwartet wird schema 1.")


def items(value, label, *, allow_empty=False):
    if not isinstance(value, list) or len(value) > MAX_COUNT or (not value and not allow_empty):
        raise Problem("INVALID_SCHEMA", f"{label}: Liste mit höchstens {MAX_COUNT} Einträgen erwartet.")
    return value


def plain_text(value, label, limit=1000):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in value)):
        raise Problem("INVALID_SCHEMA", f"{label}: Nichtleerer, einzeiliger Text erwartet.")
    return value


def identifier(value, label):
    if not isinstance(value, str) or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value) is None:
        raise Problem("INVALID_SCHEMA", f"{label}: Ungültige Kennung.")
    return value


def iso_day(value, label):
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    raise Problem("INVALID_SCHEMA", f"{label}: Gültiges Datum YYYY-MM-DD erwartet.")


def typed_value(value, kind):
    if kind == "integer" and type(value) is int and abs(value) <= MAX_INT:
        return value
    if kind == "date":
        return iso_day(value, "Wert")
    if kind == "text":
        return plain_text(value, "Wert")
    raise Problem("INVALID_SCHEMA", "Wert passt nicht zum deklarierten Feldtyp.")


def fact_key(data, fields):
    key = Key(
        identifier(data["subject"], "subject"),
        identifier(data["field"], "field"),
        iso_day(data["as_of"], "as_of"),
    )
    if key.field not in fields:
        raise Problem("UNKNOWN_FIELD", "Feld ist im Evidence Vault nicht definiert.")
    return key
