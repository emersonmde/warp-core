"""Testbench for compress module.

Exhaustively tests Compress_q(x, d) = round(2^d * x / q) mod 2^d for all 5
Kyber D values. Total vectors: 3329 × 5 = 16,645.

Also verifies the round-trip property:
    |decompress(compress(x, d), d) - x| <= ceil(q / 2^(d+1))
"""

import sys
import os
import math

import cocotb
from cocotb.triggers import Timer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import KYBER_Q, compress_q, decompress_q


D_VALUES = [1, 4, 5, 10, 11]
RESULT_SIGNALS = {
    1:  'result_d1',
    4:  'result_d4',
    5:  'result_d5',
    10: 'result_d10',
    11: 'result_d11',
}


async def drive_and_check(dut, x, d):
    """Drive x input, wait for combinational settle, check result for given D."""
    dut.x.value = x
    await Timer(1, unit='ns')
    sig = getattr(dut, RESULT_SIGNALS[d])
    val = sig.value
    result = int(val == 1) if d == 1 else val.to_unsigned()
    expected = compress_q(x, d)
    return result, expected, result == expected


@cocotb.test()
async def test_exhaustive_d1(dut):
    """Exhaustive test for D=1: 3329 values."""
    errors = 0
    for x in range(KYBER_Q):
        result, expected, match = await drive_and_check(dut, x, 1)
        if not match:
            dut._log.error(f"FAIL D=1: compress({x}) = {result}, expected {expected}")
            errors += 1
    assert errors == 0, f"D=1: {errors} errors out of {KYBER_Q}"
    dut._log.info(f"PASS: D=1, {KYBER_Q} values exhaustively verified")


@cocotb.test()
async def test_exhaustive_d4(dut):
    """Exhaustive test for D=4: 3329 values."""
    errors = 0
    for x in range(KYBER_Q):
        result, expected, match = await drive_and_check(dut, x, 4)
        if not match:
            dut._log.error(f"FAIL D=4: compress({x}) = {result}, expected {expected}")
            errors += 1
    assert errors == 0, f"D=4: {errors} errors out of {KYBER_Q}"
    dut._log.info(f"PASS: D=4, {KYBER_Q} values exhaustively verified")


@cocotb.test()
async def test_exhaustive_d5(dut):
    """Exhaustive test for D=5: 3329 values."""
    errors = 0
    for x in range(KYBER_Q):
        result, expected, match = await drive_and_check(dut, x, 5)
        if not match:
            dut._log.error(f"FAIL D=5: compress({x}) = {result}, expected {expected}")
            errors += 1
    assert errors == 0, f"D=5: {errors} errors out of {KYBER_Q}"
    dut._log.info(f"PASS: D=5, {KYBER_Q} values exhaustively verified")


@cocotb.test()
async def test_exhaustive_d10(dut):
    """Exhaustive test for D=10: 3329 values."""
    errors = 0
    for x in range(KYBER_Q):
        result, expected, match = await drive_and_check(dut, x, 10)
        if not match:
            dut._log.error(f"FAIL D=10: compress({x}) = {result}, expected {expected}")
            errors += 1
    assert errors == 0, f"D=10: {errors} errors out of {KYBER_Q}"
    dut._log.info(f"PASS: D=10, {KYBER_Q} values exhaustively verified")


@cocotb.test()
async def test_exhaustive_d11(dut):
    """Exhaustive test for D=11: 3329 values."""
    errors = 0
    for x in range(KYBER_Q):
        result, expected, match = await drive_and_check(dut, x, 11)
        if not match:
            dut._log.error(f"FAIL D=11: compress({x}) = {result}, expected {expected}")
            errors += 1
    assert errors == 0, f"D=11: {errors} errors out of {KYBER_Q}"
    dut._log.info(f"PASS: D=11, {KYBER_Q} values exhaustively verified")


@cocotb.test()
async def test_round_trip(dut):
    """Verify decompress(compress(x, d), d) ≈ x for all x, all D values.

    The maximum round-trip error is ceil(q / 2^(d+1)).
    """
    errors = 0
    for d in D_VALUES:
        max_error = math.ceil(KYBER_Q / (1 << (d + 1)))
        worst_error = 0
        for x in range(KYBER_Q):
            dut.x.value = x
            await Timer(1, unit='ns')
            sig = getattr(dut, RESULT_SIGNALS[d])
            val = sig.value
            compressed = int(val == 1) if d == 1 else val.to_unsigned()
            decompressed = decompress_q(compressed, d)
            # Error wraps around mod q
            err = min(abs(decompressed - x), KYBER_Q - abs(decompressed - x))
            worst_error = max(worst_error, err)
            if err > max_error:
                dut._log.error(
                    f"FAIL round-trip D={d}: x={x}, compressed={compressed}, "
                    f"decompressed={decompressed}, error={err}, max_allowed={max_error}"
                )
                errors += 1
        dut._log.info(f"  D={d}: worst round-trip error = {worst_error} (max allowed = {max_error})")

    assert errors == 0, f"Round-trip test: {errors} errors"
    dut._log.info(f"PASS: Round-trip verified for all {KYBER_Q} values × {len(D_VALUES)} D values")
