# Korean text pitfalls

Read before writing or changing any regex or keyword match over Korean text. The shared helpers live in `scripts/lib/korean.py`; use them instead of writing new patterns.

## Contents
- [Root cause](#root-cause)
- [Pitfall 1 — JS `\b` is not a boundary after Hangul](#pitfall-1--js-b-is-not-a-boundary-after-hangul)
- [Pitfall 2 — substring matches](#pitfall-2--substring-matches)
- [Pitfall 3 — listing conjugated syllables](#pitfall-3--listing-conjugated-syllables)
- [Particles and compounds](#particles-and-compounds)
- [Ambiguous names](#ambiguous-names)
- [Checklist](#checklist)

---

## Root cause

Korean word boundaries are not marked only by spaces, and morphemes fuse inside syllables. Regex idioms built for English fail silently.

## Pitfall 1 — JS `\b` is not a boundary after Hangul

In JavaScript `\b` is defined over `[A-Za-z0-9_]`. Hangul is not a word character, so no boundary exists after it.

```js
/운세\b/.test('[오늘의 운세/8월 21일]')   // false
```

Python differs: `re` treats Hangul as word characters in `str` patterns.

```python
re.search(r'운세\b', '[오늘의 운세/8월 21일]')   # match
re.search(r'운세\b', '[오늘의 운세계획]')        # no match
```

The danger is the same rule written in both languages (browser collectors in JS, gates in Python). Write explicit boundaries:

```js
'(?=[\\s/\\]|·,.\\-–—:：]|$)'
```

## Pitfall 2 — substring matches

Korean reads naturally without spaces, so substrings match by accident.

```js
/오는/.test('카카오는 21일 이사회를 열고')   // true — split as 카카|오는
```

Observed failures: a past board meeting was reported as an upcoming one (`오는` inside `카카오는`); the keyword `가공` matched `국가공무원법` in bill titles.

```python
r'(?<![가-힣])오는'      # no Hangul immediately before
```

`korean.contains_word(text, term, left_guard=True)` does this.

Numbers too: searching `150조` as a substring finds it inside `1500억`. Compare number **token sets** (`evidence.number_tokens`).

## Pitfall 3 — listing conjugated syllables

Past-tense contractions differ by stem:

| Base | Past | Syllable |
|---|---|---|
| 하다 | 했다 | `했` |
| 밝히다 | 밝**혔**다 | `혔` |
| 켜다 | 켰다 | `켰` |
| 펴다 | 폈다 | `폈` |
| 치다 | 쳤다 | `쳤` |

Listing `(했|였|됐)` misses `밝혔다`. The common property is the final consonant ㅆ. For a precomposed syllable, `code = 0xAC00 + (initial × 21 + medial) × 28 + final`, and ㅆ is final index 20 — `korean.has_ss_final(ch)`.

Exceptions: `있다` and `없다` carry ㅆ/ㅄ but are not past tense (`쉴 수 있다`). `있었다` is still caught through `었`.

## Particles and compounds

Particles follow names: `매경이 · 매경은 · 한겨레가 · 조선도`. Blocking all trailing Hangul misses them; allowing particles creates false hits on compounds: `중앙은행 · 중앙정부 · 동아시아 · 조선업 · 조선족`.

Handle both: allow particles, exclude compounds explicitly — `korean.match_name(text, name, exclude_compounds=(...))`.

Meaning sometimes matters: in `조선 사설에 대한 반론이 없다`, 조선 is the object, not the subject of the absence. Requiring a subject/topic particle (은/는/도/이/가) filters it.

## Ambiguous names

Some tracked names are ordinary words. `대상` (the company) is also "target": `중소기업을 대상으로`. A bare match on `대상` is never a company hit. `watch.json` gives such entities `requires_any` (e.g. `청정원`, `대상그룹`); `korean.match_entity` enforces it. Search queries follow the same rule (`대상 AND 청정원`, `"대상그룹"`).

## Checklist

When writing or changing a Korean pattern:

- [ ] Used `\b`? In JS replace it with an explicit boundary; in Python confirm the intent
- [ ] Blocked Hangul before/after? `(?<![가-힣])` / `(?![가-힣])`
- [ ] Listed conjugated syllables? Use the jamo rule instead
- [ ] A particle can follow? Allow it and keep a compound exclusion list
- [ ] Searching numbers as substrings? Compare token sets
- [ ] An ordinary-word name? Require a companion term
- [ ] Checked false hits and misses against real data from more than one period
