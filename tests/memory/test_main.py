import asyncio
import logging
from unittest.mock import MagicMock

import pytest

from mem0.memory.main import AsyncMemory, Memory


def _setup_mocks(mocker):
    """Helper to setup common mocks for both sync and async fixtures"""
    mock_embedder = mocker.MagicMock()
    mock_embedder.return_value.embed.return_value = [0.1, 0.2, 0.3]
    mocker.patch("mem0.utils.factory.EmbedderFactory.create", mock_embedder)

    mock_vector_store = mocker.MagicMock()
    mock_vector_store.return_value.search.return_value = []
    mocker.patch("mem0.utils.factory.VectorStoreFactory.create", return_value=mock_vector_store.return_value)

    mock_llm = mocker.MagicMock()
    mocker.patch("mem0.utils.factory.LlmFactory.create", mock_llm)

    mocker.patch("mem0.memory.storage.SQLiteManager", mocker.MagicMock())

    return mock_llm, mock_vector_store


class TestAddToVectorStoreErrors:
    @pytest.fixture
    def mock_memory(self, mocker):
        """Fixture that returns a Memory instance with mocker-based mocks"""
        mock_llm, _ = _setup_mocks(mocker)

        memory = Memory()
        memory.config = mocker.MagicMock()
        memory.config.custom_fact_extraction_prompt = None
        memory.config.custom_update_memory_prompt = None
        memory.api_version = "v1.1"

        return memory

    def test_empty_llm_response_fact_extraction(self, mocker, mock_memory, caplog):
        """Test empty response from LLM during fact extraction"""
        # Setup
        mock_memory.llm.generate_response.return_value = "invalid json"  # This will trigger a JSON decode error
        mock_capture_event = mocker.MagicMock()
        mocker.patch("mem0.memory.main.capture_event", mock_capture_event)

        # Execute
        with caplog.at_level(logging.ERROR):
            result = mock_memory._add_to_vector_store(
                messages=[{"role": "user", "content": "test"}], metadata={}, filters={}, infer=True
            )

        # Verify
        assert mock_memory.llm.generate_response.call_count == 1
        assert result == []  # Should return empty list when no memories processed
        # Check for error message in any of the log records
        assert any("Error in new_retrieved_facts" in record.msg for record in caplog.records), "Expected error message not found in logs"
        assert mock_capture_event.call_count == 1

    def test_empty_llm_response_memory_actions(self, mock_memory, caplog):
        """Test empty response from LLM during memory actions"""
        # Setup
        # First call returns valid JSON, second call returns empty string
        mock_memory.llm.generate_response.side_effect = ['{"facts": ["test fact"]}', ""]

        # Execute
        with caplog.at_level(logging.WARNING):
            result = mock_memory._add_to_vector_store(
                messages=[{"role": "user", "content": "test"}], metadata={}, filters={}, infer=True
            )

        # Verify
        assert mock_memory.llm.generate_response.call_count == 2
        assert result == []  # Should return empty list when no memories processed
        assert "Empty response from LLM, no memories to extract" in caplog.text


@pytest.mark.asyncio
class TestAsyncAddToVectorStoreErrors:
    @pytest.fixture
    def mock_async_memory(self, mocker):
        """Fixture for AsyncMemory with mocker-based mocks"""
        mock_llm, _ = _setup_mocks(mocker)

        memory = AsyncMemory()
        memory.config = mocker.MagicMock()
        memory.config.custom_fact_extraction_prompt = None
        memory.config.custom_update_memory_prompt = None
        memory.api_version = "v1.1"

        return memory

    @pytest.mark.asyncio
    async def test_async_empty_llm_response_fact_extraction(self, mock_async_memory, caplog, mocker):
        """Test empty response in AsyncMemory._add_to_vector_store"""
        mocker.patch("mem0.utils.factory.EmbedderFactory.create", return_value=MagicMock())
        mock_async_memory.llm.generate_response.return_value = "invalid json"  # This will trigger a JSON decode error
        mock_capture_event = mocker.MagicMock()
        mocker.patch("mem0.memory.main.capture_event", mock_capture_event)

        with caplog.at_level(logging.ERROR):
            result = await mock_async_memory._add_to_vector_store(
                messages=[{"role": "user", "content": "test"}], metadata={}, effective_filters={}, infer=True
            )
        assert mock_async_memory.llm.generate_response.call_count == 1
        assert result == []
        # Check for error message in any of the log records
        assert any("Error in new_retrieved_facts" in record.msg for record in caplog.records), "Expected error message not found in logs"
        assert mock_capture_event.call_count == 1

    @pytest.mark.asyncio
    async def test_async_empty_llm_response_memory_actions(self, mock_async_memory, caplog, mocker):
        """Test empty response in AsyncMemory._add_to_vector_store"""
        mocker.patch("mem0.utils.factory.EmbedderFactory.create", return_value=MagicMock())
        mock_async_memory.llm.generate_response.side_effect = ['{"facts": ["test fact"]}', ""]
        mock_capture_event = mocker.MagicMock()
        mocker.patch("mem0.memory.main.capture_event", mock_capture_event)

        with caplog.at_level(logging.WARNING):
            result = await mock_async_memory._add_to_vector_store(
                messages=[{"role": "user", "content": "test"}], metadata={}, effective_filters={}, infer=True
            )

        assert result == []
        assert "Empty response from LLM, no memories to extract" in caplog.text
        assert mock_capture_event.call_count == 1


class TestSyncAsyncConsistency:
    @pytest.fixture
    def memory_pair(self, mocker):
        _setup_mocks(mocker)

        sync_memory = Memory()
        async_memory = AsyncMemory()

        for memory in (sync_memory, async_memory):
            memory.api_version = "v1.1"
            memory.enable_graph = False

        return sync_memory, async_memory

    def test_search_consistency_between_sync_and_async(self, memory_pair):
        sync_memory, async_memory = memory_pair

        shared_results = [
            MagicMock(
                id="m1",
                score=0.91,
                payload={"data": "loves chai", "user_id": "u1", "created_at": "2024", "updated_at": "2024"},
            )
        ]

        sync_memory.vector_store.search.return_value = shared_results
        async_memory.vector_store.search.return_value = shared_results

        sync_result = sync_memory.search("chai", user_id="u1")
        async_result = asyncio.run(async_memory.search("chai", user_id="u1"))

        assert sync_result == async_result

    def test_get_all_consistency_between_sync_and_async(self, memory_pair):
        sync_memory, async_memory = memory_pair

        shared_memories = [
            MagicMock(
                id="m2",
                payload={"data": "prefers email", "user_id": "u1", "created_at": "2024", "updated_at": "2024"},
            )
        ]

        sync_memory.vector_store.list.return_value = shared_memories
        async_memory.vector_store.list.return_value = shared_memories

        sync_result = sync_memory.get_all(user_id="u1")
        async_result = asyncio.run(async_memory.get_all(user_id="u1"))

        assert sync_result == async_result

    @pytest.mark.asyncio
    async def test_add_infer_false_consistency_between_sync_and_async(self, memory_pair, mocker):
        sync_memory, async_memory = memory_pair

        mocker.patch.object(sync_memory, "_create_memory", side_effect=["sync-1", "sync-2"])
        mocker.patch.object(async_memory, "_create_memory", side_effect=["async-1", "async-2"])

        messages = [
            {"role": "user", "content": "I like cycling", "name": "alice"},
            {"role": "assistant", "content": "Got it"},
        ]

        sync_result = sync_memory.add(messages, user_id="u1", infer=False)
        async_result = await async_memory.add(messages, user_id="u1", infer=False)

        assert [item["memory"] for item in sync_result["results"]] == [item["memory"] for item in async_result["results"]]
        assert [item["event"] for item in sync_result["results"]] == [item["event"] for item in async_result["results"]]
        assert [item["role"] for item in sync_result["results"]] == [item["role"] for item in async_result["results"]]
