import pytest

from src.lib.csv_validation import HeaderError, RowError, map_header, parse_row

HEADER = ["user_id", "name", "email", "monthly_income", "credit_score", "employment_status", "age"]


def test_header_aliases_and_case():
    mapping = map_header(["ID", "Email Address", "Salary", "CIBIL Score", "Employment Type", "Age"])
    assert mapping == {"user_id": 0, "email": 1, "monthly_income": 2, "credit_score": 3,
                       "employment_status": 4, "age": 5}


def test_missing_required_column():
    with pytest.raises(HeaderError, match="credit_score"):
        map_header(["user_id", "email", "monthly_income", "employment_status", "age"])


def test_valid_row_is_normalised():
    m = map_header(HEADER)
    row = parse_row(["u1", "Tara", "Tara@Example.com ", "38,000", "765", "Self-Employed", "36"], m)
    assert row.email == "tara@example.com"
    assert row.monthly_income == 38000
    assert row.employment_status == "self_employed"
    assert row.credit_score == 765 and row.age == 36


@pytest.mark.parametrize("field_idx,value,msg", [
    (0, "", "user_id"),
    (2, "not-an-email", "email"),
    (3, "abc", "monthly_income"),
    (4, "1200", "credit_score"),
    (5, "Astronaut", "employment_status"),
    (6, "12", "age"),
])
def test_invalid_rows_are_rejected(field_idx, value, msg):
    m = map_header(HEADER)
    row = ["u1", "X", "x@example.com", "50000", "700", "Salaried", "30"]
    row[field_idx] = value
    with pytest.raises(RowError, match=msg):
        parse_row(row, m)

