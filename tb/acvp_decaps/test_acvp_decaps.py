"""ACVP compliance test for decaps_top.

Iterates through all ML-KEM-768 decapsulation ACVP vectors. Per vector:
  1. Python: Parse dk -> dk_pke, ek, h, z. Parse c -> c1, c2.
     ByteDecode -> s_hat[3], u_compressed[3], v_compressed.
  2. Hardware: Load s_hat(9-11), u_compressed(0-2), v_compressed(3).
     Start decrypt.
  3. Readback: m' from slot 4.
  4. Python FO: m_bytes = ByteEncode(1, m'). (K', r') = G(m_bytes || h).
     c' = k_pke_encrypt(ek, m_bytes, r'). K_bar = J(z || c).
     K = K' if c==c' else K_bar.
  5. Compare K against ACVP expected.
"""

import json
import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import KYBER_Q, KYBER_N
from kyber_acvp import (
    K, DU, DV, DK_PKE_LEN, EK_LEN,
    byte_encode, byte_decode, g_hash, j_hash, k_pke_encrypt,
)

CLK_PERIOD_NS = 10
PARAM_SET = "ML-KEM-768"
VECTORS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'ref', 'acvp_vectors')


def load_vectors():
    """Load cached ACVP decapsulation vectors."""
    prompt_path = os.path.join(VECTORS_DIR, "encapdecap_prompt.json")
    results_path = os.path.join(VECTORS_DIR, "encapdecap_results.json")

    assert os.path.exists(prompt_path), \
        f"ACVP vectors not cached. Run: python ref/test_acvp_oracle.py"

    with open(prompt_path) as f:
        prompt = json.load(f)
    with open(results_path) as f:
        results = json.load(f)

    # Find ML-KEM-768 decapsulation test groups
    prompt_groups = {g["tgId"]: g for g in prompt["testGroups"]
                     if g.get("parameterSet") == PARAM_SET
                     and g.get("function") == "decapsulation"}
    results_groups = {g["tgId"]: g for g in results["testGroups"]
                      if g["tgId"] in prompt_groups}

    vectors = []
    for tg_id in sorted(prompt_groups.keys()):
        pg = prompt_groups[tg_id]
        rmap = {tc["tcId"]: tc for tc in results_groups[tg_id]["tests"]}
        for tc in pg["tests"]:
            exp = rmap[tc["tcId"]]
            vectors.append({
                "tcId": tc["tcId"],
                "dk": bytes.fromhex(tc["dk"]),
                "c": bytes.fromhex(tc["c"]),
                "expected_k": bytes.fromhex(exp["k"]),
            })
    return vectors


async def init(dut):
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
    for addr in range(KYBER_N):
        dut.host_we.value = 1
        dut.host_slot.value = slot
        dut.host_addr.value = addr
        dut.host_din.value = coeffs[addr]
        await RisingEdge(dut.clk)
    dut.host_we.value = 0
    await RisingEdge(dut.clk)


async def read_poly(dut, slot):
    result = []
    for addr in range(KYBER_N):
        dut.host_slot.value = slot
        dut.host_addr.value = addr
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        result.append(dut.host_dout.value.to_unsigned())
    return result


async def run_decrypt(dut, timeout=100000):
    dut.decrypt_start.value = 1
    await RisingEdge(dut.clk)
    dut.decrypt_start.value = 0

    for cycle in range(timeout):
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        if dut.decrypt_done.value == 1:
            return cycle + 1
    raise TimeoutError(f"Decrypt did not complete within {timeout} cycles")


@cocotb.test()
async def test_acvp_decaps_vectors(dut):
    """Run all ML-KEM-768 ACVP decapsulation vectors through hardware."""
    vectors = load_vectors()
    dut._log.info(f"Loaded {len(vectors)} ACVP decapsulation vectors")

    await init(dut)

    total_errors = 0

    for vec in vectors:
        tc_id = vec["tcId"]
        dk = vec["dk"]
        c = vec["c"]
        expected_k = vec["expected_k"]

        # Parse dk: dk_pke || ek || h || z
        dk_pke = dk[:DK_PKE_LEN]
        ek = dk[DK_PKE_LEN : DK_PKE_LEN + EK_LEN]
        h = dk[DK_PKE_LEN + EK_LEN : DK_PKE_LEN + EK_LEN + 32]
        z = dk[DK_PKE_LEN + EK_LEN + 32:]

        # Parse ciphertext: c1 (960 bytes) || c2 (128 bytes)
        c1_len = 32 * DU * K  # 960
        c1 = c[:c1_len]
        c2 = c[c1_len:]

        # Decode s_hat from dk_pke
        s_hat = []
        for i in range(K):
            s_hat.append(byte_decode(12, dk_pke[384 * i : 384 * (i + 1)]))

        # Decode compressed ciphertext coefficients
        u_compressed = []
        for i in range(K):
            u_compressed.append(byte_decode(DU, c1[32 * DU * i : 32 * DU * (i + 1)]))
        v_compressed = byte_decode(DV, c2)

        # Load u_compressed[0..2] into slots 0-2
        for i in range(K):
            await write_poly(dut, i, u_compressed[i])

        # Load v_compressed into slot 3
        await write_poly(dut, 3, v_compressed)

        # Load s_hat[0..2] into slots 9-11
        for i in range(K):
            await write_poly(dut, 9 + i, s_hat[i])

        # Run decrypt
        cycles = await run_decrypt(dut)
        dut._log.info(f"  tcId={tc_id}: decrypt completed in {cycles} cycles")

        # Read back m' from slot 4 (256 values in {0, 1})
        m_prime_hw = await read_poly(dut, 4)

        # FO transform (Fujisaki-Okamoto): re-encrypt and check
        m_bytes = byte_encode(1, m_prime_hw)
        K_prime, r_prime = g_hash(m_bytes + h)
        K_bar = j_hash(z + c)
        c_prime = k_pke_encrypt(ek, m_bytes, r_prime)

        if c == c_prime:
            K_result = K_prime
        else:
            K_result = K_bar

        # Compare
        if K_result == expected_k:
            dut._log.info(f"  PASS tcId={tc_id} (c match: {c == c_prime})")
        else:
            total_errors += 1
            dut._log.error(f"  FAIL tcId={tc_id}: K mismatch (c match: {c == c_prime})")
            dut._log.error(f"    got:      {K_result.hex()}")
            dut._log.error(f"    expected: {expected_k.hex()}")

    assert total_errors == 0, f"ACVP Decaps: {total_errors}/{len(vectors)} vectors failed"
    dut._log.info(f"PASS: All {len(vectors)} ACVP decapsulation vectors match hardware")
