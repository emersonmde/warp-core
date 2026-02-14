"""ACVP compliance test for keygen_top.

Iterates through all ML-KEM-768 keyGen ACVP vectors. Per vector:
  1. Python: G(d||0x03) -> (rho, sigma). expand_a(rho) -> A_hat.
     PRF(sigma, 0..5) -> 6x128 CBD bytes.
  2. Hardware: Load A_hat into slots 0-8. Start keygen. Feed 768 CBD bytes.
  3. Readback: t_hat from slots 0,3,6. s_hat from slots 9-11.
  4. Python encode: ek = ByteEncode(12,t_hat) || rho.
     dk = ByteEncode(12,s_hat) || ek || H(ek) || z.
  5. Compare ek, dk against ACVP expected.
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
    K, ETA1,
    byte_encode, g_hash, h_hash, prf, expand_a,
)

CLK_PERIOD_NS = 10
PARAM_SET = "ML-KEM-768"
VECTORS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'ref', 'acvp_vectors')


def load_vectors():
    """Load cached ACVP keyGen vectors (must run test_acvp_oracle.py first)."""
    prompt_path = os.path.join(VECTORS_DIR, "keygen_prompt.json")
    results_path = os.path.join(VECTORS_DIR, "keygen_results.json")

    assert os.path.exists(prompt_path), \
        f"ACVP vectors not cached. Run: python ref/test_acvp_oracle.py"

    with open(prompt_path) as f:
        prompt = json.load(f)
    with open(results_path) as f:
        results = json.load(f)

    # Find ML-KEM-768 test groups
    prompt_groups = {g["tgId"]: g for g in prompt["testGroups"]
                     if g.get("parameterSet") == PARAM_SET}
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
                "d": bytes.fromhex(tc["d"]),
                "z": bytes.fromhex(tc["z"]),
                "expected_ek": bytes.fromhex(exp["ek"]),
                "expected_dk": bytes.fromhex(exp["dk"]),
            })
    return vectors


async def init(dut):
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


async def feed_cbd_bytes_background(dut, all_cbd_bytes, log):
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


async def run_keygen(dut, all_cbd_bytes, timeout=200000):
    cocotb.start_soon(feed_cbd_bytes_background(dut, all_cbd_bytes, dut._log))
    dut.keygen_start.value = 1
    await RisingEdge(dut.clk)
    dut.keygen_start.value = 0

    for cycle in range(timeout):
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        if dut.keygen_done.value == 1:
            return cycle + 1
    raise TimeoutError(f"KeyGen did not complete within {timeout} cycles")


@cocotb.test()
async def test_acvp_keygen_vectors(dut):
    """Run all ML-KEM-768 ACVP keyGen vectors through hardware."""
    vectors = load_vectors()
    dut._log.info(f"Loaded {len(vectors)} ACVP keyGen vectors")

    await init(dut)

    total_errors = 0

    for vec in vectors:
        tc_id = vec["tcId"]
        d = vec["d"]
        z = vec["z"]
        expected_ek = vec["expected_ek"]
        expected_dk = vec["expected_dk"]

        # Derive inputs from seed d
        rho, sigma = g_hash(d + bytes([K]))
        A_hat = expand_a(rho)

        # Generate CBD bytes: PRF(sigma, 0..5) for s[0..2], e[0..2]
        all_cbd_bytes = []
        for nonce in range(2 * K):
            all_cbd_bytes.extend(list(prf(ETA1, sigma, nonce)))

        # Load A_hat into slots (row-major: slot i*3+j)
        for i in range(K):
            for j in range(K):
                await write_poly(dut, i * 3 + j, A_hat[i][j])

        # Run keygen
        cycles = await run_keygen(dut, all_cbd_bytes)
        dut._log.info(f"  tcId={tc_id}: keygen completed in {cycles} cycles")

        # Read back t_hat from slots 0, 3, 6
        t_hat_hw = []
        for idx, slot in enumerate([0, 3, 6]):
            t_hat_hw.append(await read_poly(dut, slot))

        # Read back s_hat from slots 9-11
        s_hat_hw = []
        for i in range(K):
            s_hat_hw.append(await read_poly(dut, 9 + i))

        # Encode ek = ByteEncode(12, t_hat[0..2]) || rho
        ek = b''
        for i in range(K):
            ek += byte_encode(12, t_hat_hw[i])
        ek += rho

        # Encode dk = ByteEncode(12, s_hat[0..2]) || ek || H(ek) || z
        dk_pke = b''
        for i in range(K):
            dk_pke += byte_encode(12, s_hat_hw[i])
        dk = dk_pke + ek + h_hash(ek) + z

        # Compare
        if ek != expected_ek:
            dut._log.error(f"  FAIL tcId={tc_id}: ek mismatch")
            for i in range(min(len(ek), len(expected_ek))):
                if ek[i] != expected_ek[i]:
                    dut._log.error(f"    first diff at byte {i}: got 0x{ek[i]:02x}, expected 0x{expected_ek[i]:02x}")
                    break
            total_errors += 1
        elif dk != expected_dk:
            dut._log.error(f"  FAIL tcId={tc_id}: dk mismatch")
            total_errors += 1
        else:
            dut._log.info(f"  PASS tcId={tc_id}")

    assert total_errors == 0, f"ACVP KeyGen: {total_errors}/{len(vectors)} vectors failed"
    dut._log.info(f"PASS: All {len(vectors)} ACVP keyGen vectors match hardware")
