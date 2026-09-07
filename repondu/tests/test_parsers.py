from datetime import datetime

import pytest

from app.scraping import parsers

URL = (
    "https://www.google.com/maps/place/Le+Petit+Bouchon/@45.7640,4.8357,17z/"
    "data=!3m1!4b1!4m6!3m5!1s0x47f4ea516ae88797:0x408ab2ae4bb21f0!8m2!3d45.7641!4d4.8359"
    "!16s%2Fg%2F11c1p3q0zv?entry=ttu"
)


def test_place_id_from_url_hex():
    assert parsers.extract_place_id(URL) == "0x47f4ea516ae88797:0x408ab2ae4bb21f0"


def test_place_id_prefers_chij_from_html():
    html = '... "ChIJd8BlQ2BZwokRAFUEcm_qrcA" ...'
    assert parsers.extract_place_id(URL, html) == "ChIJd8BlQ2BZwokRAFUEcm_qrcA"


def test_place_id_missing():
    assert parsers.extract_place_id("https://www.google.com/maps/search/restaurant") is None


def test_coords_prefer_place_over_view():
    assert parsers.extract_coords(URL) == (45.7641, 4.8359)
    assert parsers.extract_coords("https://x/maps/search/r/@48.85,2.35,15z") == (48.85, 2.35)


def test_place_name_from_url():
    assert parsers.extract_place_name_from_url(URL) == "Le Petit Bouchon"


@pytest.mark.parametrize(
    "text,expected",
    [("4,5", 4.5), ("4.3", 4.3), ("4,5 étoiles", 4.5), ("5", 5.0), ("", None), (None, None)],
)
def test_parse_rating(text, expected):
    assert parsers.parse_rating(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [("(1 234)", 1234), ("(1 234)", 1234), ("1,234 reviews", 1234), ("87 avis", 87), ("", None)],
)
def test_parse_int(text, expected):
    assert parsers.parse_int(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("04 72 00 00 00", "+33472000000"),
        ("+33 4 72 00 00 00", "+33472000000"),
        ("06.12.34.56.78", "+33612345678"),
        ("0033612345678", "+33612345678"),
        ("12345", None),
        ("", None),
    ],
)
def test_normalize_phone(text, expected):
    assert parsers.normalize_phone(text) == expected


def test_is_mobile():
    assert parsers.is_mobile_phone("+33612345678")
    assert parsers.is_mobile_phone("+33712345678")
    assert not parsers.is_mobile_phone("+33472000000")
    assert not parsers.is_mobile_phone(None)


def test_find_phones_dedup_and_order():
    text = "Tel 04 72 00 00 00 / mobile 06 12 34 56 78 / encore 04.72.00.00.00"
    assert parsers.find_phones(text) == ["+33472000000", "+33612345678"]


def test_parse_card_full():
    card = {
        "name": "Le Petit Bouchon",
        "href": URL,
        "rating_text": "4,5",
        "reviews_text": "(1 234)",
        "website": "https://petitbouchon.fr/",
        "lines": [
            "Restaurant lyonnais · 12 Rue Mercière",
            "Ouvert ⋅ Ferme à 23:00",
            "04 72 00 00 00",
        ],
        "text": "Le Petit Bouchon\n4,5(1 234)\nRestaurant lyonnais · 12 Rue Mercière\n"
        "04 72 00 00 00",
    }
    p = parsers.parse_card(card)
    assert p == {
        "place_id": "0x47f4ea516ae88797:0x408ab2ae4bb21f0",
        "name": "Le Petit Bouchon",
        "maps_url": URL,
        "rating": 4.5,
        "review_count": 1234,
        "website": "https://petitbouchon.fr/",
        "phone": "+33472000000",
        "category": "Restaurant lyonnais",
        "address": "12 Rue Mercière",
        "lat": 45.7641,
        "lng": 4.8359,
    }


def test_parse_card_price_segment_ignored():
    card = {"href": URL, "lines": ["Pizzeria · €€ · 3 Rue de la Paix"], "text": ""}
    p = parsers.parse_card(card)
    assert p["category"] == "Pizzeria"
    assert p["address"] == "3 Rue de la Paix"
    assert p["name"] == "Le Petit Bouchon"  # depuis l'URL


def test_parse_card_without_place_id():
    assert parsers.parse_card({"href": "https://www.google.com/maps/search/x"}) is None


def test_parse_place():
    p = parsers.parse_place(
        {
            "name": "Chez Marcel",
            "rating_text": "4,2",
            "reviews_text": "356 avis",
            "category": "Brasserie",
            "phone": "+33 4 78 00 00 00",
            "website": "https://chezmarcel.fr",
            "address": "Adresse: 1 Place Bellecour, 69002 Lyon",
            "url": URL,
        }
    )
    assert p["rating"] == 4.2 and p["review_count"] == 356
    assert p["phone"] == "+33478000000"
    assert p["lat"] == 45.7641


NOW = datetime(2026, 9, 7, 12, 0, 0)


@pytest.mark.parametrize(
    "text,days",
    [
        ("il y a 3 jours", 3),
        ("il y a une semaine", 7),
        ("il y a 2 semaines", 14),
        ("il y a un mois", 30.44),
        ("il y a 2 mois", 60.88),
        ("il y a un an", 365.25),
        ("il y a 5 ans", 5 * 365.25),
        ("Modifié il y a 3 jours", 3),
        ("il y a 4 heures", 0),
        ("2 months ago", 60.88),
        ("a week ago", 7),
        ("a year ago", 365.25),
    ],
)
def test_parse_relative_date(text, days):
    d = parsers.parse_relative_date(text, now=NOW)
    assert d is not None
    assert abs((NOW - d).total_seconds() / 86400 - days) < 0.01


def test_parse_relative_date_unknown():
    assert parsers.parse_relative_date("hier", now=NOW) is None
    assert parsers.parse_relative_date("", now=NOW) is None


def test_parse_review_with_owner_response():
    r = parsers.parse_review(
        {
            "review_id": "ChdDSUhNMG9nS0VJQ0FnSUR",
            "rating_label": "5 étoiles",
            "date_text": "il y a 2 mois",
            "text": "Excellent accueil",
            "author": "Marie D.",
            "owner_response": "Réponse du propriétaire\nil y a un mois\nMerci Marie !",
        },
        now=NOW,
    )
    assert r["rating"] == 5
    assert r["has_owner_response"] == 1
    assert r["owner_response_text"] == "Merci Marie !"
    assert r["date"] and abs((NOW - r["date"]).days - 60) <= 1


def test_parse_review_without_response():
    r = parsers.parse_review(
        {"review_id": "abc", "rating_label": "1 star", "date_text": "a day ago"}
    )
    assert r["rating"] == 1 and r["has_owner_response"] == 0 and r["owner_response_text"] is None


def test_parse_review_no_id():
    assert parsers.parse_review({"review_id": ""}) is None
