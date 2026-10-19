from umpyr_engine.ingest.pii import PIIMasker, mask_text


def test_masks_email_phone_card_address():
    t = (
        "Hi, I'm Priya Natarajan. Reach me at priya.n@example.com or (415) 555-0133. "
        "I paid with 4111 1111 1111 1111 and live at 1234 Maple Street, Apt 5."
    )
    m = PIIMasker().mask(t)
    assert "[EMAIL]" in m.text and "[PHONE]" in m.text and "[CARD]" in m.text and "[ADDRESS]" in m.text
    assert "priya" not in m.text.lower()
    assert m.counts["email"] == 1 and m.counts["card"] == 1


def test_non_luhn_number_is_not_a_card_and_order_numbers_survive():
    t = "Order R-100233445 tracking 1Z999AA10123456784 total 12345678901234"
    out = mask_text(t)
    assert "[CARD]" not in out
    assert "R-100233445" in out


def test_salutation_and_known_names():
    t = "Dear Marcus,\nThanks for your help.\nRegards,\nMarcus Delacroix"
    out = PIIMasker(known_names=["Marcus Delacroix"]).mask(t).text
    assert "Marcus" not in out and "Delacroix" not in out
    assert out.count("[NAME]") >= 2


def test_common_words_not_masked_when_lowercase():
    out = mask_text("please mark the box and will you check the bill")
    assert "[NAME]" not in out


def test_empty_and_none():
    assert mask_text(None) == ""
    assert mask_text("") == ""
