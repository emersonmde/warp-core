"""ACVP compliance test for encaps_top.

Iterates through all ML-KEM-768 encapsulation ACVP vectors. Per vector:
  1. Python: Parse ek -> t_hat[3] + rho. expand_a(rho) -> A_hat.
     h=H(ek). (K,r)=G(m||h). PRF(r, 0..6) -> 7x128 CBD bytes.
     mu = Decompress(1, ByteDecode(1, m)).
  2. Hardware: Load A_hat(0-8), t_hat(9-11), mu(12). Start encaps.
     Feed 896 CBD bytes.
  3. Readback: Compressed u from slots 16-18 (D=10), compressed v
     from slot 19 (D=4).
  4. Python encode: c = ByteEncode(10,u[0..2]) || ByteEncode(4,v).
  5. Compare c and K against ACVP expected.
"""

import json
import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import KYBER_Q, KYBER_N, decompress_q
from kyber_acvp import (
    K, ETA1, ETA2, DU, DV,
    byte_encode, byte_decode, g_hash, h_hash, prf, expand_a,
)

CLK_PERIOD_NS = 10
PARAM_SET = "ML-KEM-768"
VECTORS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'ref', 'acvp_vectors')


def load_vectors():
    """Load cached ACVP encapsulation vectors."""
    prompt_path = os.path.join(VECTORS_DIR, "encapdecap_prompt.json")
    results_path = os.path.join(VECTORS_DIR, "encapdecap_results.json")

    assert os.path.exists(prompt_path), \
        f"ACVP vectors not cached. Run: python ref/test_acvp_oracle.py"

    with open(prompt_path) as f:
        prompt = json.load(f)
    with open(results_path) as f:
        results = json.load(f)

    # Find ML-KEM-768 encapsulation test groups
    prompt_groups = {g["tgId"]: g for g in prompt["testGroups"]
                     if g.get("parameterSet") == PARAM_SET
                     and g.get("function") == "encapsulation"}
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
                "ek": bytes.fromhex(tc["ek"]),
                "m": bytes.fromhex(tc["m"]),
                "expected_c": bytes.fromhex(exp["c"]),
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
    dut.encaps_start.value = 0
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


async def run_encaps(dut, all_cbd_bytes, timeout=200000):
    cocotb.start_soon(feed_cbd_bytes_background(dut, all_cbd_bytes, dut._log))
    dut.encaps_start.value = 1
    await RisingEdge(dut.clk)
    dut.encaps_start.value = 0

    for cycle in range(timeout):
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        if dut.encaps_done.value == 1:
            return cycle + 1
    raise TimeoutError(f"Encaps did not complete within {timeout} cycles")


@cocotb.test()
async def test_acvp_encaps_vectors(dut):
    """Run all ML-KEM-768 ACVP encapsulation vectors through hardware."""
    vectors = load_vectors()
    dut._log.info(f"Loaded {len(vectors)} ACVP encapsulation vectors")

    await init(dut)

    total_errors = 0

    for vec in vectors:
        tc_id = vec["tcId"]
        ek = vec["ek"]
        m = vec["m"]
        expected_c = vec["expected_c"]
        expected_k = vec["expected_k"]

        # Parse ek -> t_hat[3] + rho
        t_hat = []
        for i in range(K):
            t_hat.append(byte_decode(12, ek[384 * i : 384 * (i + 1)]))
        rho = ek[384 * K:]

        # Expand A_hat
        A_hat = expand_a(rho)

        # Derive encryption randomness
        h = h_hash(ek)
        K_val, r = g_hash(m + h)

        # Generate CBD bytes: PRF(r, 0..6) for y[0..2], e1[0..2], e2
        all_cbd_bytes = []
        for nonce in range(2 * K):
            all_cbd_bytes.extend(list(prf(ETA1, r, nonce)))
        all_cbd_bytes.extend(list(prf(ETA2, r, 2 * K)))

        # Decompress message: mu = Decompress(1, ByteDecode(1, m))
        mu_coeffs = byte_decode(1, m)
        mu = [decompress_q(c, 1) for c in mu_coeffs]

        # Load A_hat into slots 0-8 (A_hat[j][i] -> slot j*3+i for encaps)
        for j in range(K):
            for i in range(K):
                await write_poly(dut, j * 3 + i, A_hat[j][i])

        # Load t_hat[0..2] into slots 9-11
        for i in range(K):
            await write_poly(dut, 9 + i, t_hat[i])

        # Load mu into slot 12
        await write_poly(dut, 12, mu)

        # Run encaps
        cycles = await run_encaps(dut, all_cbd_bytes)
        dut._log.info(f"  tcId={tc_id}: encaps completed in {cycles} cycles")

        # Read back compressed u from slots 16-18 (D=10)
        u_compressed = []
        for i in range(K):
            u_compressed.append(await read_poly(dut, 16 + i))

        # Read back compressed v from slot 19 (D=4)
        v_compressed = await read_poly(dut, 19)

        # Encode ciphertext: c = ByteEncode(10, u[0..2]) || ByteEncode(4, v)
        c = b''
        for i in range(K):
            c += byte_encode(DU, u_compressed[i])
        c += byte_encode(DV, v_compressed)

        # Compare ciphertext and shared secret
        c_ok = (c == expected_c)
        k_ok = (K_val == expected_k)

        if c_ok and k_ok:
            dut._log.info(f"  PASS tcId={tc_id}")
        else:
            total_errors += 1
            if not c_ok:
                dut._log.error(f"  FAIL tcId={tc_id}: ciphertext mismatch")
                for i in range(min(len(c), len(expected_c))):
                    if c[i] != expected_c[i]:
                        dut._log.error(f"    first diff at byte {i}: got 0x{c[i]:02x}, expected 0x{expected_c[i]:02x}")
                        break
            if not k_ok:
                dut._log.error(f"  FAIL tcId={tc_id}: shared secret K mismatch")

    assert total_errors == 0, f"ACVP Encaps: {total_errors}/{len(vectors)} vectors failed"
    dut._log.info(f"PASS: All {len(vectors)} ACVP encapsulation vectors match hardware")
