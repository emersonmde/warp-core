"""Testbench for decompress module.

Exhaustively tests Decompress_q(y, d) = round(q * y / 2^d) for all 5 Kyber
D values. Total vectors: 2 + 16 + 32 + 1024 + 2048 = 3122.

Also verifies all outputs are in [0, q-1].
"""

import sys
import os

import cocotb
from cocotb.triggers import Timer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import KYBER_Q, decompress_q


D_VALUES = [1, 4, 5, 10, 11]
RESULT_SIGNALS = {
    1:  'result_d1',
    4:  'result_d4',
    5:  'result_d5',
    10: 'result_d10',
    11: 'result_d11',
}


async def drive_and_check(dut, y, d):
    """Drive y input, wait for combinational settle, check result for given D."""
    dut.y.value = y
    await Timer(1, unit='ns')
    sig = getattr(dut, RESULT_SIGNALS[d])
    result = sig.value.to_unsigned()
    expected = decompress_q(y, d)
    return result, expected, result == expected


@cocotb.test()
async def test_exhaustive_d1(dut):
    """Exhaustive test for D=1: 2 values."""
    errors = 0
    for y in range(2):
        result, expected, match = await drive_and_check(dut, y, 1)
        if not match:
            dut._log.error(f"FAIL D=1: decompress({y}) = {result}, expected {expected}")
            errors += 1
        assert result < KYBER_Q, f"D=1: output {result} >= q for y={y}"
    assert errors == 0, f"D=1: {errors} errors"
    dut._log.info("PASS: D=1, 2 values exhaustively verified")


@cocotb.test()
async def test_exhaustive_d4(dut):
    """Exhaustive test for D=4: 16 values."""
    errors = 0
    for y in range(16):
        result, expected, match = await drive_and_check(dut, y, 4)
        if not match:
            dut._log.error(f"FAIL D=4: decompress({y}) = {result}, expected {expected}")
            errors += 1
        assert result < KYBER_Q, f"D=4: output {result} >= q for y={y}"
    assert errors == 0, f"D=4: {errors} errors"
    dut._log.info("PASS: D=4, 16 values exhaustively verified")


@cocotb.test()
async def test_exhaustive_d5(dut):
    """Exhaustive test for D=5: 32 values."""
    errors = 0
    for y in range(32):
        result, expected, match = await drive_and_check(dut, y, 5)
        if not match:
            dut._log.error(f"FAIL D=5: decompress({y}) = {result}, expected {expected}")
            errors += 1
        assert result < KYBER_Q, f"D=5: output {result} >= q for y={y}"
    assert errors == 0, f"D=5: {errors} errors"
    dut._log.info("PASS: D=5, 32 values exhaustively verified")


@cocotb.test()
async def test_exhaustive_d10(dut):
    """Exhaustive test for D=10: 1024 values."""
    errors = 0
    for y in range(1024):
        result, expected, match = await drive_and_check(dut, y, 10)
        if not match:
            dut._log.error(f"FAIL D=10: decompress({y}) = {result}, expected {expected}")
            errors += 1
        assert result < KYBER_Q, f"D=10: output {result} >= q for y={y}"
    assert errors == 0, f"D=10: {errors} errors"
    dut._log.info("PASS: D=10, 1024 values exhaustively verified")


@cocotb.test()
async def test_exhaustive_d11(dut):
    """Exhaustive test for D=11: 2048 values."""
    errors = 0
    for y in range(2048):
        result, expected, match = await drive_and_check(dut, y, 11)
        if not match:
            dut._log.error(f"FAIL D=11: decompress({y}) = {result}, expected {expected}")
            errors += 1
        assert result < KYBER_Q, f"D=11: output {result} >= q for y={y}"
    assert errors == 0, f"D=11: {errors} errors"
    dut._log.info("PASS: D=11, 2048 values exhaustively verified")
