"""Testbench for encaps_top module.

Tests the ML-KEM-768 encapsulation controller (encaps_ctrl) wired to
kyber_top via encaps_top. Verifies the full 93-micro-op sequence:
  Phase 0: CBD noise sampling (7 polys)
  Phase 1: NTT(r)
  Phase 2: A_hat^T * r_hat + e1 → u
  Phase 3: t_hat^T * r_hat + e2 + m → v
  Phase 4: Compress u (D=10) and v (D=4)

Test strategy: Use deterministic random seeds for all inputs (A_hat, t_hat,
m, CBD bytes). Feed CBD bytes via handshake. Compare hardware results
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
    ntt_forward, ntt_inverse,
    poly_basemul as oracle_basemul,
    poly_add as oracle_add,
    compress_q, cbd_sample_eta2, encaps_inner,
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
    dut.encaps_start.value = 0
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
    """Background coroutine: feed CBD bytes via valid/ready handshake.

    all_cbd_bytes: flat list of all bytes to feed (7 × 128 = 896 bytes).
    Feeds one byte per cycle when ready is asserted.
    """
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


async def run_encaps(dut, all_cbd_bytes, timeout=200000):
    """Start encapsulation and wait for done, feeding CBD bytes in background."""
    # Start CBD byte feeder
    feeder = cocotb.start_soon(
        feed_cbd_bytes_background(dut, all_cbd_bytes, dut._log)
    )

    # Pulse encaps_start
    dut.encaps_start.value = 1
    await RisingEdge(dut.clk)
    dut.encaps_start.value = 0

    # Wait for encaps_done
    for cycle in range(timeout):
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        if dut.encaps_done.value == 1:
            dut._log.info(f"Encaps completed in ~{cycle + 1} cycles")
            return cycle + 1
    raise TimeoutError(f"Encaps did not complete within {timeout} cycles")


def setup_encaps_inputs(rng):
    """Generate all encaps inputs from a deterministic RNG.

    Returns:
        A_hat: 3x3 list of NTT-domain polys
        t_hat: list of 3 NTT-domain polys
        m: decompressed message poly
        cbd_bytes_list: list of 7 lists of 128 bytes each
        all_cbd_bytes: flat list of 896 bytes
        r, e1, e2: sampled noise polys (from oracle)
    """
    # Generate A_hat[j][i] — 9 NTT-domain polys
    A_hat = [[gen_random_poly(rng) for i in range(3)] for j in range(3)]

    # Generate t_hat[0..2] — 3 NTT-domain polys
    t_hat = [gen_random_poly(rng) for _ in range(3)]

    # Message polynomial (as if decompressed from 32 bytes)
    m = gen_random_poly(rng)

    # CBD bytes: 7 × 128 bytes = 896 bytes total
    # Order: r[0], r[1], r[2], e1[0], e1[1], e1[2], e2
    cbd_bytes_list = [gen_cbd_bytes(rng) for _ in range(7)]
    all_cbd_bytes = []
    for b in cbd_bytes_list:
        all_cbd_bytes.extend(b)

    # Compute oracle noise polynomials
    r = [cbd_sample_eta2(cbd_bytes_list[i]) for i in range(3)]
    e1 = [cbd_sample_eta2(cbd_bytes_list[3 + i]) for i in range(3)]
    e2 = cbd_sample_eta2(cbd_bytes_list[6])

    return A_hat, t_hat, m, cbd_bytes_list, all_cbd_bytes, r, e1, e2


async def preload_inputs(dut, A_hat, t_hat, m):
    """Preload A_hat, t_hat, and m into the bank slots."""
    # A_hat[j][i] → slot j*3+i
    for j in range(3):
        for i in range(3):
            await write_poly(dut, j * 3 + i, A_hat[j][i])

    # t_hat[0..2] → slots 9-11
    for k in range(3):
        await write_poly(dut, 9 + k, t_hat[k])

    # m → slot 12
    await write_poly(dut, 12, m)


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════

@cocotb.test()
async def test_encaps_basic(dut):
    """Full encapsulation: verify u[0..2] and v against oracle."""
    await init(dut)

    rng = random.Random(5000)
    A_hat, t_hat, m, _, all_cbd_bytes, r, e1, e2 = setup_encaps_inputs(rng)

    # Preload inputs
    await preload_inputs(dut, A_hat, t_hat, m)

    # Run encapsulation
    await run_encaps(dut, all_cbd_bytes)

    # Compute oracle results
    u_oracle, v_oracle = encaps_inner(A_hat, t_hat, r, e1, e2, m)

    # Verify uncompressed u[0..2] in slots 0-2
    total_errors = 0
    for i in range(3):
        result = await read_poly(dut, i)
        errors = compare_polys(result, u_oracle[i], f"u[{i}] (slot {i})", dut._log)
        total_errors += errors

    # Verify uncompressed v in slot 9
    result_v = await read_poly(dut, 9)
    errors = compare_polys(result_v, v_oracle, "v (slot 9)", dut._log)
    total_errors += errors

    assert total_errors == 0, f"Encaps basic: {total_errors} total mismatches"
    dut._log.info("PASS: Encaps basic — u[0..2] and v match oracle")


@cocotb.test()
async def test_encaps_compress_values(dut):
    """Verify compressed output slots match compress_q(oracle, D)."""
    await init(dut)

    rng = random.Random(5001)
    A_hat, t_hat, m, _, all_cbd_bytes, r, e1, e2 = setup_encaps_inputs(rng)

    await preload_inputs(dut, A_hat, t_hat, m)
    await run_encaps(dut, all_cbd_bytes)

    u_oracle, v_oracle = encaps_inner(A_hat, t_hat, r, e1, e2, m)

    # Verify compressed u[0..2] in slots 16-18 (D=10)
    total_errors = 0
    for i in range(3):
        result = await read_poly(dut, 16 + i)
        expected = [compress_q(c, 10) for c in u_oracle[i]]
        errors = compare_polys(result, expected, f"compress_u[{i}] (slot {16+i})", dut._log)
        total_errors += errors

    # Verify compressed v in slot 19 (D=4)
    result_v = await read_poly(dut, 19)
    expected_v = [compress_q(c, 4) for c in v_oracle]
    errors = compare_polys(result_v, expected_v, "compress_v (slot 19)", dut._log)
    total_errors += errors

    assert total_errors == 0, f"Encaps compress: {total_errors} total mismatches"
    dut._log.info("PASS: Encaps compress values verified against oracle")


@cocotb.test()
async def test_encaps_slot_preservation(dut):
    """Verify r_hat (slots 13-15) survive through Phases 2-3 (read-only)."""
    await init(dut)

    rng = random.Random(5002)
    A_hat, t_hat, m, _, all_cbd_bytes, r, e1, e2 = setup_encaps_inputs(rng)

    await preload_inputs(dut, A_hat, t_hat, m)
    await run_encaps(dut, all_cbd_bytes)

    # r_hat = NTT(r) — computed by oracle
    r_hat_oracle = [ntt_forward(r[i]) for i in range(3)]

    total_errors = 0
    for i in range(3):
        result = await read_poly(dut, 13 + i)
        errors = compare_polys(result, r_hat_oracle[i], f"r_hat[{i}] (slot {13+i})", dut._log)
        total_errors += errors

    assert total_errors == 0, f"Slot preservation: {total_errors} mismatches"
    dut._log.info("PASS: r_hat slots 13-15 preserved through matmul phases")


@cocotb.test()
async def test_encaps_two_runs(dut):
    """Run encaps twice with different randomness, verify different outputs."""
    await init(dut)

    # First run
    rng1 = random.Random(5003)
    A_hat1, t_hat1, m1, _, cbd1, r1, e11, e21 = setup_encaps_inputs(rng1)
    await preload_inputs(dut, A_hat1, t_hat1, m1)
    await run_encaps(dut, cbd1)

    u1_oracle, v1_oracle = encaps_inner(A_hat1, t_hat1, r1, e11, e21, m1)
    u1_hw = []
    for i in range(3):
        u1_hw.append(await read_poly(dut, i))
    v1_hw = await read_poly(dut, 9)

    # Verify first run
    total_errors = 0
    for i in range(3):
        total_errors += compare_polys(u1_hw[i], u1_oracle[i], f"run1 u[{i}]", dut._log)
    total_errors += compare_polys(v1_hw, v1_oracle, "run1 v", dut._log)
    assert total_errors == 0, f"First run: {total_errors} mismatches"

    # Second run with different inputs
    rng2 = random.Random(5004)
    A_hat2, t_hat2, m2, _, cbd2, r2, e12, e22 = setup_encaps_inputs(rng2)
    await preload_inputs(dut, A_hat2, t_hat2, m2)
    await run_encaps(dut, cbd2)

    u2_oracle, v2_oracle = encaps_inner(A_hat2, t_hat2, r2, e12, e22, m2)
    u2_hw = []
    for i in range(3):
        u2_hw.append(await read_poly(dut, i))
    v2_hw = await read_poly(dut, 9)

    # Verify second run
    total_errors = 0
    for i in range(3):
        total_errors += compare_polys(u2_hw[i], u2_oracle[i], f"run2 u[{i}]", dut._log)
    total_errors += compare_polys(v2_hw, v2_oracle, "run2 v", dut._log)
    assert total_errors == 0, f"Second run: {total_errors} mismatches"

    # Verify the two runs produced different results
    same_count = sum(1 for i in range(KYBER_N) if u1_hw[0][i] == u2_hw[0][i])
    assert same_count < KYBER_N, "Two runs with different inputs produced identical u[0]"

    dut._log.info("PASS: Two encaps runs with different inputs both verified")


@cocotb.test()
async def test_encaps_idle_after_done(dut):
    """Verify host I/O works after encaps completes."""
    await init(dut)

    rng = random.Random(5005)
    A_hat, t_hat, m, _, all_cbd_bytes, r, e1, e2 = setup_encaps_inputs(rng)

    await preload_inputs(dut, A_hat, t_hat, m)
    await run_encaps(dut, all_cbd_bytes)

    # Write a known polynomial to a free slot and read it back
    test_poly = gen_random_poly(rng)
    await write_poly(dut, 3, test_poly)  # slot 3 was consumed by Phase 2
    result = await read_poly(dut, 3)

    errors = compare_polys(result, test_poly, "idle_after_done", dut._log)
    assert errors == 0, f"Host I/O after encaps: {errors} mismatches"
    dut._log.info("PASS: Host I/O works after encaps completes")


@cocotb.test()
async def test_encaps_message_slot(dut):
    """Verify message slot 12 is not corrupted during encaps."""
    await init(dut)

    rng = random.Random(5006)
    A_hat, t_hat, m, _, all_cbd_bytes, r, e1, e2 = setup_encaps_inputs(rng)

    await preload_inputs(dut, A_hat, t_hat, m)
    await run_encaps(dut, all_cbd_bytes)

    # Slot 12 (message) is only read during Phase 3, never written
    result_m = await read_poly(dut, 12)
    errors = compare_polys(result_m, m, "message (slot 12)", dut._log)
    assert errors == 0, f"Message slot: {errors} mismatches"
    dut._log.info("PASS: Message slot 12 preserved through encaps")
