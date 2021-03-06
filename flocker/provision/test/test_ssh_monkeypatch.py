# Copyright Hybrid Logic Ltd.  See LICENSE file for details.

"""
Tests for ``flocker.provision._ssh._monkeypatch``.
"""

from twisted.trial.unittest import SynchronousTestCase as TestCase
from .._ssh._monkeypatch import _patch_7672_needed, patch_7672_applied


class Twisted7672Tests(TestCase):
    """"
    Tests for ``flocker.provision._ssh._monkeypatch``.
    """

    def test_needsPatch(self):
        """
        Check to see if patch is still required.
        """
        self.assertTrue((not _patch_7672_needed()) or patch_7672_applied,
                        "Monkeypatch for twisted bug #7672 can be removed.")
