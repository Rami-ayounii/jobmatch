from app.core.security import hash_field


def test_hash_normalizes_whitespace():
    assert hash_field("Jane Doe") == hash_field("  Jane Doe  ")


def test_hash_normalizes_case():
    assert hash_field("Jane Doe") == hash_field("JANE DOE")
    assert hash_field("jane@example.com") == hash_field("JANE@EXAMPLE.COM")


def test_hash_different_values_differ():
    assert hash_field("Jane Doe") != hash_field("John Doe")
    assert hash_field("a@b.com") != hash_field("a@c.com")


def test_hash_returns_64_char_hex():
    result = hash_field("test")
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)
