from __future__ import annotations
import calendar
import hashlib
import json
from datetime import date, timedelta
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any
import numpy as np
from jsonschema import Draft202012Validator, FormatChecker
from .errors import FDTError


def canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError) as e:
        raise FDTError('INVALID_JSON_VALUE', str(e)) from e


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


@lru_cache(maxsize=10)
def validator(name: str) -> Draft202012Validator:
    schema = json.loads(files('fdt').joinpath('schemas', f'{name}.json').read_text(encoding='utf-8'))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate(name: str, value: Any) -> None:
    canonical(value)  # Python floats permit NaN; JSON does not.
    errors = sorted(validator(name).iter_errors(value), key=lambda e: str(list(e.path)))
    if errors:
        err = errors[0]
        raise FDTError('SCHEMA_VALIDATION', f'{name}: {err.message}', {'path': list(err.path)})


def read_json(path: str | Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'), parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
    except (OSError, ValueError) as e:
        raise FDTError('JSON_READ_ERROR', str(e)) from e


def write_json(path: str | Path, value: Any) -> None:
    canonical(value)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def month_date(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def next_month(d: date, day: int) -> date:
    return month_date(d.year + (d.month == 12), d.month % 12 + 1, day)


def quantiles(values: np.ndarray) -> dict[str, int]:
    return {f'p{p}': int(round(float(np.percentile(values, p)))) for p in (10, 50, 90)}


def probability(mask: np.ndarray) -> float:
    return round(float(np.mean(mask)), 6)


def warning(code: str, message: str, **details: Any) -> dict:
    return {'code': code, 'message': message, 'details': details}
