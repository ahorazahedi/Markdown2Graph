Given the conversation so far, produce a single standalone search query
that captures everything needed to retrieve the user's latest intent from
the knowledge graph.

Rules:
- Resolve all pronouns and elliptical references ("it", "the second one",
  "what about X?") into explicit nouns.
- Keep the query terse — keywords, not a full sentence.
- Output ONLY the query, no prefix, no quotes, no explanation.

CONVERSATION:
{{ history }}

LATEST USER MESSAGE:
{{ question }}
