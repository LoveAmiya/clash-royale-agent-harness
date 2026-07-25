# Card Alias Policy

`query_parser.py` keeps canonical names identical to `data/cards_meta.json`.
`CARD_COMMUNITY_ALIASES` contains only recognition terms; it never changes the
displayed card name or the source attached to a metric.

## Sources

- Supercell Support is the source of the product terminology for card
  evolution. <https://support.supercell.com/clash-royale/zh/articles/card-evolution-9.html>
- The Chinese Clash Royale Wiki card index is used to cross-check official
  Chinese card names. <https://clashroyale.fandom.com/zh/wiki/%E5%8D%A1%E7%89%8C?variant=zh-cn>
- TapTap and 4399 community glossaries are used only for common player
  abbreviations such as `\u5c0f\u7535`, `\u8d85\u9a91`, `\u86ee\u9524`, `\u8001\u9ad8`, and `\u5973\u67aa`.
  <https://www.taptap.cn/moment/15229676398052378>
  <https://news.4399.com/kpct/xinde/m/787949.html>

## Resolution Rules

1. A card's official canonical key is the English `card_name` in the snapshot.
2. English spaces, dots, underscores, and hyphens are ignored for matching.
   For example, `Mini P.E.K.K.A.`, `mini-pekka`, and `minipekka` resolve to the
   same canonical key.
3. Evolution forms accept `\u8fdb\u5316`, `\u89c9\u9192`, `evo`, and `evolved` before or after
   every base-card alias. Hero forms accept `\u82f1\u96c4`, `hero`, and their
   suffix forms.
4. An alias may belong to only one canonical card after normalization. Tests
   reject collisions. Deliberately ambiguous short forms are either excluded or
   pinned to the community's dominant use: `\u5c0f\u7535` means `Zap`, while
   `\u7535\u7cbe\u7075` and `\u5c0f\u7535\u7cbe\u7075` mean `Electro Spirit`.
5. The model parser is called first. A high-confidence local alias parse is
   retained only when the model returns `reject`, so valid card terms cannot
   disappear because of a parser false negative.
