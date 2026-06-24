"""Unittest module for log handlers."""

import os
import sys
import unittest
from unittest import mock

sys.path.append("..")  # Adds higher directory to python modules path.
from utils.log import LogFilter


class MockLog:
    """
    Mock logs.
    """

    def __init__(self, **kwargs):
        self.funcName = kwargs["funcName"]  # NOSONAR


class MetaTestCase(unittest.TestCase):
    def test_log_filter(self):
        result = LogFilter().filter(MockLog(funcName="invoke"))
        self.assertFalse(result)

        result1 = LogFilter().filter(MockLog(funcName="get_file"))
        self.assertTrue(result1)

        result2 = LogFilter().filter(MockLog(funcName="Synced"))
        self.assertTrue(result2)
