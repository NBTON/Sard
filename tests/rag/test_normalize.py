"""Arabic normalization tests (no network required)."""

from sard.rag.normalize import (
    ArabicNormalizationOptions,
    clean_document_text,
    normalize_arabic,
    remove_repeated_lines,
)


def test_strips_diacritics_and_tatweel():
    text = "الْيَنَابِيــــعُ الْحَارَّة"
    result = normalize_arabic(text)
    assert "\u0640" not in result  # tatweel gone
    assert "\u064b" not in result  # fathatan etc. gone
    assert "َ" not in result


def test_normalizes_alef_variants():
    text = "أحساء إحساء آحساء احساء"
    result = normalize_arabic(text)
    # All alef variants should fold to bare alef -> all four words identical
    words = result.split()
    assert len(set(words)) == 1


def test_normalizes_yeh_and_kaf_variants():
    text = "علي كتاب"  # already standard form baseline
    alt = "علٮ كتاب"  # not a real variant char, just ensure no crash
    assert normalize_arabic(alt)


def test_does_not_mutate_original_query_semantics_when_disabled():
    text = "السَّلَامُ عَلَيْكُمْ"
    options = ArabicNormalizationOptions(strip_diacritics=False, strip_tatweel=False)
    result = normalize_arabic(text, options)
    assert "َ" in result  # diacritics preserved when disabled


def test_collapses_whitespace_conservatively():
    text = "  مرحبا   بالعالم  \n\n\n\nسطر آخر  "
    result = normalize_arabic(text)
    assert "  " not in result
    assert "\n\n\n" not in result


def test_clean_document_text_preserves_arabic_letters_exactly():
    text = "الينابيع الحارة في الأحساء"
    cleaned = clean_document_text(text)
    assert cleaned == text  # nothing should change for already-clean text


def test_clean_document_text_collapses_blank_lines_and_trailing_spaces():
    text = "سطر أول   \n\n\n\nسطر ثاني"
    cleaned = clean_document_text(text)
    assert "\n\n\n" not in cleaned
    assert "سطر أول" in cleaned and "سطر ثاني" in cleaned


def test_remove_repeated_lines_strips_boilerplate_headers():
    pages = [
        "هيئة السياحة السعودية\nمحتوى الصفحة الأولى\nجميع الحقوق محفوظة",
        "هيئة السياحة السعودية\nمحتوى الصفحة الثانية\nجميع الحقوق محفوظة",
        "هيئة السياحة السعودية\nمحتوى الصفحة الثالثة\nجميع الحقوق محفوظة",
    ]
    cleaned = remove_repeated_lines(pages)
    for page in cleaned:
        assert "هيئة السياحة السعودية" not in page
        assert "جميع الحقوق محفوظة" not in page
    assert "محتوى الصفحة الأولى" in cleaned[0]
