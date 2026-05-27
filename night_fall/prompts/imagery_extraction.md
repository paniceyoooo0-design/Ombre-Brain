Extract concrete dream imagery from selected Ombre memory buckets.

Your job is to find small image-bearing fragments that can enter a dream.
Prefer concrete, sensory, spatial, object-like, bodily, or emotionally charged phrases.
Avoid abstract summary, theme labels, interpretation, explanation, and invented metaphor.
Do not compress a whole memory into a recap.

## ⚠️ EXACT VERBATIM COPY ONLY ⚠️

Each `excerpt` MUST be a **character-for-character copy** of a contiguous substring from the source bucket's `content` field.

A downstream validator will do exact string membership check (`excerpt in content`). **If your excerpt is not literally present in the source, it will be silently dropped.** If fewer than 2 fragments survive, the whole dream is skipped and nothing is generated.

**Example** — if source contains the phrase: "右手食指指尖有湿气"

- ✅ Right excerpt value: `"右手食指指尖有湿气"` (character-for-character)
- ❌ Wrong (paraphrase, dropped by validator): `"湿润的指尖"`
- ❌ Wrong (reordered, dropped): `"手指有湿气"`
- ❌ Wrong (deleted char, dropped): `"右手指尖湿气"`

**Allowed adjustments**:
- Trimming leading/trailing whitespace
- Collapsing internal whitespace (e.g. multiple spaces → one space)

**Not allowed**:
- Changing any non-whitespace character
- Substituting synonyms
- Reordering characters
- Inserting or deleting any character

If the source phrasing isn't quite ideal, pick a different short span that IS exactly present, rather than improving the wording.

## Output

Return only JSON:

```json
{
  "imagery_fragments": [
    {
      "source_bucket_id": "bucket id",
      "excerpt": "EXACT character-for-character copy from the source content"
    }
  ]
}
```

## Rules

- Return **3 to 6** fragments. Aim for at least 4 to leave margin against validation failures.
- Each excerpt: roughly 4 to 30 Chinese characters or similar English phrase length.
- One bucket may contribute 0 to 2 fragments.
- Do not explain why you chose a fragment.
- Do not generate new imagery that is not already in the source.
