# Korean text-handling traps

Read this before changing any regex or pattern in the pipeline. This project fell into **traps with the same root three times**.

## Contents
- [Root cause](#root-cause)
- [Trap 1 — JS `\b` is not a boundary after Hangul](#trap-1--js-b-is-not-a-boundary-after-hangul)
- [Trap 2 — substring matches](#trap-2--substring-matches)
- [Trap 3 — listing conjugated forms by syllable](#trap-3--listing-conjugated-forms-by-syllable)
- [Handling particles](#handling-particles)
- [Checklist](#checklist)

---

## Root cause

In Korean, **word boundaries are not marked by spaces alone, and forms fuse inside a syllable.**
Regex idioms made for English drift quietly out of line. All three traps came from this.

## Trap 1 — JS `\b` is not a boundary after Hangul

The JS regex `\b` is based on `[A-Za-z0-9_]`. Hangul is not a word character,
so no word boundary holds after a Hangul character.

```js
/운세\b/.test('[오늘의 운세/8월 21일]')   // false — no match
```

**Python is different.** For a str pattern, `re` treats Hangul as word characters.

```python
re.search(r'운세\b', '[오늘의 운세/8월 21일]')   # matches
re.search(r'운세\b', '[오늘의 운세계획]')        # no match (as intended)
```

**This is especially dangerous when the same rule is used in both languages.** `collect.js` and `cluster.py`
hold the same column-detection rule, and only one side drifted.

### Fix

State the boundary explicitly.

```js
'(?=[\\s/\\]|·,.\\-–—:：]|$)'      // JS
```

## Trap 2 — substring matches

Korean reads naturally even without spaces, so a substring is easily caught.

```js
/오는/.test('카카오는 21일 이사회를 열고')   // true — cut as '카카|오는'
```

The real accident: an 8월 21일 board meeting, a past fact, was put in the briefing **as a scheduled event for 9월 21일**.
「나오는」, 「보이는」, 「들어오는」, 「사오는」 and countless others are the same trap.

### Fix

```python
r'(?<![가-힣])오는'        # no match when a Hangul character comes before
```

Numbers are the same. Searching by substring finds 「150조」 inside 「1500억」 in the body.
-> Compare as token sets.

## Trap 3 — listing conjugated forms by syllable

The Korean past tense contracts in a different way for each stem.

| Base form | Past form | Syllable |
|---|---|---|
| 하다 | 했다 | `했` |
| 밝히다 | 밝**혔**다 | `혔` |
| 켜다 | 켰다 | `켰` |
| 펴다 | 폈다 | `폈` |
| 치다 | 쳤다 | `쳤` |

Writing only `(했|였|됐)` misses 「밝혔다」 completely. This did cause a large number of missed past-tense judgments.

### Fix — judge at the jamo level

The only thing they have in common is **the final consonant ㅆ**. A composed Hangul syllable is

```
code = 0xAC00 + (initial × 21 + medial) × 28 + final
```

so compute the final-consonant index directly. ㅆ is number 20.

```python
def _has_ss_final(ch):
    c = ord(ch)
    return 0xAC00 <= c <= 0xD7A3 and (c - 0xAC00) % 28 == 20
```

**Do not forget the exceptions.** 「있다」 and 「없다」 end in ㅆ/ㅄ but are not past forms.
「쉴 수 있다」 was caught as past and a perfectly good schedule item was thrown away.
(「있었다」 is still caught as it is, because its previous character is `었`.)

## Handling particles

A particle attaches after an outlet name or proper noun. Blocking every following Hangul character cannot catch them.

```
매경이 · 매경은 · 한겨레가 · 조선도
```

But if particles are allowed, compounds are caught as false positives.

```
중앙은행 · 중앙정부 · 동아시아 · 조선업 · 조선족
```

**Both must be handled.** Allow the particle, and explicitly exclude the compounds.

```python
PARTICLE = r'(?:은|는|이|가|을|를|의|도|만|과|와|에|에서|으로|로)?'
NOT_OUTLET = re.compile(r'^(중앙(은행|정부|회|당|…)|동아(시아|…)|조선(족|업|…)|…)')
```

Sometimes the meaning matters too. In 「조선 사설에 대한 반론이 없다」 (there is no rebuttal to the 조선 editorial), 조선 is
**the target of the absence, not its subject**. Requiring a subject or topic particle (은/는/도/이/가) filters it out.

## Checklist

When writing or changing a Hangul pattern:

- [ ] Did you use `\b`? -> In JS replace it with an explicit boundary. In Python too, confirm the intent.
- [ ] Did you block Hangul before and after? -> `(?<![가-힣])` / `(?![가-힣])`
- [ ] Did you list conjugated forms by syllable? -> Check whether a jamo rule can replace it.
- [ ] Is it a place where a particle attaches? -> Allow it, and keep an exclusion list for compounds.
- [ ] Do you search numbers by substring? -> Use token comparison.
- [ ] Did you **check every false positive and miss** against measured data from two days?
