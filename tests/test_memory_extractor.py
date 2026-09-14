from daily_agent.memory_extractor import RuleBasedMemoryExtractor, persist_extracted_memories


class RecordingStore:
    def __init__(self):
        self.preferences = {}
        self.memories = []

    def set_preference(self, key, value, user_id):
        self.preferences[(user_id, key)] = value

    def delete_preference(self, key, user_id):
        return self.preferences.pop((user_id, key), None) is not None

    def add_memory(self, text, user_id, source, expires_at=None):
        self.memories.append((user_id, source, text, expires_at))

    def list_memories(self, user_id, limit):
        return []

    def delete_memory(self, memory_id, user_id):
        return True


def test_extractor_reads_generic_preferences_without_domain_defaults():
    extractor = RuleBasedMemoryExtractor()

    items = extractor.extract("我叫小林，以后请用中文回答，回答尽量简洁")

    assert [(item.key, item.value) for item in items] == [
        ("preferred_name", "小林"),
        ("response_language", "中文"),
        ("response_style", "简洁"),
    ]


def test_extractor_persists_direct_self_identity_for_cross_session_recall():
    extractor = RuleBasedMemoryExtractor()

    items = extractor.extract("我是父王")

    assert [(item.key, item.value) for item in items] == [
        ("self_identity", "父王"),
    ]


def test_extractor_does_not_treat_temporary_sentence_as_self_identity():
    extractor = RuleBasedMemoryExtractor()

    assert extractor.extract("我是想问一下天气") == []
    assert extractor.extract("我是谁") == []


def test_extractor_saves_explicit_stable_fact_but_not_context_pointer():
    extractor = RuleBasedMemoryExtractor()

    saved = extractor.extract("请记住：我主要使用 Python 3.11")
    ignored = extractor.extract("请记住这个")

    assert saved[0].key is None
    assert saved[0].memory_text == "我主要使用 Python 3.11"
    assert ignored == []


def test_extractor_never_persists_sensitive_information():
    extractor = RuleBasedMemoryExtractor()

    assert extractor.extract("请记住：我的 API key 是 sk-secret") == []
    assert extractor.extract("请记住：银行卡是 123456") == []


def test_persist_upserts_preferences_and_deduplicates_memory_through_store():
    store = RecordingStore()

    preferences = persist_extracted_memories("以后请用英文回复", user_id="u1", store=store)
    facts = persist_extracted_memories("请记住：我的编辑器是 VS Code", user_id="u1", store=store)

    assert preferences[0].key == "response_language"
    assert store.preferences == {("u1", "response_language"): "英文"}
    assert facts[0].memory_text == "我的编辑器是 VS Code"
    assert store.memories == [("u1", "auto", "我的编辑器是 VS Code", None)]


def test_extractor_handles_correction_and_forget_directives():
    extractor = RuleBasedMemoryExtractor()

    correction = extractor.extract("我不叫小王，我叫小林")
    forget = extractor.extract("请忘记我的名字")

    assert [(item.key, item.value, item.action) for item in correction] == [
        ("preferred_name", "小林", "upsert"),
    ]
    assert [(item.key, item.action) for item in forget] == [("preferred_name", "delete")]


def test_persist_deletes_preference_when_user_explicitly_asks_to_forget():
    store = RecordingStore()
    store.preferences[("u1", "preferred_name")] = "小王"

    items = persist_extracted_memories("忘记我的名字", user_id="u1", store=store)

    assert items[0].action == "delete"
    assert store.preferences == {}


def test_persist_can_forget_self_identity():
    store = RecordingStore()
    store.preferences[("u1", "self_identity")] = "父王"

    items = persist_extracted_memories("忘记我的身份", user_id="u1", store=store)

    assert [(item.key, item.action) for item in items] == [("self_identity", "delete")]
    assert store.preferences == {}


def test_explicit_memory_can_have_user_controlled_expiry():
    store = RecordingStore()

    items = persist_extracted_memories("请记住 30 天：我临时使用 Python 3.11", user_id="u1", store=store)

    assert items[0].expires_at is not None
    assert store.memories[0][3] == items[0].expires_at
