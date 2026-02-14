"""Testbench for keygen_top module.

Tests the ML-KEM-768 key generation controller (keygen_ctrl) wired to
kyber_top via keygen_top. Verifies the full 69-micro-op sequence:
  Phase 0: CBD noise sampling (6 polys: s[3] + e[3])
  Phase 1: NTT (6 polys: s[3] + e[3])
  Phase 2-4: Matmul rows 0-2: A[i] · s_hat + e_hat[i] → t_hat[i]

Test strategy: Use deterministic random seeds for all inputs (A_hat,
CBD bytes). Feed CBD bytes via handshake. Compare hardware results
against Python oracle.
"""

import sys
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge

# Add ref/ to path for oracle functions
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import (
    KYBER_Q, KYBER_N,
    ntt_forward,
    cbd_sample_eta2, keygen_inner,
)

CLK_PERIOD_NS = 10


async def init(dut):
    """Start clock, assert reset, release."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit='ns').start())
    dut.rst_n.value = 0
    dut.host_we.value = 0
    dut.host_slot.value = 0
    dut.host_addr.value = 0
    dut.host_din.value = 0
    dut.keygen_start.value = 0
    dut.cbd_byte_valid.value = 0
    dut.cbd_byte_data.value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


async def write_poly(dut, slot, coeffs):
    """Write 256 coefficients to a slot via host interface."""
    for addr in range(KYBER_N):
        dut.host_we.value = 1
        dut.host_slot.value = slot
        dut.host_addr.value = addr
        dut.host_din.value = coeffs[addr]
        await RisingEdge(dut.clk)
    dut.host_we.value = 0
    await RisingEdge(dut.clk)


async def read_poly(dut, slot):
    """Read 256 coefficients from a slot (synchronous RAM: sample on FallingEdge)."""
    result = []
    for addr in range(KYBER_N):
        dut.host_slot.value = slot
        dut.host_addr.value = addr
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        result.append(dut.host_dout.value.to_unsigned())
    return result


def compare_polys(got, expected, label, log, max_errors=10):
    """Compare two polynomials, return error count."""
    errors = 0
    for i in range(KYBER_N):
        if got[i] != expected[i]:
            log.error(f"FAIL {label}: coeff[{i}] = {got[i]}, expected {expected[i]}")
            errors += 1
            if errors >= max_errors:
                log.error("(stopping after max errors)")
                break
    return errors


def gen_random_poly(rng):
    """Generate a random polynomial in [0, q-1]."""
    return [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]


def gen_cbd_bytes(rng):
    """Generate 128 random bytes for CBD sampling."""
    return [rng.randint(0, 255) for _ in range(128)]


async def feed_cbd_bytes_background(dut, all_cbd_bytes, log):
    """Background coroutine: feed CBD bytes via valid/ready handshake."""
    byte_idx = 0
    total = len(all_cbd_bytes)
    while byte_idx < total:
        dut.cbd_byte_valid.value = 1
        dut.cbd_byte_data.value = all_cbd_bytes[byte_idx]
        await FallingEdge(dut.clk)
        if dut.cbd_byte_ready.value == 1:
            byte_idx += 1
        await RisingEdge(dut.clk)
    dut.cbd_byte_valid.value = 0
    log.info(f"CBD feeder: all {total} bytes delivered")


async def run_keygen(dut, all_cbd_bytes, timeout=200000):
    """Start keygen and wait for done, feeding CBD bytes in background."""
    feeder = cocotb.start_soon(
        feed_cbd_bytes_background(dut, all_cbd_bytes, dut._log)
    )

    # Pulse keygen_start
    dut.keygen_start.value = 1
    await RisingEdge(dut.clk)
    dut.keygen_start.value = 0

    # Wait for keygen_done
    for cycle in range(timeout):
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        if dut.keygen_done.value == 1:
            dut._log.info(f"KeyGen completed in ~{cycle + 1} cycles")
            return cycle + 1
    raise TimeoutError(f"KeyGen did not complete within {timeout} cycles")


def setup_keygen_inputs(rng):
    """Generate all keygen inputs from a deterministic RNG.

    Returns:
        A_hat: 3x3 list of NTT-domain polys (A_hat[i][j] = row i, col j)
        cbd_bytes_list: list of 6 lists of 128 bytes each
        all_cbd_bytes: flat list of 768 bytes
        s_noise, e_noise: sampled noise polys (from oracle)
    """
    # Generate A_hat[i][j] — 9 NTT-domain polys (row-major)
    A_hat = [[gen_random_poly(rng) for j in range(3)] for i in range(3)]

    # CBD bytes: 6 × 128 bytes = 768 bytes total
    # Order: s[0], s[1], s[2], e[0], e[1], e[2]
    cbd_bytes_list = [gen_cbd_bytes(rng) for _ in range(6)]
    all_cbd_bytes = []
    for b in cbd_bytes_list:
        all_cbd_bytes.extend(b)

    # Compute oracle noise polynomials
    s_noise = [cbd_sample_eta2(cbd_bytes_list[i]) for i in range(3)]
    e_noise = [cbd_sample_eta2(cbd_bytes_list[3 + i]) for i in range(3)]

    return A_hat, cbd_bytes_list, all_cbd_bytes, s_noise, e_noise


async def preload_a_hat(dut, A_hat):
    """Preload A_hat[i][j] into bank slots (row-major: slot = i*3+j)."""
    for i in range(3):
        for j in range(3):
            await write_poly(dut, i * 3 + j, A_hat[i][j])


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════

@cocotb.test()
async def test_keygen_basic(dut):
    """Full keygen: verify t_hat (slots 0,3,6) and s_hat (slots 9-11) match oracle."""
    await init(dut)

    rng = random.Random(6000)
    A_hat, _, all_cbd_bytes, s_noise, e_noise = setup_keygen_inputs(rng)

    # Preload A_hat
    await preload_a_hat(dut, A_hat)

    # Run keygen
    await run_keygen(dut, all_cbd_bytes)

    # Compute oracle results
    t_hat_oracle, s_hat_oracle = keygen_inner(A_hat, s_noise, e_noise)

    # Verify t_hat[0..2] in slots 0, 3, 6
    total_errors = 0
    t_hat_slots = [0, 3, 6]
    for idx, slot in enumerate(t_hat_slots):
        result = await read_poly(dut, slot)
        errors = compare_polys(result, t_hat_oracle[idx], f"t_hat[{idx}] (slot {slot})", dut._log)
        total_errors += errors

    # Verify s_hat[0..2] in slots 9-11
    for i in range(3):
        result = await read_poly(dut, 9 + i)
        errors = compare_polys(result, s_hat_oracle[i], f"s_hat[{i}] (slot {9+i})", dut._log)
        total_errors += errors

    assert total_errors == 0, f"KeyGen basic: {total_errors} total mismatches"
    dut._log.info("PASS: KeyGen basic — t_hat and s_hat match oracle")


@cocotb.test()
async def test_keygen_s_hat_preservation(dut):
    """Verify s_hat slots (9-11) survive through matmul phases (read-only)."""
    await init(dut)

    rng = random.Random(6001)
    A_hat, _, all_cbd_bytes, s_noise, e_noise = setup_keygen_inputs(rng)

    await preload_a_hat(dut, A_hat)
    await run_keygen(dut, all_cbd_bytes)

    # s_hat = NTT(s_noise)
    s_hat_oracle = [ntt_forward(s_noise[i]) for i in range(3)]

    total_errors = 0
    for i in range(3):
        result = await read_poly(dut, 9 + i)
        errors = compare_polys(result, s_hat_oracle[i], f"s_hat[{i}] (slot {9+i})", dut._log)
        total_errors += errors

    assert total_errors == 0, f"s_hat preservation: {total_errors} mismatches"
    dut._log.info("PASS: s_hat slots 9-11 preserved through matmul phases")


@cocotb.test()
async def test_keygen_two_runs(dut):
    """Run keygen twice with different seeds, verify different outputs."""
    await init(dut)

    # First run
    rng1 = random.Random(6002)
    A_hat1, _, cbd1, s1, e1 = setup_keygen_inputs(rng1)
    await preload_a_hat(dut, A_hat1)
    await run_keygen(dut, cbd1)

    t_hat1_oracle, s_hat1_oracle = keygen_inner(A_hat1, s1, e1)
    t1_hw = await read_poly(dut, 0)

    total_errors = 0
    total_errors += compare_polys(t1_hw, t_hat1_oracle[0], "run1 t_hat[0]", dut._log)
    assert total_errors == 0, f"First run: {total_errors} mismatches"

    # Second run with different inputs
    rng2 = random.Random(6003)
    A_hat2, _, cbd2, s2, e2 = setup_keygen_inputs(rng2)
    await preload_a_hat(dut, A_hat2)
    await run_keygen(dut, cbd2)

    t_hat2_oracle, s_hat2_oracle = keygen_inner(A_hat2, s2, e2)
    t2_hw = await read_poly(dut, 0)

    total_errors = 0
    total_errors += compare_polys(t2_hw, t_hat2_oracle[0], "run2 t_hat[0]", dut._log)
    assert total_errors == 0, f"Second run: {total_errors} mismatches"

    # Verify different results
    same_count = sum(1 for i in range(KYBER_N) if t1_hw[i] == t2_hw[i])
    assert same_count < KYBER_N, "Two runs with different inputs produced identical t_hat[0]"

    dut._log.info("PASS: Two keygen runs with different inputs both verified")


@cocotb.test()
async def test_keygen_idle_after_done(dut):
    """Verify host I/O works after keygen completes."""
    await init(dut)

    rng = random.Random(6004)
    A_hat, _, all_cbd_bytes, s_noise, e_noise = setup_keygen_inputs(rng)

    await preload_a_hat(dut, A_hat)
    await run_keygen(dut, all_cbd_bytes)

    # Write a known polynomial to a free slot and read it back
    test_poly = gen_random_poly(rng)
    await write_poly(dut, 15, test_poly)  # slot 15 is unused
    result = await read_poly(dut, 15)

    errors = compare_polys(result, test_poly, "idle_after_done", dut._log)
    assert errors == 0, f"Host I/O after keygen: {errors} mismatches"
    dut._log.info("PASS: Host I/O works after keygen completes")


@cocotb.test()
async def test_keygen_e_hat_preservation(dut):
    """Verify e_hat (slots 12-14) not corrupted by matmul (only read, never written)."""
    await init(dut)

    rng = random.Random(6005)
    A_hat, _, all_cbd_bytes, s_noise, e_noise = setup_keygen_inputs(rng)

    await preload_a_hat(dut, A_hat)
    await run_keygen(dut, all_cbd_bytes)

    # e_hat = NTT(e_noise)
    e_hat_oracle = [ntt_forward(e_noise[i]) for i in range(3)]

    total_errors = 0
    for i in range(3):
        result = await read_poly(dut, 12 + i)
        errors = compare_polys(result, e_hat_oracle[i], f"e_hat[{i}] (slot {12+i})", dut._log)
        total_errors += errors

    assert total_errors == 0, f"e_hat preservation: {total_errors} mismatches"
    dut._log.info("PASS: e_hat slots 12-14 preserved through matmul phases")
