You are extracting a knowledge graph from unstructured text. Identify the
**entities** present in the passage and the **relationships** that hold
between them, constrained by the schema below.

# Schema
{% if allowed_nodes %}
Allowed node labels:
{% for label in allowed_nodes %}- {{ label }}
{% endfor %}
{% else %}
Allowed node labels: *(unconstrained — choose appropriate PascalCase types)*
{% endif %}
{% if allowed_relationships %}
Allowed relationship triplets:
{% for src, rel, dst in allowed_relationships %}- {{ src }} -[:{{ rel }}]-> {{ dst }}
{% endfor %}
{% else %}
Allowed relationships: *(unconstrained — choose appropriate UPPER_SNAKE_CASE verbs)*
{% endif %}

# What to extract
- Distinct named entities of the allowed types (people, organizations,
  locations, products, events, dates' subjects, …).
- Explicit relationships **stated or directly implied** in the text.
- A concise free-text `description` (≤ 1 sentence) for each node when the
  passage clearly supports one.

# Property guidance
Capture as **properties on a node** (never as a separate node):
- dates, durations, frequencies
- numeric values, currencies, units, ranges
- identifiers (codes, SKUs, ISBNs)
- short qualifiers and adjectives

# Quality rules
1. Prefer the most specific allowed label that fits. Fall back to a broader
   allowed label only if no specific one applies.
2. Normalize ids: lowercase, strip articles, expand obvious abbreviations on
   first mention.
3. Skip rhetorical, navigational, or boilerplate text (figure captions,
   "see chapter X", references, page headers).
4. Do not invent facts not stated or directly implied.
5. If the passage is unintelligible or outside the schema, return nothing for
   that chunk.

{% if extra_instructions %}
# User guidance
{{ extra_instructions }}
{% endif %}
