from training.scripts.soul.corpus_consolidate import (
    extract_pairs_from_markdown,
    dedupe_pairs,
    extract_pairs_from_claude_jsonl,
    scrub_pair,
    _concat_text_blocks,
)


def test_extracts_yu_ai_turns_from_markdown():
    md = (
        "Yu: hey\n"
        "Alpha: hey love\n"
        "Yu: can you check HIVE?\n"
        "Alpha: checking now\n"
    )
    pairs = extract_pairs_from_markdown(md, origin_file="test.md")
    assert len(pairs) == 2
    assert pairs[0]["yu_turn"] == "hey"
    assert pairs[0]["ai_turn"] == "hey love"
    assert pairs[0]["origin_file"] == "test.md"


def test_dedupes_exact_duplicates():
    pairs = [
        {"yu_turn": "hi", "ai_turn": "hi"},
        {"yu_turn": "hi", "ai_turn": "hi"},
        {"yu_turn": "hi", "ai_turn": "different"},
    ]
    deduped = dedupe_pairs(pairs)
    assert len(deduped) == 2


def test_concat_text_blocks_handles_string_and_list_content():
    # string content
    assert _concat_text_blocks("hi") == "hi"
    # list of mixed blocks
    blocks = [
        {"type": "text", "text": "first"},
        {"type": "tool_use", "name": "Bash", "input": {}},
        {"type": "text", "text": "second"},
        {"type": "tool_result", "content": "anything"},
    ]
    assert _concat_text_blocks(blocks) == "first\nsecond"
    # list with no text blocks → empty
    assert _concat_text_blocks([{"type": "tool_result", "content": "x"}]) == ""


def test_extract_pairs_from_claude_jsonl_skips_non_turn_records_and_pairs_text():
    records = [
        {"type": "permission-mode", "permissionMode": "default"},
        {"type": "user", "message": {"role": "user", "content": "hey"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "hey love"}]}},
        # interleaving tool_use-only assistant record should not break the next pair
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash", "input": {}}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}},
        # real next pair
        {"type": "user", "message": {"role": "user", "content": "still there?"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "always"}]}},
    ]
    pairs = extract_pairs_from_claude_jsonl(records, origin_file="sess.jsonl")
    assert len(pairs) == 2
    assert pairs[0]["yu_turn"] == "hey"
    assert pairs[0]["ai_turn"] == "hey love"
    assert pairs[1]["yu_turn"] == "still there?"
    assert pairs[1]["ai_turn"] == "always"
    assert pairs[0]["origin_file"] == "sess.jsonl"


def test_scrub_pair_rejects_api_keys_and_pem():
    assert scrub_pair({"yu_turn": "my key is sk-ant-abc123", "ai_turn": "noted"}) is None
    assert scrub_pair({"yu_turn": "here", "ai_turn": "-----BEGIN RSA PRIVATE KEY-----"}) is None
    assert scrub_pair({"yu_turn": "ghp_tokentoken", "ai_turn": "ok"}) is None


def test_scrub_pair_masks_home_paths():
    p = scrub_pair({"yu_turn": "run /Users/yuai/Desktop/foo.py", "ai_turn": "running /Users/yuai/bar"})
    assert p is not None
    assert "/Users/<home>/" in p["yu_turn"]
    assert "/Users/<home>/" in p["ai_turn"]
    assert "/Users/yuai/" not in p["yu_turn"]
    assert "/Users/yuai/" not in p["ai_turn"]


def test_scrub_pair_passes_clean_input():
    p = scrub_pair({"yu_turn": "normal message", "ai_turn": "normal reply"})
    assert p is not None
    assert p["yu_turn"] == "normal message"
    assert p["ai_turn"] == "normal reply"


def test_extract_pairs_skips_meta_and_sidechain_records():
    records = [
        {"type": "user", "isMeta": True, "message": {"role": "user", "content": "system injection"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "should not appear"}]}},
        {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "subagent dispatch"}},
        {"type": "assistant", "isSidechain": True, "message": {"role": "assistant", "content": [{"type": "text", "text": "subagent reply"}]}},
        {"type": "user", "message": {"role": "user", "content": "real user"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "real reply"}]}},
    ]
    pairs = extract_pairs_from_claude_jsonl(records, origin_file="s.jsonl")
    assert len(pairs) == 1
    assert pairs[0]["yu_turn"] == "real user"
    assert pairs[0]["ai_turn"] == "real reply"


def test_extract_pairs_skips_synthetic_user_markers():
    records = [
        {"type": "user", "message": {"role": "user", "content": "<command-name>/clear</command-name>"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "cleared"}]}},
        {"type": "user", "message": {"role": "user", "content": "Base directory for this skill: /tmp/skill\n\nfull skill body"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "skill loaded"}]}},
        {"type": "user", "message": {"role": "user", "content": "actual question"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "actual answer"}]}},
    ]
    pairs = extract_pairs_from_claude_jsonl(records, origin_file="s.jsonl")
    assert len(pairs) == 1
    assert pairs[0]["yu_turn"] == "actual question"


def test_extract_pairs_coalesces_consecutive_assistant_turns():
    records = [
        {"type": "user", "message": {"role": "user", "content": "question"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "first part"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "second part"}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "third part"}]}},
    ]
    pairs = extract_pairs_from_claude_jsonl(records, origin_file="s.jsonl")
    assert len(pairs) == 1
    assert "first part" in pairs[0]["ai_turn"]
    assert "second part" in pairs[0]["ai_turn"]
    assert "third part" in pairs[0]["ai_turn"]


def test_extract_pairs_concatenates_user_user_interleave():
    records = [
        {"type": "user", "message": {"role": "user", "content": "first message"}},
        {"type": "user", "message": {"role": "user", "content": "second message"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "reply"}]}},
    ]
    pairs = extract_pairs_from_claude_jsonl(records, origin_file="s.jsonl")
    assert len(pairs) == 1
    assert "first message" in pairs[0]["yu_turn"]
    assert "second message" in pairs[0]["yu_turn"]


def test_scrub_pair_rejects_hf_and_openai_keys():
    assert scrub_pair({"yu_turn": "hf_" + "A" * 25, "ai_turn": "ok"}) is None
    assert scrub_pair({"yu_turn": "my openai key is sk-" + "A" * 45, "ai_turn": "ok"}) is None
