import pytest
from packages.validation.normalizer import (
    extract_domain,
    normalize_company_name,
    normalize_email,
    normalize_phone,
    normalize_whitespace,
)

def test_normalize_whitespace():
    raw = "  Hello \t\t world! \r\n\r\n\r\n Line 2   "
    clean = normalize_whitespace(raw)
    assert clean == "Hello world!\n\nLine 2"

def test_normalize_email():
    assert normalize_email("  Amelia.Grant@HumeLogistics.Example  ") == "amelia.grant@humelogistics.example"
    assert normalize_email("invalid-email-address") is None
    assert normalize_email(None) is None

def test_normalize_phone_australian():
    assert normalize_phone("0400111020") == "0400 111 020"
    assert normalize_phone("0400 111 020") == "0400 111 020"
    assert normalize_phone("+61 400 111 020") == "0400 111 020"
    assert normalize_phone("61400111020") == "0400 111 020"
    assert normalize_phone(None) is None

def test_extract_domain():
    assert extract_domain("amelia@humelogistics.example") == "humelogistics.example"
    assert extract_domain("https://www.northbankcollege.example/contact") == "northbankcollege.example"
    assert extract_domain(None) is None

def test_normalize_company_name():
    assert normalize_company_name("Hume Logistics pty ltd") == "Hume Logistics Pty Ltd"
    assert normalize_company_name("Greenfields Foods PTY. LTD.") == "Greenfields Foods Pty Ltd"
