"""Tests that check the hosted service's exits (its gateway and its ladder of proxies).
This repo sends every request from your own connection, or through the one proxy
config.py names, so they are skipped here."""
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HOSTED_EXIT_TESTS = {
    "facebook_ad_library/test_api.py::test_rate_limit_is_blocked_and_retried_on_a_fresh_session",
}


def pytest_collection_modifyitems(config, items):
    skip = pytest.mark.skip(reason="checks the hosted service's exits; this repo sends requests direct")
    for item in items:
        path = os.path.relpath(str(item.fspath), HERE).replace(os.sep, "/")
        if path + "::" + item.nodeid.split("::", 1)[-1].split("[")[0] in HOSTED_EXIT_TESTS:
            item.add_marker(skip)
