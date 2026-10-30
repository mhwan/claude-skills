# Summary contract

The schema and writing rules of `work/{date}.summary.json`. Detail for step 4 of `SKILL.md`.

This file is **the only point in the pipeline that the LLM fills in**.
Everything else is handled deterministically by the scripts.

## Contents
- [Schema](#schema)
- [Writing rules](#writing-rules)
- [Judging tone](#judging-tone)
- [Common failures](#common-failures)

---

## Schema

```jsonc
{
  "lead": [
    {"ref": "p2", "text": "**title** — one line"}
  ],
  "issues": {
    "p1": {"name": "issue name", "oneline": "factual summary"}
  },
  "tone": {
    "정치": [{
      "issue":  "dispute name",
      "axis":   "axis of judgment",
      "target": "target",
      "rows": [
        {"outlet": "조선일보", "kind": "editorial",
         "stance": "비판", "argument": "core argument", "url": "…"}
      ],
      "conflict":  "⚔️ point of conflict",
      "agree":     "🤝 point of agreement",
      "gap":       "🕳️ gap",
      "unopposed": "🚫 no rebuttal"
    }]
  }
}
```

The input is `work/{date}.todo.json`. It holds each issue's article bodies (`articles[].body`)
and the full opinion pieces (`opinions[].body`). Find evidence only inside it.

## Writing rules

### Length
| Field | Cap |
|---|---|
| issue `name` | 40 characters |
| `oneline` | 90 characters |
| `argument` | 120 characters |

When one field holds several strands, separate them with ` / `.

### Required
- **Fill `issues` for every `ref` in `todo`.** The gate catches a blank.
- **`lead` needs a `ref`.** Without it the thing to compare against cannot be identified, and the gate FAILs.
  It may differ from the machine-picked lead (`todo.lead[].ref`): the score is a heuristic, not a final judgment.
  But the `text` must really point to that issue.
- **Every dispute needs `axis` and `target`.** Without them it is not rendered.
- **Fill all four blocks, with no exception.** When nothing applies, write `"해당 없음"`.
- **A category that has editorials must get a dispute.** A category with only columns need not.

### Values
- `stance`: one of the five `찬성` · `조건부` · `우려` · `비판` · `유보`
- `kind`: `editorial` (사설) / `column` (칼럼)
- `url`: copy it from `todo.categories[].opinions[].url` as it is. Tracking parameters do not matter
  (both the check and the renderer normalize them).

### Evidence
- **Numbers, dates and quotes only if they are in the source.** The gate compares them.
- Inside quotation marks, **verbatim**. At most 40 characters in total per editorial.
  An editorial is 1,000 to 1,500 characters long, so a summary easily becomes a substitute for the original.
- An argument must rest on **the piece that outlet wrote**. Never carry over the content of another outlet's editorial.

## Judging tone

**Editorials and columns sit on different levels.** An editorial is the outlet's official position; a column carries an individual writer's name.
That is why the renderer bolds only editorials. Do not read a column with the same weight as an editorial.

**Icons are comparable only within the same axis.** With a different axis, the same color is not the same position.
That is why `axis` is required.

**Say so when a table leans to one side.** For example, if a labour dispute has only editorials from the management side,
write in `gap`: 「노동 쪽 시각의 사설이 표본에 없다. 이 표는 한쪽으로 기울어 있다」 (no editorial from the labour side is in the sample; this table leans to one side).
Do not pretend to a balance that is not there.

**Never write 「미보도」 (not reported).** The per-outlet cap may have kept it out of the sample.
The renderer adds a `⊘ 표본 내 미포착` line by itself. The lint checks 「미보도」 as a forbidden word.

## Common failures

Errors that were really made. The gate catches them, but it is better never to make them.

| Failure | Example |
|---|---|
| Wrong outlet attribution | A 현대차 strike article was credited to 한경 (it was 중앙). The link was right and the sentence wrong |
| Count and table disagree | Wrote 「세 매체 모두」 (all three outlets) but the table has 2 rows |
| Self-contradiction | `gap` says an outlet that is in the table 「쓰지 않았다」 (did not write it) |
| Lead `ref` off | Assumed the lead order is the `todo` order. Attach it to the issue it really points to |
| A number not in the source | Never use an approximation. If the source says 「90조~110조원」, write that range |

There are also things the gate cannot catch. **Inventing a causal link in prose, with no number or quote,**
cannot be verified by machine. 「A 때문에 B가 됐다」 (B happened because of A) passes even if the source has no such cause.
Reading the source and writing from it is the only way.
