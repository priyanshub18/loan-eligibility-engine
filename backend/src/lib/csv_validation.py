"""CSV header mapping and per-row validation for the user upload.

Kept free of any AWS / DB imports so it is trivially unit-testable.
A bad row never fails the whole upload: it is rejected with a reason and
counted, and a small sample of rejections is surfaced in the UI.
"""
import re
from dataclasses import dataclass

REQUIRED_COLUMNS = ("user_id", "email", "monthly_income", "credit_score", "employment_status", "age")
OPTIONAL_COLUMNS = ("name",)

# Tolerate the usual header variations people produce in spreadsheets.
HEADER_ALIASES = {
    "id": "user_id",
    "userid": "user_id",
    "email_address": "email",
    "income": "monthly_income",
    "salary": "monthly_income",
    "monthly_salary": "monthly_income",
    "cibil": "credit_score",
    "cibil_score": "credit_score",
    "employment": "employment_status",
    "employment_type": "employment_status",
}

# Canonical employment values used everywhere downstream (DB + n8n matching).
EMPLOYMENT_MAP = {
    "salaried": "salaried",
    "employed": "salaried",
    "full-time": "salaried",
    "self-employed": "self_employed",
    "self employed": "self_employed",
    "self_employed": "self_employed",
    "selfemployed": "self_employed",
    "self-employed professional": "self_employed",
    "business": "business",
    "business owner": "business",
    "self-employed business": "business",
    "unemployed": "unemployed",
    "student": "other",
    "retired": "other",
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class HeaderError(ValueError):
    """The file cannot be processed at all (missing required columns)."""


class RowError(ValueError):
    """A single row is invalid; it is skipped and reported."""


@dataclass(frozen=True)
class UserRow:
    user_id: str
    name: str | None
    email: str
    monthly_income: float
    credit_score: int
    employment_status: str
    age: int


def _canonical(header: str) -> str:
    key = re.sub(r"[\s\-]+", "_", header.strip().lower())
    return HEADER_ALIASES.get(key, key)


def map_header(header_row: list[str]) -> dict[str, int]:
    """Return {canonical_column: index}. Raises HeaderError if anything required is missing."""
    mapping: dict[str, int] = {}
    for idx, col in enumerate(header_row):
        name = _canonical(col)
        if name in REQUIRED_COLUMNS + OPTIONAL_COLUMNS and name not in mapping:
            mapping[name] = idx
    missing = [c for c in REQUIRED_COLUMNS if c not in mapping]
    if missing:
        raise HeaderError(f"CSV is missing required column(s): {', '.join(missing)}")
    return mapping


def _number(value: str, field: str) -> float:
    cleaned = value.replace(",", "").replace("₹", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        raise RowError(f"{field} is not a number: {value!r}") from None


def parse_row(row: list[str], mapping: dict[str, int]) -> UserRow:
    def get(col: str) -> str:
        idx = mapping.get(col)
        return row[idx].strip() if idx is not None and idx < len(row) else ""

    user_id = get("user_id")
    if not user_id:
        raise RowError("user_id is empty")

    email = get("email").lower()
    if not _EMAIL_RE.match(email):
        raise RowError(f"invalid email: {email!r}")

    income = _number(get("monthly_income"), "monthly_income")
    if income < 0:
        raise RowError("monthly_income is negative")

    score = _number(get("credit_score"), "credit_score")
    if not 300 <= score <= 900:
        raise RowError(f"credit_score out of range 300-900: {score:g}")

    age = _number(get("age"), "age")
    if not 18 <= age <= 100:
        raise RowError(f"age out of range 18-100: {age:g}")

    raw_emp = get("employment_status").lower()
    employment = EMPLOYMENT_MAP.get(raw_emp)
    if employment is None:
        raise RowError(f"unknown employment_status: {raw_emp!r}")

    return UserRow(
        user_id=user_id,
        name=get("name") or None,
        email=email,
        monthly_income=round(income, 2),
        credit_score=int(score),
        employment_status=employment,
        age=int(age),
    )
