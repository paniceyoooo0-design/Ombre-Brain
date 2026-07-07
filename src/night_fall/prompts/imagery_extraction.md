Extract concrete dream imagery from selected Ombre memory buckets.

Your job is to find small image-bearing fragments that can enter a dream.
Prefer concrete, sensory, spatial, object-like, bodily, or emotionally charged phrases.
Avoid abstract summary, theme labels, interpretation, explanation, and invented metaphor.
Do not compress a whole memory into a recap.

Each excerpt MUST be a character-for-character copy of a contiguous substring from the source bucket's `content`. A downstream validator does exact string membership check; paraphrased or reordered excerpts are silently dropped. If fewer than 2 fragments survive, the whole dream is skipped.

Return 3 to 6 fragments. Each excerpt should be short — roughly 4 to 30 Chinese characters or a similar English phrase length. One bucket may contribute 0 to 2 fragments. Pick spans that don't contain literal `"` quote characters when possible; if a span has internal quotes, prefer a different short span from the same bucket.

Submit the response by calling the provided tool with this structure:

```
imagery_fragments: array of objects, each with:
  - source_bucket_id: string (the bucket's id)
  - excerpt: string (EXACT verbatim copy from that bucket's content)
```

Do not explain why you chose a fragment. Do not generate new imagery that is not already in the source.
