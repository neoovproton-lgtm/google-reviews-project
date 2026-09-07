from pathlib import Path

from app.enrichment.extract import Extraction, extract_page

SITE = Path(__file__).parent / "fixtures" / "site"
HOME = "https://www.chezfixture.example/"


def test_home_page_extraction():
    ex = extract_page((SITE / "index.html").read_text(), HOME)
    assert ex.emails == []  # sentry (script) et logo@2x.png filtrés
    assert ex.contact_form_url is None  # formulaire de recherche ignoré
    assert ex.contact_links == ["https://www.chezfixture.example/contact"]
    assert ex.instagram == "chezfixture"  # /p/abc123 ignoré
    assert ex.facebook == "ChezFixtureLyon"  # sharer ignoré
    assert ex.phones == ["+33478000010", "+33612345678"]
    assert ex.mobile_phone == "+33612345678"


def test_contact_page_extraction_and_email_ranking():
    ex = extract_page((SITE / "contact.html").read_text(), HOME + "contact")
    assert ex.emails == [
        "contact@chezfixture.example",
        "resa@chezfixture.example",  # obfusqué [at] [dot]
        "marie.dupont@chezfixture.example",
    ]
    assert ex.contact_form_url == HOME + "contact"


def test_email_ranking_prefers_own_domain():
    html = '<a href="mailto:contact@gmail.com">m</a> <p>info@monresto.fr bob@monresto.fr</p>'
    ex = extract_page(html, "https://monresto.fr/")
    assert ex.emails == ["info@monresto.fr", "contact@gmail.com", "bob@monresto.fr"]


def test_socials_from_raw_html_when_not_links():
    html = '<div data-url="https://instagram.com/le_resto.lyon"></div><p>fb.com</p>'
    ex = extract_page(html, "https://x.fr/")
    assert ex.instagram == "le_resto.lyon" and ex.facebook is None


def test_facebook_pages_and_profile_forms():
    pages = '<a href="https://www.facebook.com/pages/Le-Resto/123456">f</a>'
    assert extract_page(pages, "https://x.fr/").facebook == "pages/Le-Resto/123456"
    profile = '<a href="https://facebook.com/profile.php?id=42">f</a>'
    assert extract_page(profile, "https://x.fr/").facebook == "profile.php?id=42"


def test_merge_keeps_first_and_appends():
    a = Extraction(emails=["a@x.fr"], instagram="one", phones=["+33600000001"])
    b = Extraction(
        emails=["b@x.fr", "a@x.fr"],
        instagram="two",
        facebook="fb",
        contact_form_url="https://x.fr/contact",
        phones=["+33600000001", "+33100000000"],
    )
    a.merge(b)
    assert a.emails == ["a@x.fr", "b@x.fr"] and a.instagram == "one" and a.facebook == "fb"
    assert a.contact_form_url == "https://x.fr/contact"
    assert a.phones == ["+33600000001", "+33100000000"]


def test_tel_links_are_phones():
    ex = extract_page('<a href="tel:+33612345678">appeler</a>', "https://x.fr/")
    assert ex.mobile_phone == "+33612345678"
