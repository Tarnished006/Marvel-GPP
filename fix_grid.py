import codecs

with codecs.open('screens/dashboard.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace(
    "visible_cards = [c for c in self.card_widgets if c.isVisibleTo(self.scroll_content) or not c.isHidden()]",
    "visible_cards = [c for c in self.card_widgets if getattr(c, '_matches_search', True)]"
)

content = content.replace(
    "card.setVisible(matches_search and matches_filter)",
    "card._matches_search = (matches_search and matches_filter)\n            card.setVisible(card._matches_search)"
)

with codecs.open('screens/dashboard.py', 'w', encoding='utf-8') as f:
    f.write(content)
