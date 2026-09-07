"""Sélecteurs et extracteurs JS pour Google Maps.

Seul endroit du code qui connaît le DOM de Google Maps. Si Google change ses classes,
c'est ici (et dans tests/fixtures/) qu'il faut intervenir.
"""

# --- Page de consentement (consent.google.com) ---------------------------------------------
CONSENT_URL_FRAGMENT = "consent.google.com"
CONSENT_BUTTONS = [
    'button:has-text("Tout refuser")',
    'button:has-text("Reject all")',
    'button:has-text("Tout accepter")',
    'button:has-text("Accept all")',
    'form[action*="consent"] button',
]

# --- Liste de résultats --------------------------------------------------------------------
RESULTS_FEED = 'div[role="feed"]'
RESULT_CARD = "div.Nv2PK"
RESULT_LINK = 'a[href*="/maps/place/"]'
END_OF_LIST_TEXTS = ("Vous êtes arrivé à la fin de la liste", "You've reached the end of the list")

# Extrait les cartes de la liste sous forme de dicts bruts (le parsing est fait côté Python).
EXTRACT_CARDS_JS = r"""
() => {
  const feed = document.querySelector('div[role="feed"]');
  if (!feed) return [];
  const cards = feed.querySelectorAll('div.Nv2PK');
  const out = [];
  for (const card of cards) {
    const link = card.querySelector('a[href*="/maps/place/"]');
    if (!link) continue;
    const rating = card.querySelector('span.MW4etd');
    const count = card.querySelector('span.UY7F9');
    const site = card.querySelector('a[data-value="Site Web"], a[data-value="Website"]');
    const lines = Array.from(card.querySelectorAll('div.W4Efsd'))
      .map(e => e.innerText.trim()).filter(Boolean);
    out.push({
      name: link.getAttribute('aria-label') || '',
      href: link.getAttribute('href') || '',
      rating_text: rating ? rating.innerText.trim() : '',
      reviews_text: count ? count.innerText.trim() : '',
      website: site ? (site.getAttribute('href') || '') : '',
      lines: lines,
      text: card.innerText,
    });
  }
  return out;
}
"""

END_OF_LIST_JS = r"""
() => {
  const feed = document.querySelector('div[role="feed"]');
  if (!feed) return false;
  const t = feed.innerText;
  return t.includes("Vous êtes arrivé à la fin de la liste")
      || t.includes("You've reached the end of the list");
}
"""

SCROLL_FEED_JS = r"""
() => {
  const feed = document.querySelector('div[role="feed"]');
  if (!feed) return 0;
  feed.scrollBy(0, feed.scrollHeight);
  return feed.scrollHeight;
}
"""

# --- Fiche établissement ----------------------------------------------------------------------
PLACE_TITLE = "h1"
EXTRACT_PLACE_JS = r"""
() => {
  const q = (sel) => document.querySelector(sel);
  const txt = (el) => el ? el.innerText.trim() : '';
  const attr = (el, a) => el ? (el.getAttribute(a) || '') : '';
  const h1 = q('h1');
  const rating = q('div.F7nice span[aria-hidden="true"]');
  const count = q('div.F7nice span[aria-label*="avis"], div.F7nice span[aria-label*="reviews"]');
  const category = q('button[jsaction*="category"]');
  const phone = q('button[data-item-id^="phone:tel:"]');
  const site = q('a[data-item-id="authority"]');
  const address = q('button[data-item-id="address"]');
  return {
    name: txt(h1),
    rating_text: txt(rating),
    reviews_text: count ? (count.getAttribute('aria-label') || '') : '',
    category: txt(category),
    phone: phone ? (phone.getAttribute('data-item-id') || '').replace('phone:tel:', '') : '',
    website: attr(site, 'href'),
    address: attr(address, 'aria-label') || txt(address),
    url: location.href,
  };
}
"""

# --- Onglet Avis ------------------------------------------------------------------------------
REVIEWS_TAB_BUTTONS = [
    'button[role="tab"]:has-text("Avis")',
    'button[role="tab"]:has-text("Reviews")',
]
REVIEWS_SORT_BUTTONS = [
    'button[aria-label="Trier les avis"]',
    'button[aria-label="Sort reviews"]',
]
REVIEWS_SORT_NEWEST = [
    'div[role="menuitemradio"]:has-text("Les plus récents")',
    'div[role="menuitemradio"]:has-text("Newest")',
]
REVIEWS_SCROLLABLE = 'div[role="main"] div.m6QErb.DxyBCb'
REVIEW_ITEM = "div[data-review-id]"
REVIEW_MORE_BUTTONS = 'button[aria-label="Voir plus"], button[aria-label="See more"]'

EXTRACT_REVIEWS_JS = r"""
() => {
  const items = document.querySelectorAll('div[data-review-id]');
  const out = [];
  const seen = new Set();
  for (const el of items) {
    const id = el.getAttribute('data-review-id');
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const star = el.querySelector(
      'span[role="img"][aria-label*="toile"], span[role="img"][aria-label*="star"]');
    const date = el.querySelector('span.rsqaWe, span.DU9Pgb');
    const text = el.querySelector('span.wiI7pd');
    const author = el.querySelector('div.d4r55');
    const owner = el.querySelector('div.CDe7pd');
    out.push({
      review_id: id,
      rating_label: star ? (star.getAttribute('aria-label') || '') : '',
      date_text: date ? date.innerText.trim() : '',
      text: text ? text.innerText.trim() : '',
      author: author ? author.innerText.trim() : '',
      owner_response: owner ? owner.innerText.trim() : '',
      raw: el.innerText,
    });
  }
  return out;
}
"""

SCROLL_REVIEWS_JS = r"""
() => {
  const el = document.querySelector('div[role="main"] div.m6QErb.DxyBCb')
          || document.querySelector('div[role="main"]');
  if (!el) return 0;
  el.scrollBy(0, el.scrollHeight);
  return el.scrollHeight;
}
"""
