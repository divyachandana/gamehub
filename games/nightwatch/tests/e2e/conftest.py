def pytest_configure(config):  # type: ignore[no-untyped-def]
    config.addinivalue_line("markers", "e2e: starts a real NightWatch server (skip with -m 'not e2e')")
