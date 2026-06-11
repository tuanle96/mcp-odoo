from odoo_mcp.knowledge_index import (
    BM25Index,
    KnowledgeStore,
    flatten_record_text,
    knowledge_max_docs,
    tokenize,
)


def test_tokenize_lowercases_and_splits():
    assert tokenize("Hello World-Foo") == ["hello", "world", "foo"]


def test_tokenize_folds_accents_for_vietnamese():
    assert tokenize("Hóa Đơn") == tokenize("hoa đon".replace("đ", "đ"))
    # "hóa đơn" should match a query typed without diacritics
    assert "hoa" in tokenize("hóa đơn")


def test_flatten_record_text_handles_odoo_value_shapes():
    record = {
        "id": 7,
        "name": "Azure Interior",
        "amount": 1500.5,
        "active": True,
        "partner_id": [3, "Deco Addict"],
        "tag_ids": [1, 2, 3],
    }
    text = flatten_record_text(record)
    assert "Azure Interior" in text
    assert "Deco Addict" in text
    assert "1500.5" in text
    # booleans and bare id lists contribute no text
    assert "True" not in text


def test_flatten_record_text_strips_html_markup():
    record = {
        "id": 1,
        "comment": "<div>Customer prefers <b>email</b> contact<br/></div>",
    }
    text = flatten_record_text(record)
    assert "div" not in text
    assert "br" not in text
    assert "Customer prefers" in text
    assert "email" in text


def test_bm25_ranks_matching_doc_higher():
    index = BM25Index()
    index.add(1, "invoice overdue payment reminder customer")
    index.add(2, "delivery order warehouse stock picking")
    index.add(3, "invoice draft validation")
    results = index.search("overdue invoice", limit=3)
    assert results[0]["record_id"] == 1
    ids = [item["record_id"] for item in results]
    assert 2 not in ids


def test_bm25_empty_query_and_corpus():
    index = BM25Index()
    assert index.search("anything") == []
    index.add(1, "alpha")
    assert index.search("") == []
    assert index.search("zzz_no_match") == []


def test_bm25_reindex_replaces_document():
    index = BM25Index()
    index.add(1, "old text about apples")
    index.add(1, "new text about oranges")
    assert index.search("apples") == []
    assert index.search("oranges")[0]["record_id"] == 1
    assert len(index.documents) == 1


def test_bm25_remove_cleans_frequencies():
    index = BM25Index()
    index.add(1, "shared unique1")
    index.add(2, "shared unique2")
    index.remove(1)
    assert "unique1" not in index.document_frequency
    assert index.document_frequency["shared"] == 1


def test_store_index_and_search_roundtrip():
    store = KnowledgeStore(max_docs=100)
    outcome = store.index_records(
        "default",
        "res.partner",
        [
            {"id": 1, "name": "Azure Interior", "city": "Fremont"},
            {"id": 2, "name": "Deco Addict", "city": "Pleasant Hill"},
        ],
    )
    assert outcome["indexed"] == 2
    found = store.search("default", "res.partner", "azure")
    assert found["success"] is True
    assert found["results"][0]["record_id"] == 1


def test_store_search_without_index_fails_helpfully():
    store = KnowledgeStore(max_docs=10)
    result = store.search("default", "res.partner", "azure")
    assert result["success"] is False
    assert "index_knowledge" in result["error"]


def test_store_doc_budget_enforced():
    store = KnowledgeStore(max_docs=2)
    outcome = store.index_records(
        "default",
        "res.partner",
        [{"id": i, "name": f"partner {i}"} for i in range(1, 5)],
    )
    assert outcome["indexed"] == 2
    assert outcome["skipped_over_budget"] == 2
    assert store.stats()["total_documents"] == 2


def test_store_budget_allows_reindex_of_existing_doc():
    store = KnowledgeStore(max_docs=1)
    store.index_records("default", "m", [{"id": 1, "name": "first"}])
    outcome = store.index_records("default", "m", [{"id": 1, "name": "second"}])
    assert outcome["indexed"] == 1
    assert store.search("default", "m", "second")["success"] is True


def test_store_replace_drops_previous_corpus():
    store = KnowledgeStore(max_docs=10)
    store.index_records("default", "m", [{"id": 1, "name": "old"}])
    store.index_records("default", "m", [{"id": 2, "name": "new"}], replace=True)
    assert store.search("default", "m", "old")["success"] is True
    assert store.search("default", "m", "old")["results"] == []
    assert store.search("default", "m", "new")["results"][0]["record_id"] == 2


def test_store_drop_by_model_and_instance():
    store = KnowledgeStore(max_docs=10)
    store.index_records("a", "m1", [{"id": 1, "name": "x"}])
    store.index_records("a", "m2", [{"id": 2, "name": "y"}])
    store.index_records("b", "m1", [{"id": 3, "name": "z"}])
    assert store.drop("a", "m1")["dropped_indexes"] == 1
    assert store.drop("a")["dropped_indexes"] == 1
    assert store.stats()["total_documents"] == 1


def test_store_skips_records_without_int_id_or_text():
    store = KnowledgeStore(max_docs=10)
    outcome = store.index_records(
        "default",
        "m",
        [{"name": "no id"}, {"id": "7", "name": "string id"}, {"id": 5}],
    )
    assert outcome["indexed"] == 0


def test_max_docs_env_parsing(monkeypatch):
    monkeypatch.setenv("ODOO_MCP_KNOWLEDGE_MAX_DOCS", "junk")
    assert knowledge_max_docs() == 5000
    monkeypatch.setenv("ODOO_MCP_KNOWLEDGE_MAX_DOCS", "10")
    assert knowledge_max_docs() == 10
    monkeypatch.setenv("ODOO_MCP_KNOWLEDGE_MAX_DOCS", "-1")
    assert knowledge_max_docs() == 1
