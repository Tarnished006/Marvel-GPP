import codecs

with codecs.open('screens/dashboard.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace(
    "self.grid.addWidget(card, row, col)",
    "self.grid.addWidget(card, row, col)\n            card.show()"
)

with codecs.open('screens/dashboard.py', 'w', encoding='utf-8') as f:
    f.write(content)
