"""The suite for the rewrite: empty until there is something to check.

The four gates in ``.github/workflows/checks.yml`` still run, and a suite that reads nothing would
let them pass over a package with no tests. ``tests/test_package.py`` is what keeps the first gate
honest while the tree is empty; delete it when there is a real test to take its place.
"""
