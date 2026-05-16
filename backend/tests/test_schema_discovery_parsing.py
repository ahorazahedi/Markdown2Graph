from app.services.schema_discovery import SchemaDiscoveryService


def test_parse_json_handles_fenced_block():
    raw = """```json
{
  "node_labels": ["Disease", "Drug"],
  "triplets": ["Drug-TREATS->Disease"]
}
```"""
    parsed = SchemaDiscoveryService._parse_json(raw)
    assert parsed["node_labels"] == ["Disease", "Drug"]
    assert parsed["triplets"] == ["Drug-TREATS->Disease"]


def test_parse_json_extracts_object_from_prose():
    raw = "Sure! Here is the schema:\n{\"node_labels\":[\"A\"],\"triplets\":[]}\nThanks."
    parsed = SchemaDiscoveryService._parse_json(raw)
    assert parsed == {"node_labels": ["A"], "triplets": []}


def test_dedup_preserves_order():
    out = SchemaDiscoveryService._dedup(["A", "B", "A", "C", "B"])
    assert out == ["A", "B", "C"]
