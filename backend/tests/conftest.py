"""
Shared pytest fixtures for the backend test suite.

Sets up a fresh asyncio event loop before each test to prevent event loop
state leakage between tests that mix asyncio.run() and
asyncio.get_event_loop().run_until_complete() patterns.
"""
import asyncio
import pytest


@pytest.fixture(autouse=True)
def reset_event_loop():
    """Ensure each test starts with a fresh event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    loop.close()
    asyncio.set_event_loop(None)
