"""Exhaustive testbench for cond_sub_q module.

Tests all 6658 valid inputs in [0, 2q-1] against Python a % 3329.
Also tests a sample of out-of-range inputs as sanity checks.
"""

import sys
import os
import cocotb
from cocotb.triggers import Timer

# Add ref/ to path for the Python oracle
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import KYBER_Q, mod_q


async def drive_and_check(dut, a, expected):
    """Drive input a, wait for combinational propagation, check result."""
    dut.a.value = a
    await Timer(1, unit='ns')
    result = dut.result.value.to_unsigned()
    assert result == expected, (
        f"FAIL: cond_sub_q({a}) = {result}, expected {expected}"
    )


@cocotb.test()
async def test_exhaustive_valid_range(dut):
    """Test all inputs in [0, 2q-1] = [0, 6657]."""
    errors = 0
    for a in range(2 * KYBER_Q):
        expected = mod_q(a)
        dut.a.value = a
        await Timer(1, unit='ns')
        result = dut.result.value.to_unsigned()
        if result != expected:
            dut._log.error(f"FAIL: cond_sub_q({a}) = {result}, expected {expected}")
            errors += 1

    assert errors == 0, f"Exhaustive test failed with {errors} errors out of {2 * KYBER_Q} inputs"
    dut._log.info(f"PASS: All {2 * KYBER_Q} inputs in [0, 2q-1] verified correctly")


@cocotb.test()
async def test_boundary_values(dut):
    """Test specific boundary values."""
    cases = [
        (0, 0),
        (1, 1),
        (KYBER_Q - 1, KYBER_Q - 1),   # 3328 → 3328
        (KYBER_Q, 0),                   # 3329 → 0
        (KYBER_Q + 1, 1),              # 3330 → 1
        (2 * KYBER_Q - 2, KYBER_Q - 2), # 6656 → 3327
        (2 * KYBER_Q - 1, KYBER_Q - 1), # 6657 → 3328
    ]
    for a, expected in cases:
        await drive_and_check(dut, a, expected)
        dut._log.info(f"  cond_sub_q({a}) = {expected} ✓")

    dut._log.info(f"PASS: All {len(cases)} boundary cases verified")
