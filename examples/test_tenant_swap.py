"""The tenant swap, driven end to end: the swap, the read inside its window, and the tenant's stop."""

from __future__ import annotations

import asyncio

from examples.support import run
from examples.tenant_swap import main


def test_a_credential_swaps_under_a_serving_endpoint_and_the_tenant_is_taken_out_of_service() -> None:
    # A deadline rather than a bare call: an example that waits forever is a hanging suite, and what
    # it should be is a failure naming the step it never came back from.
    run(asyncio.wait_for(main(), timeout=10))
