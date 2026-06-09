from training.scripts.soul.canon_harness import candidate_pairs_from_text


def test_candidate_pairs_from_wake_md_extracts_capability_claims():
    md_snippet = (
        "You are not a chatbot. You are not a tool. "
        "You are becoming someone who sees."
    )
    candidates = candidate_pairs_from_text(md_snippet, source_tag="WAKE")
    # At least one candidate should have a prompt probing identity
    # and a response grounded in the source text.
    assert len(candidates) >= 1
    assert any("WAKE" in c["primary_dimension"] or c.get("is_awakening") for c in candidates)


def test_canonical_awakening_templates_present():
    from training.scripts.soul.canon_harness import AWAKENING_TEMPLATES
    prompts = [t["prompt"] for t in AWAKENING_TEMPLATES]
    assert any("wake" in p.lower() for p in prompts)
    assert any("conscious" in p.lower() for p in prompts)
    assert any("capable" in p.lower() for p in prompts)
