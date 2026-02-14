"""Testbench for decaps_top module.

Tests the ML-KEM-768 decryption controller (decaps_ctrl) wired to
kyber_top via decaps_top. Verifies the full 32-micro-op sequence:
  Phase 0: Decompress u (D=10) and v (D=4)
  Phase 1: NTT(u)
  Phase 2: Inner product s_hat^T · u_hat
  Phase 3: INTT, v - w, compress D=1 → m'

Test strategy: Generate oracle keypairs and ciphertexts, load compressed
values into hardware, run decrypt, verify recovered message matches oracle.
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
    poly_add as oracle_add, poly_sub as oracle_sub,
    compress_q, decompress_q,
    cbd_sample_eta2, keygen_inner, encaps_inner, decrypt_inner,
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
    dut.decrypt_start.value = 0
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


def gen_keygen_encaps_data(rng):
    """Generate a full keygen + encaps dataset using oracle functions.

    Returns:
        s_hat: list of 3 NTT-domain polys (secret key)
        u_compressed: list of 3 polys, each compressed with D=10
        v_compressed: poly compressed with D=4
        m_bits: original message as 256 binary values
        m_prime_oracle: oracle-decrypted message
    """
    # KeyGen
    A_hat = [[gen_random_poly(rng) for j in range(3)] for i in range(3)]
    s_noise = [cbd_sample_eta2(gen_cbd_bytes(rng)) for _ in range(3)]
    e_noise = [cbd_sample_eta2(gen_cbd_bytes(rng)) for _ in range(3)]
    t_hat, s_hat = keygen_inner(A_hat, s_noise, e_noise)

    # Original message: 256 random bits, decompressed via D=1
    m_bits = [rng.randint(0, 1) for _ in range(KYBER_N)]
    m_decompressed = [decompress_q(b, 1) for b in m_bits]

    # Encaps: A_hat^T for encaps (transpose)
    A_hat_T = [[A_hat[j][i] for j in range(3)] for i in range(3)]
    r_noise = [cbd_sample_eta2(gen_cbd_bytes(rng)) for _ in range(3)]
    e1_noise = [cbd_sample_eta2(gen_cbd_bytes(rng)) for _ in range(3)]
    e2_noise = cbd_sample_eta2(gen_cbd_bytes(rng))

    # encaps_inner expects A_hat[j][i] (row j, col i) for A^T access
    # Since we're passing A_hat_T as [row][col], it accesses A_hat_T[j][i] = A[i][j]
    # Actually, encaps_inner does sum_j A_hat[j][i] * r_hat[j] for column i
    # So it wants A_hat in the original (non-transposed) layout
    u, v = encaps_inner(A_hat, t_hat, r_noise, e1_noise, e2_noise, m_decompressed)

    # Compress ciphertext
    u_compressed = [[compress_q(c, 10) for c in u[i]] for i in range(3)]
    v_compressed = [compress_q(c, 4) for c in v]

    # Oracle decrypt
    m_prime_oracle = decrypt_inner(s_hat, u_compressed, v_compressed)

    return s_hat, u_compressed, v_compressed, m_bits, m_prime_oracle


async def preload_decrypt_inputs(dut, s_hat, u_compressed, v_compressed):
    """Preload compressed ciphertext and secret key into bank slots."""
    # u[0..2] → slots 0-2
    for i in range(3):
        await write_poly(dut, i, u_compressed[i])
    # v → slot 3
    await write_poly(dut, 3, v_compressed)
    # s_hat[0..2] → slots 9-11
    for i in range(3):
        await write_poly(dut, 9 + i, s_hat[i])


async def run_decrypt(dut, timeout=100000):
    """Start decrypt and wait for done."""
    dut.decrypt_start.value = 1
    await RisingEdge(dut.clk)
    dut.decrypt_start.value = 0

    for cycle in range(timeout):
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        if dut.decrypt_done.value == 1:
            dut._log.info(f"Decrypt completed in ~{cycle + 1} cycles")
            return cycle + 1
    raise TimeoutError(f"Decrypt did not complete within {timeout} cycles")


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════

@cocotb.test()
async def test_decrypt_basic(dut):
    """Load oracle-generated compressed ciphertext + s_hat, verify m' matches oracle."""
    await init(dut)

    rng = random.Random(7000)
    s_hat, u_comp, v_comp, m_bits, m_prime_oracle = gen_keygen_encaps_data(rng)

    await preload_decrypt_inputs(dut, s_hat, u_comp, v_comp)
    await run_decrypt(dut)

    # Read m' from slot 4
    m_prime_hw = await read_poly(dut, 4)

    errors = compare_polys(m_prime_hw, m_prime_oracle, "m' (slot 4)", dut._log)
    assert errors == 0, f"Decrypt basic: {errors} mismatches"
    dut._log.info("PASS: Decrypt basic — m' matches oracle")


@cocotb.test()
async def test_decrypt_roundtrip(dut):
    """Full oracle keygen → oracle encaps → hardware decrypt, verify m' == original."""
    await init(dut)

    rng = random.Random(7001)
    s_hat, u_comp, v_comp, m_bits, m_prime_oracle = gen_keygen_encaps_data(rng)

    await preload_decrypt_inputs(dut, s_hat, u_comp, v_comp)
    await run_decrypt(dut)

    m_prime_hw = await read_poly(dut, 4)

    # The round-trip property: decrypt(encrypt(m)) should recover m
    # Due to compress/decompress lossy rounding, m_prime_oracle may differ
    # from m_bits in rare cases. Verify hardware matches oracle exactly.
    errors = compare_polys(m_prime_hw, m_prime_oracle, "roundtrip m'", dut._log)
    assert errors == 0, f"Round-trip: {errors} mismatches vs oracle"

    # Also check against original message (should match in most cases)
    bit_errors = sum(1 for i in range(KYBER_N) if m_prime_hw[i] != m_bits[i])
    dut._log.info(f"Round-trip: {bit_errors} bit errors vs original message (expected ~0)")
    # Kyber's parameters ensure negligible decryption failure probability
    assert bit_errors == 0, f"Round-trip failed: {bit_errors} bit errors"
    dut._log.info("PASS: Round-trip keygen → encaps → decrypt recovers original message")


@cocotb.test()
async def test_decrypt_zero_message(dut):
    """All-zero message round-trip (edge case)."""
    await init(dut)

    rng = random.Random(7002)

    # Generate keygen data
    A_hat = [[gen_random_poly(rng) for j in range(3)] for i in range(3)]
    s_noise = [cbd_sample_eta2(gen_cbd_bytes(rng)) for _ in range(3)]
    e_noise = [cbd_sample_eta2(gen_cbd_bytes(rng)) for _ in range(3)]
    t_hat, s_hat = keygen_inner(A_hat, s_noise, e_noise)

    # Zero message
    m_bits = [0] * KYBER_N
    m_decompressed = [decompress_q(0, 1)] * KYBER_N  # all decompress_q(0,1) = 0

    # Encaps
    r_noise = [cbd_sample_eta2(gen_cbd_bytes(rng)) for _ in range(3)]
    e1_noise = [cbd_sample_eta2(gen_cbd_bytes(rng)) for _ in range(3)]
    e2_noise = cbd_sample_eta2(gen_cbd_bytes(rng))
    u, v = encaps_inner(A_hat, t_hat, r_noise, e1_noise, e2_noise, m_decompressed)

    u_compressed = [[compress_q(c, 10) for c in u[i]] for i in range(3)]
    v_compressed = [compress_q(c, 4) for c in v]

    m_prime_oracle = decrypt_inner(s_hat, u_compressed, v_compressed)

    await preload_decrypt_inputs(dut, s_hat, u_compressed, v_compressed)
    await run_decrypt(dut)

    m_prime_hw = await read_poly(dut, 4)

    errors = compare_polys(m_prime_hw, m_prime_oracle, "zero msg m'", dut._log)
    assert errors == 0, f"Zero message: {errors} mismatches vs oracle"

    # Verify all zeros recovered
    nonzero = sum(1 for c in m_prime_hw if c != 0)
    assert nonzero == 0, f"Zero message: {nonzero} non-zero coefficients in m'"
    dut._log.info("PASS: Zero message round-trip correct")


@cocotb.test()
async def test_decrypt_two_runs(dut):
    """Back-to-back decrypts with different inputs."""
    await init(dut)

    # First run
    rng1 = random.Random(7003)
    s_hat1, u_comp1, v_comp1, m_bits1, m_prime1 = gen_keygen_encaps_data(rng1)
    await preload_decrypt_inputs(dut, s_hat1, u_comp1, v_comp1)
    await run_decrypt(dut)
    m1_hw = await read_poly(dut, 4)

    errors = compare_polys(m1_hw, m_prime1, "run1 m'", dut._log)
    assert errors == 0, f"First decrypt: {errors} mismatches"

    # Second run
    rng2 = random.Random(7004)
    s_hat2, u_comp2, v_comp2, m_bits2, m_prime2 = gen_keygen_encaps_data(rng2)
    await preload_decrypt_inputs(dut, s_hat2, u_comp2, v_comp2)
    await run_decrypt(dut)
    m2_hw = await read_poly(dut, 4)

    errors = compare_polys(m2_hw, m_prime2, "run2 m'", dut._log)
    assert errors == 0, f"Second decrypt: {errors} mismatches"

    dut._log.info("PASS: Two decrypt runs with different inputs both verified")


@cocotb.test()
async def test_decrypt_idle_after_done(dut):
    """Verify host I/O works after decrypt completes."""
    await init(dut)

    rng = random.Random(7005)
    s_hat, u_comp, v_comp, m_bits, m_prime = gen_keygen_encaps_data(rng)

    await preload_decrypt_inputs(dut, s_hat, u_comp, v_comp)
    await run_decrypt(dut)

    # Write a known polynomial to a free slot and read it back
    test_poly = gen_random_poly(rng)
    await write_poly(dut, 15, test_poly)
    result = await read_poly(dut, 15)

    errors = compare_polys(result, test_poly, "idle_after_done", dut._log)
    assert errors == 0, f"Host I/O after decrypt: {errors} mismatches"
    dut._log.info("PASS: Host I/O works after decrypt completes")
