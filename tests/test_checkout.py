from __future__ import annotations

import inspect

from superseded.server.checkout import checkout_repo


def test_checkout_repo_no_base_ref_param():
    """base_ref is dead parameter; verify it's removed from signature."""
    sig = inspect.signature(checkout_repo)
    assert "base_ref" not in sig.parameters
