"""Testbench for cbd_sampler module.

Tests CBD η=2 sampling: 128 bytes → 256 coefficients in [0, q-1].
  1. All nibbles: 8 bytes covering all 16 nibble values
  2. Random bytes: 3 random 128-byte blocks vs oracle
  3. Known vectors: all-zeros, all-0xFF, all-0x0F, all-0x03
  4. Timing: byte_valid always high → exactly 129 cycles
  5. Byte stall: random delays in byte_valid, correct results
"""

import sys
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import KYBER_Q, KYBER_N, cbd_sample_eta2


CLK_PERIOD_NS = 10


async def init(dut):
    """Start clock, assert reset, release."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit='ns').start())
    dut.rst_n.value = 0
    dut.start.value = 0
    dut.byte_valid.value = 0
    dut.byte_data.value = 0
    dut.r_addr.value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


async def run_sampler(dut, data, stall_pattern=None):
    """Start sampler, feed 128 bytes, wait for done. Returns cycle count.

    stall_pattern: if provided, a list of booleans (length 128).
    If stall_pattern[i] is True, deassert byte_valid for 1 cycle before byte i.
    """
    stalls = list(stall_pattern) if stall_pattern else None

    # Pulse start
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0

    byte_idx = 0
    cycles = 0
    presenting = False  # True when byte_valid is high with valid data

    while True:
        # Decide what to drive this cycle
        if byte_idx < 128:
            if stalls and stalls[byte_idx]:
                # Stall: deassert byte_valid for this cycle
                dut.byte_valid.value = 0
                stalls[byte_idx] = False
                presenting = False
            else:
                dut.byte_valid.value = 1
                dut.byte_data.value = data[byte_idx]
                presenting = True
        else:
            dut.byte_valid.value = 0
            presenting = False

        await RisingEdge(dut.clk)
        cycles += 1

        await FallingEdge(dut.clk)
        if dut.done.value == 1:
            dut.byte_valid.value = 0
            break

        # If we were presenting a byte and it was accepted, advance
        if presenting and dut.byte_ready.value == 1:
            byte_idx += 1

        if cycles > 2000:
            raise RuntimeError(
                f"Timeout: cbd_sampler did not assert done within 2000 cycles (byte_idx={byte_idx})"
            )

    return cycles


async def read_result(dut, count=256):
    """Read coefficients from result RAM via r_addr/r_dout."""
    result = []
    for addr in range(count):
        dut.r_addr.value = addr
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        result.append(dut.r_dout.value.to_unsigned())
    return result


def check_coeffs(dut, result, expected, label):
    """Compare result against expected, log errors."""
    errors = 0
    for i in range(len(expected)):
        if result[i] != expected[i]:
            dut._log.error(
                f"FAIL {label}: coeff[{i}] = {result[i]}, expected {expected[i]}"
            )
            errors += 1
            if errors >= 10:
                dut._log.error("(stopping after 10 errors)")
                break
    return errors


@cocotb.test()
async def test_all_nibbles(dut):
    """8 bytes covering all 16 nibble values, verify each coefficient."""
    await init(dut)

    test_bytes = [
        0x10,  # lo=0x0 → (0+0)-(0+0)=0,   hi=0x1 → (1+0)-(0+0)=1
        0x32,  # lo=0x2 → (0+1)-(0+0)=1,   hi=0x3 → (1+1)-(0+0)=2
        0x54,  # lo=0x4 → (0+0)-(1+0)=-1,  hi=0x5 → (1+0)-(1+0)=0
        0x76,  # lo=0x6 → (0+1)-(1+0)=0,   hi=0x7 → (1+1)-(1+0)=1
        0x98,  # lo=0x8 → (0+0)-(0+1)=-1,  hi=0x9 → (1+0)-(0+1)=0
        0xBA,  # lo=0xA → (0+1)-(0+1)=0,   hi=0xB → (1+1)-(0+1)=1
        0xDC,  # lo=0xC → (0+0)-(1+1)=-2,  hi=0xD → (1+0)-(1+1)=-1
        0xFE,  # lo=0xE → (0+1)-(1+1)=-1,  hi=0xF → (1+1)-(1+1)=0
    ]

    # Pad to 128 bytes
    padded = test_bytes + [0x00] * (128 - len(test_bytes))
    expected = cbd_sample_eta2(padded)

    await run_sampler(dut, padded)
    result = await read_result(dut)

    errors = check_coeffs(dut, result, expected, "all_nibbles")
    assert errors == 0, f"All-nibbles test: {errors} mismatches"

    dut._log.info(f"  First 16 coefficients: {result[:16]}")
    dut._log.info(f"  Expected:              {expected[:16]}")
    dut._log.info("PASS: All 16 nibble values verified")


@cocotb.test()
async def test_random_bytes(dut):
    """3 random 128-byte blocks vs cbd_sample_eta2 oracle."""
    await init(dut)

    rng = random.Random(42)

    for t in range(3):
        data = [rng.randint(0, 255) for _ in range(128)]
        expected = cbd_sample_eta2(data)

        await run_sampler(dut, data)
        result = await read_result(dut)

        errors = check_coeffs(dut, result, expected, f"random_{t}")
        assert errors == 0, f"Random test {t}: {errors} mismatches"
        dut._log.info(f"  Random test {t}: PASS")

    dut._log.info("PASS: 3 random 128-byte blocks verified against oracle")


@cocotb.test()
async def test_known_vectors(dut):
    """Known input patterns: all-zeros, all-0xFF, all-0x0F, all-0x03."""
    await init(dut)

    # All zeros: every nibble is 0x0, coeff = 0
    data = [0x00] * 128
    expected = cbd_sample_eta2(data)
    await run_sampler(dut, data)
    result = await read_result(dut)
    assert result == expected, "All-zeros: all coefficients should be 0"
    dut._log.info("  All-zeros: PASS")

    # All 0xFF: nibble=0xF → (1+1)-(1+1) = 0
    data = [0xFF] * 128
    expected = cbd_sample_eta2(data)
    await run_sampler(dut, data)
    result = await read_result(dut)
    assert result == expected, "All-0xFF: all coefficients should be 0"
    dut._log.info("  All-0xFF: PASS")

    # All 0x0F: lo=0xF → 0, hi=0x0 → 0
    data = [0x0F] * 128
    expected = cbd_sample_eta2(data)
    await run_sampler(dut, data)
    result = await read_result(dut)
    assert result == expected, "All-0x0F: all coefficients should be 0"
    dut._log.info("  All-0x0F: PASS")

    # All 0x03: lo=0x3 → (1+1)-(0+0)=+2, hi=0x0 → 0
    data = [0x03] * 128
    expected = cbd_sample_eta2(data)
    await run_sampler(dut, data)
    result = await read_result(dut)
    errors = check_coeffs(dut, result, expected, "all_0x03")
    assert errors == 0, f"All-0x03: {errors} mismatches"
    dut._log.info("  All-0x03 (lo=+2, hi=0): PASS")

    dut._log.info("PASS: Known vector tests verified")


@cocotb.test()
async def test_timing(dut):
    """byte_valid always high → exactly 129 cycles from start to done."""
    await init(dut)

    rng = random.Random(99)
    data = [rng.randint(0, 255) for _ in range(128)]

    cycles = await run_sampler(dut, data)

    # 128 RUN cycles + 1 DONE cycle = 129
    expected_cycles = 129
    assert cycles == expected_cycles, \
        f"Expected {expected_cycles} cycles, got {cycles}"
    dut._log.info(f"PASS: Timing verified ({cycles} cycles)")


@cocotb.test()
async def test_byte_stall(dut):
    """Insert random stalls in byte_valid, verify correct results."""
    await init(dut)

    rng = random.Random(7)
    data = [rng.randint(0, 255) for _ in range(128)]
    expected = cbd_sample_eta2(data)

    stall_rng = random.Random(13)
    stall_pattern = [stall_rng.random() < 0.3 for _ in range(128)]

    cycles = await run_sampler(dut, data, stall_pattern=stall_pattern)
    result = await read_result(dut)

    errors = check_coeffs(dut, result, expected, "byte_stall")
    assert errors == 0, f"Byte stall test: {errors} mismatches"
    dut._log.info(f"PASS: Byte stall test verified ({cycles} cycles with stalls)")
