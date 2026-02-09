"""Testbench for ntt_rom module.

Exhaustive verification: reads all 128 addresses and compares
each value against the Python ZETAS table.
"""

import sys
import os

import cocotb
from cocotb.triggers import Timer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import ZETAS


@cocotb.test()
async def test_exhaustive(dut):
    """Read all 128 ROM entries and verify against Python reference."""
    errors = 0

    for addr in range(128):
        dut.addr.value = addr
        await Timer(1, unit='ns')
        result = dut.zeta.value.to_unsigned()
        expected = ZETAS[addr]
        if result != expected:
            dut._log.error(
                f"FAIL: rom[{addr}] = {result}, expected {expected}"
            )
            errors += 1

    assert errors == 0, f"ROM exhaustive test: {errors}/128 mismatches"
    dut._log.info("PASS: All 128 ROM entries match Python ZETAS table")
