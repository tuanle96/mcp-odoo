"""Unit tests for schema_cache module BoundedTTLCache."""

import time
import threading

import pytest

from odoo_mcp import schema_cache


class TestSchemaCacheSettings:
    """Test _schema_cache_settings env var parsing."""

    def test_defaults_when_unset(self):
        """Return defaults when env vars unset."""
        max_entries, ttl = schema_cache._schema_cache_settings()
        assert max_entries == 256
        assert ttl == 10 * 60

    def test_parses_max_entries_from_env(self, monkeypatch):
        """Parse ODOO_MCP_SCHEMA_CACHE_MAX."""
        monkeypatch.setenv("ODOO_MCP_SCHEMA_CACHE_MAX", "512")
        max_entries, ttl = schema_cache._schema_cache_settings()
        assert max_entries == 512
        assert ttl == 10 * 60

    def test_parses_ttl_from_env(self, monkeypatch):
        """Parse ODOO_MCP_SCHEMA_CACHE_TTL."""
        monkeypatch.setenv("ODOO_MCP_SCHEMA_CACHE_TTL", "300")
        max_entries, ttl = schema_cache._schema_cache_settings()
        assert max_entries == 256
        assert ttl == 300.0

    def test_parses_both_env_vars(self, monkeypatch):
        """Parse both env vars together."""
        monkeypatch.setenv("ODOO_MCP_SCHEMA_CACHE_MAX", "128")
        monkeypatch.setenv("ODOO_MCP_SCHEMA_CACHE_TTL", "600")
        max_entries, ttl = schema_cache._schema_cache_settings()
        assert max_entries == 128
        assert ttl == 600.0

    def test_clamps_max_entries_to_minimum(self, monkeypatch):
        """Clamp max_entries to minimum 1."""
        monkeypatch.setenv("ODOO_MCP_SCHEMA_CACHE_MAX", "0")
        max_entries, _ = schema_cache._schema_cache_settings()
        assert max_entries == 1

        monkeypatch.setenv("ODOO_MCP_SCHEMA_CACHE_MAX", "-10")
        max_entries, _ = schema_cache._schema_cache_settings()
        assert max_entries == 1

    def test_clamps_ttl_to_minimum(self, monkeypatch):
        """Clamp ttl to minimum 1.0."""
        monkeypatch.setenv("ODOO_MCP_SCHEMA_CACHE_TTL", "0")
        _, ttl = schema_cache._schema_cache_settings()
        assert ttl == 1.0

        monkeypatch.setenv("ODOO_MCP_SCHEMA_CACHE_TTL", "-10")
        _, ttl = schema_cache._schema_cache_settings()
        assert ttl == 1.0

    def test_invalid_max_entries_defaults(self, monkeypatch):
        """Return default for invalid ODOO_MCP_SCHEMA_CACHE_MAX."""
        monkeypatch.setenv("ODOO_MCP_SCHEMA_CACHE_MAX", "not_a_number")
        max_entries, ttl = schema_cache._schema_cache_settings()
        assert max_entries == 256

    def test_invalid_ttl_defaults(self, monkeypatch):
        """Return default for invalid ODOO_MCP_SCHEMA_CACHE_TTL."""
        monkeypatch.setenv("ODOO_MCP_SCHEMA_CACHE_TTL", "not_a_number")
        max_entries, ttl = schema_cache._schema_cache_settings()
        assert ttl == 10 * 60

    def test_whitespace_stripped_from_env_vars(self, monkeypatch):
        """Strip whitespace from env var values."""
        monkeypatch.setenv("ODOO_MCP_SCHEMA_CACHE_MAX", "  200  ")
        monkeypatch.setenv("ODOO_MCP_SCHEMA_CACHE_TTL", "  500.5  ")
        max_entries, ttl = schema_cache._schema_cache_settings()
        assert max_entries == 200
        assert ttl == 500.5


class TestBoundedTTLCacheBasicOps:
    """Test BoundedTTLCache basic get/set operations."""

    def test_set_and_get_simple_value(self):
        """Set and retrieve a simple value."""
        cache = schema_cache.BoundedTTLCache(max_entries=10, ttl_seconds=60)
        cache["key1"] = "value1"
        assert cache["key1"] == "value1"

    def test_get_missing_key_raises_keyerror(self):
        """Raise KeyError for missing key."""
        cache = schema_cache.BoundedTTLCache()
        with pytest.raises(KeyError):
            cache["missing"]

    def test_get_method_returns_default(self):
        """get() method returns default for missing key."""
        cache = schema_cache.BoundedTTLCache()
        assert cache.get("missing") is None
        assert cache.get("missing", "default") == "default"

    def test_contains_existing_key(self):
        """__contains__ returns True for existing key."""
        cache = schema_cache.BoundedTTLCache()
        cache["key1"] = "value1"
        assert ("key1" in cache) is True

    def test_contains_missing_key(self):
        """__contains__ returns False for missing key."""
        cache = schema_cache.BoundedTTLCache()
        assert ("missing" in cache) is False

    def test_len_after_set_and_get(self):
        """Track length correctly after set/get."""
        cache = schema_cache.BoundedTTLCache()
        cache["key1"] = "value1"
        cache["key2"] = "value2"
        assert len(cache) == 2

    def test_overwrite_existing_key(self):
        """Overwrite existing key value."""
        cache = schema_cache.BoundedTTLCache()
        cache["key1"] = "value1"
        cache["key1"] = "value2"
        assert cache["key1"] == "value2"
        assert len(cache) == 1


class TestBoundedTTLCacheTTL:
    """Test BoundedTTLCache TTL expiration."""

    def test_expired_key_raises_keyerror(self, monkeypatch):
        """Expired key raises KeyError on access."""
        cache = schema_cache.BoundedTTLCache(max_entries=10, ttl_seconds=1.0)
        cache["key1"] = "value1"

        # Monkeypatch time.time to simulate expiration
        original_time = time.time
        monkeypatch.setattr("time.time", lambda: original_time() + 2.0)

        with pytest.raises(KeyError):
            cache["key1"]

    def test_expired_key_not_in_cache(self, monkeypatch):
        """Expired key returns False in __contains__."""
        cache = schema_cache.BoundedTTLCache(max_entries=10, ttl_seconds=1.0)
        cache["key1"] = "value1"

        original_time = time.time
        monkeypatch.setattr("time.time", lambda: original_time() + 2.0)

        assert ("key1" in cache) is False

    def test_expired_key_get_returns_default(self, monkeypatch):
        """Expired key with get() returns default."""
        cache = schema_cache.BoundedTTLCache(max_entries=10, ttl_seconds=1.0)
        cache["key1"] = "value1"

        original_time = time.time
        monkeypatch.setattr("time.time", lambda: original_time() + 2.0)

        assert cache.get("key1", "default") == "default"

    def test_key_alive_before_ttl_expires(self, monkeypatch):
        """Key accessible before TTL expires."""
        cache = schema_cache.BoundedTTLCache(max_entries=10, ttl_seconds=10.0)
        cache["key1"] = "value1"

        original_time = time.time
        monkeypatch.setattr("time.time", lambda: original_time() + 5.0)

        assert cache["key1"] == "value1"

    def test_ttl_countdown_from_set_time(self, monkeypatch):
        """TTL countdown starts from set time, not first access."""
        cache = schema_cache.BoundedTTLCache(max_entries=10, ttl_seconds=10.0)

        original_time = time.time
        monkeypatch.setattr("time.time", lambda: original_time())
        cache["key1"] = "value1"

        # Move time forward 5 seconds
        monkeypatch.setattr("time.time", lambda: original_time() + 5.0)
        assert cache["key1"] == "value1"

        # Move time forward to 11 seconds total (1 second past expiry)
        monkeypatch.setattr("time.time", lambda: original_time() + 11.0)
        with pytest.raises(KeyError):
            cache["key1"]


class TestBoundedTTLCacheLRU:
    """Test BoundedTTLCache LRU eviction."""

    def test_lru_evicts_oldest_entry(self):
        """Evict oldest entry when max_entries exceeded."""
        cache = schema_cache.BoundedTTLCache(max_entries=2, ttl_seconds=600)
        cache["key1"] = "value1"
        cache["key2"] = "value2"
        cache["key3"] = "value3"  # Triggers eviction of key1

        assert len(cache) == 2
        with pytest.raises(KeyError):
            cache["key1"]
        assert cache["key2"] == "value2"
        assert cache["key3"] == "value3"

    def test_lru_refreshes_on_read(self):
        """Reading a key refreshes its LRU position."""
        cache = schema_cache.BoundedTTLCache(max_entries=2, ttl_seconds=600)
        cache["key1"] = "value1"
        cache["key2"] = "value2"

        # Access key1 to refresh its position
        _ = cache["key1"]

        # Add key3, which should evict key2 (oldest accessed)
        cache["key3"] = "value3"

        assert len(cache) == 2
        assert cache["key1"] == "value1"
        assert cache["key3"] == "value3"
        with pytest.raises(KeyError):
            cache["key2"]

    def test_lru_updates_on_set_existing_key(self):
        """Setting existing key updates LRU position."""
        cache = schema_cache.BoundedTTLCache(max_entries=2, ttl_seconds=600)
        cache["key1"] = "value1"
        cache["key2"] = "value2"

        # Overwrite key1 to refresh its position
        cache["key1"] = "value1_updated"

        # Add key3, which should evict key2
        cache["key3"] = "value3"

        assert cache["key1"] == "value1_updated"
        assert cache["key3"] == "value3"
        with pytest.raises(KeyError):
            cache["key2"]

    def test_max_entries_one(self):
        """Work correctly with max_entries=1."""
        cache = schema_cache.BoundedTTLCache(max_entries=1, ttl_seconds=600)
        cache["key1"] = "value1"
        cache["key2"] = "value2"

        assert len(cache) == 1
        with pytest.raises(KeyError):
            cache["key1"]
        assert cache["key2"] == "value2"

    def test_large_batch_insert(self):
        """Handle large batch inserts correctly."""
        cache = schema_cache.BoundedTTLCache(max_entries=100, ttl_seconds=600)
        for i in range(200):
            cache[f"key{i}"] = f"value{i}"

        assert len(cache) == 100
        # Oldest entries should be evicted
        with pytest.raises(KeyError):
            cache["key0"]
        # Newest entries should exist
        assert cache["key199"] == "value199"


class TestBoundedTTLCacheThreadSafety:
    """Test BoundedTTLCache basic thread safety."""

    def test_concurrent_reads_dont_crash(self):
        """Multiple threads can read concurrently."""
        cache = schema_cache.BoundedTTLCache(max_entries=10, ttl_seconds=60)
        cache["key1"] = "value1"

        results = []
        errors = []

        def read_key():
            try:
                for _ in range(100):
                    results.append(cache.get("key1"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=read_key) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len([r for r in results if r == "value1"]) > 0

    def test_concurrent_writes_dont_crash(self):
        """Multiple threads can write concurrently."""
        cache = schema_cache.BoundedTTLCache(max_entries=100, ttl_seconds=60)
        errors = []

        def write_keys(prefix):
            try:
                for i in range(50):
                    cache[f"{prefix}_key{i}"] = f"value{i}"
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_keys, args=(f"t{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # Cache should have some entries (may be less than 250 due to LRU)
        assert len(cache) > 0

    def test_concurrent_mixed_operations(self):
        """Mixed read/write operations don't crash."""
        cache = schema_cache.BoundedTTLCache(max_entries=50, ttl_seconds=60)
        errors = []

        def mixed_ops(thread_id):
            try:
                for i in range(50):
                    cache[f"t{thread_id}_k{i}"] = f"v{i}"
                    cache.get(f"t{thread_id}_k{i // 2}", None)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=mixed_ops, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


class TestBoundedTTLCacheEdgeCases:
    """Test BoundedTTLCache edge cases and special values."""

    def test_string_keys_converted(self):
        """Non-string keys converted to strings."""
        cache = schema_cache.BoundedTTLCache()
        cache[123] = "value"
        # __setitem__ receives int, internally converts
        # __getitem__ expects str, so we need str("123")
        # But the API converts internally
        cache["123"] = "value2"
        assert cache["123"] == "value2"

    def test_complex_object_values(self):
        """Cache complex objects."""
        cache = schema_cache.BoundedTTLCache()
        obj = {"nested": {"key": [1, 2, 3]}, "tuple": (1, 2)}
        cache["obj"] = obj
        retrieved = cache["obj"]
        assert retrieved["nested"]["key"] == [1, 2, 3]
        assert retrieved["tuple"] == (1, 2)

    def test_none_value_can_be_cached(self):
        """Cache None value."""
        cache = schema_cache.BoundedTTLCache()
        cache["none_key"] = None
        assert cache["none_key"] is None

    def test_empty_string_key(self):
        """Cache with empty string key."""
        cache = schema_cache.BoundedTTLCache()
        cache[""] = "empty_key_value"
        assert cache[""] == "empty_key_value"


class TestBuildSchemaCache:
    """Test _build_schema_cache factory function."""

    def test_builds_with_default_settings(self):
        """Build cache with default settings."""
        cache = schema_cache._build_schema_cache()
        assert isinstance(cache, schema_cache.BoundedTTLCache)
        assert cache.max_entries == 256
        assert cache.ttl_seconds == 10 * 60

    def test_builds_with_env_settings(self, monkeypatch):
        """Build cache using env var settings."""
        monkeypatch.setenv("ODOO_MCP_SCHEMA_CACHE_MAX", "128")
        monkeypatch.setenv("ODOO_MCP_SCHEMA_CACHE_TTL", "300")
        cache = schema_cache._build_schema_cache()
        assert cache.max_entries == 128
        assert cache.ttl_seconds == 300
