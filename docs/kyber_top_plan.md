# Plan: Completing Kyber-768 — cbd_sampler through kyber_top

## Context

Milestones 1–4 are complete: 14 modules, 50 tests, all passing. The accelerator has modular arithmetic, NTT/INTT engine, polynomial basemul, and compress/decompress. What remains is tying it all together into a working Kyber-768 (ML-KEM-768, FIPS 203) accelerator capable of KeyGen, Encaps, and Decaps.

### Architectural Decisions (from discussion)

1. **Keccak**: Internal bus interface (`keccak_if` port group at kyber_top boundary, absorb/squeeze semantics). No Keccak core built — external provider connects. Designed so a co-located core can be added later without FSM changes.
2. **Kyber-768 only**: k=3, du=10, dv=4, eta1=eta2=2. Hardcoded at the orchestration level. Polynomial-level modules stay parameterized.
3. **Encode/Decode**: Software boundary. Host handles byte packing. Accelerator operates at 12-bit coefficient level.
4. **Host I/O**: Coefficient-level. Host loads/reads polynomial RAMs via `host_slot[4:0]`/`host_addr[7:0]`/`host_din[11:0]`/`host_dout[11:0]`. Same proven pattern as existing cocotb testbenches.
5. **Host provides hash results**: H(pk), G(m||h) → K, r_seed are computed by host software and loaded via registers. The `keccak_if` is used for SHAKE256 squeezing (CBD noise sampling only). This avoids ByteEncode serialization in hardware. Full hash integration deferred to when a Keccak core is co-located.
6. **A_hat expansion**: Host precomputes A_hat[3×3] from seed ρ in software, loads 9 polynomials. SampleNTT (rejection sampling) stays in software.
7. **No modifications to existing verified modules**: kyber_top wraps them with copy-in/copy-out patterns.

---

## Module Summary

### New Modules

| Module | Type | Description |
|--------|------|-------------|
| `poly_add` | Sequential FSM | Reads two RAM port groups, writes mod_add result to a third. 258 cycles. |
| `poly_sub` | Sequential FSM | Same as poly_add but with mod_sub. 258 cycles. |
| `cbd_sampler` | Sequential FSM | Consumes 128 random bytes from keccak squeeze, produces 256 CBD_η=2 coefficients. ~130 cycles. |
| `kyber_top` | Sequential FSM | Master orchestrator: 20-slot RAM bank, port mux, keccak_if, micro-op sequencer for KeyGen/Encaps/Decaps. |

### Existing Modules (used as-is, no modifications)

| Module | How kyber_top uses it |
|--------|----------------------|
| `ntt_engine` | Copy-in 256 coefficients → run NTT/INTT → copy-out 256 coefficients |
| `poly_basemul` | Copy-in A (256) + B (256) → run basemul → copy-out A (256, result in-place) |
| `compress` | Instantiated for D=1,4,10. Fed combinationally during compress micro-op loops. |
| `decompress` | Instantiated for D=1,4,10. Fed combinationally during decompress micro-op loops. |
| `mod_add` | Instantiated inside `poly_add`. |
| `mod_sub` | Instantiated inside `poly_sub`. |

---

## Module Designs

### poly_add / poly_sub

External-port modules (no internal RAM). kyber_top connects them to RAM bank slots via port mux.

```
module poly_add (
    clk, rst_n,
    start, done, busy,
    // Source A read port (connected to RAM bank slot by kyber_top)
    src_a_addr[7:0], src_a_dout[11:0],
    // Source B read port
    src_b_addr[7:0], src_b_dout[11:0],
    // Destination write port
    dst_we, dst_addr[7:0], dst_din[11:0]
);
```

FSM: READ (present addr i, read both sources) → WRITE (write mod_add result at addr i, present addr i+1) → loop 256 → DONE.
Two cycles per coefficient × 256 + 1 setup + 1 done = **258 cycles** (not 513 — pipelining the read/write on alternate cycles).

poly_sub is identical but uses mod_sub.

### cbd_sampler

CBD with η=2. Each coefficient needs 4 random bits. Each byte produces 2 coefficients.

```
module cbd_sampler (
    clk, rst_n,
    start, done, busy,
    // Keccak squeeze byte input
    byte_req,                // request next byte
    byte_valid,              // byte available
    byte_data[7:0],          // the byte
    // Destination write port (to RAM bank)
    dst_we, dst_addr[7:0], dst_din[11:0]
);
```

CBD_η=2 computation (combinational):
- For nibble `[b3 b2 b1 b0]`: `coeff = (b0+b1) - (b2+b3)`
- Result in [-2, 2]. If negative, add q to get [0, q-1].
- Small case/LUT: `{0→0, 1→1, 2→2, -1→3328, -2→3327}`

FSM: REQUEST_BYTE → wait byte_valid → WRITE_LO (low nibble → addr 2i) → WRITE_HI (high nibble → addr 2i+1) → loop 128 bytes → DONE. ~**130 cycles** (excluding keccak latency, dominated by byte_valid wait).

### keccak_if (port group, not a module)

Defined at kyber_top boundary. Supports absorb (kyber_top → keccak) and squeeze (keccak → kyber_top):

```
// Absorb
keccak_absorb_valid, keccak_absorb_data[7:0], keccak_absorb_ready,
keccak_absorb_last,
// Squeeze
keccak_squeeze_ready, keccak_squeeze_valid, keccak_squeeze_data[7:0],
// Control
keccak_mode[1:0],     // 0=SHA3-256, 1=SHA3-512, 2=SHAKE128, 3=SHAKE256
keccak_start,         // begin new hash/XOF
keccak_ready          // keccak core idle
```

For initial implementation, only SHAKE256 mode is used (CBD sampling): absorb r_seed (32 bytes) + nonce (1 byte), then squeeze 128 bytes. The testbench mocks the keccak responses.

### kyber_top

```
module kyber_top (
    clk, rst_n,

    // Host command interface
    host_start, host_op[1:0], host_done, host_busy,
    //   op: 0=KeyGen, 1=Encaps, 2=Decaps

    // Host polynomial I/O (coefficient-level)
    host_we, host_slot[4:0], host_addr[7:0],
    host_din[11:0], host_dout[11:0],

    // Host register interface (for seeds, keys, nonces)
    reg_we, reg_addr[3:0], reg_wdata[31:0], reg_rdata[31:0],

    // Keccak interface (port group above)
    keccak_*,

    // Status
    decaps_match       // 1 if Decaps ct comparison passed
);
```

**Register map** (host writes via reg_we/reg_addr/reg_wdata):
| Addr | Name | Description |
|------|------|-------------|
| 0–7 | SEED[0..7] | r_seed for CBD noise sampling (32 bytes) |
| 8 | H_PK[0] | h = H(pk), first 4 bytes (Decaps only) |
| ... | ... | (8 registers for 32-byte h) |

Shared key K is read back via reg_rdata (host provides K from G hash for Encaps, reads K' result for Decaps).

---

## RAM Bank Architecture

**20 poly_ram instances**, each 256×12 dual-port. Addressed by `host_slot[4:0]` (0–19).

BRAM budget: 20 (bank) + 1 (ntt_engine) + 2 (poly_basemul) = **23 out of 50** on Artix-7 XC7A35T.

### Slot Allocation — Encaps

| Slots | Contents | Loaded by |
|-------|----------|-----------|
| 0–8 | A_hat[j×3+i] (public matrix, NTT domain) | Host |
| 9–11 | t_hat[0..2] (public key, NTT domain) | Host |
| 12 | m (message polynomial) | Host |
| 13–15 | r_hat[0..2] (randomness, NTT'd in-place) | CBD + NTT |
| 16–18 | e1[0..2] (noise) → then u[0..2] (result) | CBD → overwritten |
| 19 | e2 (noise) → then v (result) | CBD → overwritten |

Note: A_hat slots 0–8 are consumed during matrix-vector multiply (basemul overwrites RAM A in-place). After computing u[i], the corresponding A_hat column is no longer needed.

### Slot Allocation — Decaps

| Slots | Contents | Loaded by |
|-------|----------|-----------|
| 0–8 | A_hat[3×3] | Host |
| 9–11 | t_hat[0..2] | Host |
| 12–14 | s_hat[0..2] (secret key) | Host |
| 15–17 | ct_u[0..2] (compressed ciphertext u, d=10) | Host |
| 18 | ct_v (compressed ciphertext v, d=4) | Host |
| 19 | temp/accumulator | FSM |

Slots 15–18 are decompressed in-place, then reused for intermediate results during re-encryption.

### Port Mux

A `case` on the current micro-op state routes RAM bank port signals to the active sub-module. Only 2–3 slots are active simultaneously. Each slot's port A is multiplexed between:

- Host interface (during IDLE)
- Copy source/dest (during COPY_TO/FROM_NTT/BASEMUL)
- poly_add/poly_sub ports (during ADD/SUB micro-ops)
- Compress/decompress loop (during COMPRESS/DECOMPRESS micro-ops)
- cbd_sampler output (during CBD_SAMPLE)

---

## Micro-Operation Architecture

The master FSM uses a **step counter** (`step_reg`) indexed by `{operation, step}`. Each step executes one micro-op, waits for its done signal, then advances.

| Micro-Op | Description | Cycles |
|----------|-------------|--------|
| COPY_TO_NTT(src) | bank[src] → ntt_engine ext port | 257 |
| COPY_FROM_NTT(dst) | ntt_engine ext port → bank[dst] | 257 |
| RUN_NTT(mode) | Start ntt_engine, wait done | 1800 (FWD) / 2313 (INV) |
| COPY_TO_BM_A(src) | bank[src] → poly_basemul RAM A | 257 |
| COPY_TO_BM_B(src) | bank[src] → poly_basemul RAM B | 257 |
| COPY_FROM_BM_A(dst) | poly_basemul RAM A → bank[dst] | 257 |
| RUN_BASEMUL | Start poly_basemul, wait done | 257 |
| POLY_ADD(a, b, dst) | bank[dst][i] = mod_add(bank[a][i], bank[b][i]) | 258 |
| POLY_SUB(a, b, dst) | bank[dst][i] = mod_sub(bank[a][i], bank[b][i]) | 258 |
| COMPRESS(src, dst, D) | bank[dst][i] = compress_D(bank[src][i]) | 258 |
| DECOMPRESS(src, dst, D) | bank[dst][i] = decompress_D(bank[src][i]) | 258 |
| CBD_SAMPLE(dst, nonce) | SHAKE256(seed‖nonce) → cbd_sampler → bank[dst] | ~170 + keccak |
| COMPARE(a, b) | Constant-time OR of all mismatches over 256 coefficients | 258 |

### NTT Composite (convenience): 257 + 1800 + 257 = **2314 cycles** per forward NTT
### Basemul Composite: 257 + 257 + 257 + 257 = **1028 cycles** per basemul (copy A + copy B + run + copy result)

---

## Algorithm Sequences

### Encaps (~49 steps, ~44k cycles + keccak latency)

```
Phase 1: CBD noise sampling — 7 polynomials
  CBD_SAMPLE(s13, nonce=0)  →  r[0]
  CBD_SAMPLE(s14, nonce=1)  →  r[1]
  CBD_SAMPLE(s15, nonce=2)  →  r[2]
  CBD_SAMPLE(s16, nonce=3)  →  e1[0]
  CBD_SAMPLE(s17, nonce=4)  →  e1[1]
  CBD_SAMPLE(s18, nonce=5)  →  e1[2]
  CBD_SAMPLE(s19, nonce=6)  →  e2

Phase 2: NTT(r) — 3 forward NTTs
  COPY_TO_NTT(s13), RUN_NTT(FWD), COPY_FROM_NTT(s13)  →  r_hat[0]
  COPY_TO_NTT(s14), RUN_NTT(FWD), COPY_FROM_NTT(s14)  →  r_hat[1]
  COPY_TO_NTT(s15), RUN_NTT(FWD), COPY_FROM_NTT(s15)  →  r_hat[2]

Phase 3: u[i] = INTT(A^T · r_hat) + e1[i] — per i=0,1,2
  // u[0]:
  COPY_TO_BM_A(s0), COPY_TO_BM_B(s13), RUN_BASEMUL, COPY_FROM_BM_A(s0)
  COPY_TO_BM_A(s3), COPY_TO_BM_B(s14), RUN_BASEMUL, COPY_FROM_BM_A(s3)
  POLY_ADD(s0, s3, s0)                                     // accumulate
  COPY_TO_BM_A(s6), COPY_TO_BM_B(s15), RUN_BASEMUL, COPY_FROM_BM_A(s6)
  POLY_ADD(s0, s6, s0)                                     // accumulate
  COPY_TO_NTT(s0), RUN_NTT(INV), COPY_FROM_NTT(s0)       // INTT
  POLY_ADD(s0, s16, s0)                                     // + e1[0]
  // (repeat for u[1] with A[*,1] and e1[1], u[2] with A[*,2] and e1[2])

Phase 4: v = INTT(t_hat^T · r_hat) + e2 + Decompress(m, 1)
  COPY_TO_BM_A(s9),  COPY_TO_BM_B(s13), RUN_BASEMUL, COPY_FROM_BM_A(s9)
  COPY_TO_BM_A(s10), COPY_TO_BM_B(s14), RUN_BASEMUL, COPY_FROM_BM_A(s10)
  POLY_ADD(s9, s10, s9)
  COPY_TO_BM_A(s11), COPY_TO_BM_B(s15), RUN_BASEMUL, COPY_FROM_BM_A(s11)
  POLY_ADD(s9, s11, s9)
  COPY_TO_NTT(s9), RUN_NTT(INV), COPY_FROM_NTT(s9)
  POLY_ADD(s9, s19, s9)                                     // + e2
  DECOMPRESS(s12, s12, D=1)                                 // Decompress msg
  POLY_ADD(s9, s12, s9)                                     // + msg

Phase 5: Compress outputs
  COMPRESS(s0, s0, D=10)    // u[0]
  COMPRESS(s1, s1, D=10)    // u[1]
  COMPRESS(s2, s2, D=10)    // u[2]
  COMPRESS(s9, s9, D=4)     // v
  DONE — host reads slots 0-2 (compressed u), slot 9 (compressed v)
```

### Decaps (~80 steps, ~90k cycles + keccak latency)

```
Phase 1: Decompress ciphertext
  DECOMPRESS(s15, s15, D=10)  →  u[0]
  DECOMPRESS(s16, s16, D=10)  →  u[1]
  DECOMPRESS(s17, s17, D=10)  →  u[2]
  DECOMPRESS(s18, s18, D=4)   →  v

Phase 2: Decrypt — w = INTT(s_hat^T · NTT(u))
  NTT(s15), NTT(s16), NTT(s17)                             // u_hat[0..2]
  basemul(s12, s15) + basemul(s13, s16) + basemul(s14, s17) // s^T · u_hat
  accumulate → s12
  INTT(s12) → w

Phase 3: Recover message — m' = Compress(v - w, 1)
  POLY_SUB(s18, s12, s19)
  COMPRESS(s19, s19, D=1)                                    // m'

Phase 4: Re-encrypt (same as Encaps using m' at s19)
  (Same sequence as Encaps phases 1–5, but reading m from s19)
  Results: u' in slots that previously held A_hat columns, v' in reused slot

Phase 5: Constant-time compare
  COMPARE recomputed compressed u'/v' against original ct slots
  Accumulate mismatch flag (OR all differences, no early exit)
  Set decaps_match output

Phase 6: Select key
  decaps_match=1 → K = K' (host-provided)
  decaps_match=0 → implicit rejection (host uses z to derive reject key)
  DONE
```

### Cycle Budget Estimates

| Operation | Encaps | Decaps |
|-----------|--------|--------|
| CBD sampling (7 polys) | ~1,200 + keccak | ~1,200 + keccak (re-encrypt) |
| Forward NTT (3×) | 6,942 | 13,884 (3 for u + 3 for re-encrypt) |
| Inverse NTT (4×) | 11,480 | 22,960 |
| Basemul + copy (12×) | 12,336 | 24,672 |
| Poly add/sub (12×) | 3,096 | 6,192 |
| Compress/decompress (5×) | 1,290 | 2,580 |
| Misc (compare, setup) | — | ~500 |
| **Total (excl. keccak)** | **~36k** | **~72k** |
| **At 100 MHz** | **~360 µs** | **~720 µs** |

---

## Implementation Order

### Milestone 5a: poly_add + poly_sub

| Action | File |
|--------|------|
| Create | `rtl/poly_add.v` |
| Create | `rtl/poly_sub.v` |
| Create | `tb/poly_add/test_poly_add.py` + `Makefile` |
| Create | `tb/poly_sub/test_poly_sub.py` + `Makefile` |
| Modify | `ref/kyber_math.py` — add `poly_add()`, `poly_sub()` |
| Modify | `Makefile` — add targets |

Tests: Load two random polynomials into wrapper's RAMs, run add/sub, compare all 256 outputs against oracle. Boundary cases (0+0, max+max, etc.). ~5 tests each.

### Milestone 5b: cbd_sampler

| Action | File |
|--------|------|
| Create | `rtl/cbd_sampler.v` |
| Create | `tb/cbd_sampler/cbd_sampler_tb_wrapper.v` (optional, if keccak mock needs wrapping) |
| Create | `tb/cbd_sampler/test_cbd_sampler.py` + `Makefile` |
| Modify | `ref/kyber_math.py` — add `cbd_eta2(random_bytes)` |
| Modify | `Makefile` — add targets |

Tests: Drive known byte sequences via byte_req/byte_valid/byte_data, verify output coefficients match oracle. Verify distribution is centered binomial. Verify output range [0, q-1].

### Milestone 5c: kyber_top RAM bank + host I/O skeleton

| Action | File |
|--------|------|
| Create | `rtl/kyber_top.v` with 20 poly_rams, host port mux, IDLE state only |
| Create | `tb/kyber_top/test_kyber_top.py` — write/read all 20 slots, verify isolation |

### Milestone 5d: Micro-op infrastructure

- Add COPY_TO/FROM_NTT, RUN_NTT micro-ops. Test: load poly, NTT, verify.
- Add COPY_TO/FROM_BASEMUL, RUN_BASEMUL. Test: load two polys, basemul, verify.
- Add POLY_ADD/POLY_SUB micro-ops. Test: load, add, verify.
- Add COMPRESS/DECOMPRESS micro-ops. Test: load, compress, verify.
- Add CBD_SAMPLE + keccak_if. Test: mock keccak, sample poly, verify.

### Milestone 5e: Encaps FSM

- Implement full Encaps step sequence
- Test against known test vectors (deterministic with fixed seeds)
- Cross-check compressed output against Python oracle doing the same operations

### Milestone 5f: Decaps, KeyGen, and Integration

- Implement full Decaps including re-encryption and constant-time compare
- Test: Decaps of valid ciphertext → correct match
- Test: Decaps of modified ciphertext → mismatch (implicit rejection)
- Test: Verify constant cycle count (same for match and mismatch)
- Implement KeyGen FSM (simpler than Encaps: CBD sample s/e, NTT(s), A·s_hat + NTT(e) → t_hat)
- Full KeyGen → Encaps → Decaps round-trip test
- Multiple random test vectors with deterministic seeds

---

## Verification Strategy

Each milestone tests in isolation before integration:

- **poly_add/sub**: Exhaustive slices + 100k random pairs (same as mod_add/mod_sub but at polynomial level). Testbench instantiates a wrapper with two poly_rams for input and one for output.
- **cbd_sampler**: Known byte sequences → verify against `cbd_eta2()` oracle. Exhaustive over all 256 possible byte values (each byte → 2 coefficients). Distribution check.
- **kyber_top micro-ops**: Each micro-op tested individually by loading test data, running the micro-op, reading results. Compared against Python oracle performing the same operation.
- **kyber_top Encaps/Decaps**: Deterministic test vectors with fixed random seeds. The testbench mocks keccak_if by providing pre-computed SHAKE256 output. Python oracle runs the same algorithm with the same seeds and compares all intermediate and final results.
- **Round-trip**: KeyGen → Encaps → Decaps produces matching shared secret.

`make test` runs all tests including new ones. Target: **~65 tests across 18 modules**.

---

## Files Summary

| Action | File |
|--------|------|
| Create | `rtl/poly_add.v`, `rtl/poly_sub.v` |
| Create | `rtl/cbd_sampler.v` |
| Create | `rtl/kyber_top.v` |
| Create | `tb/poly_add/*`, `tb/poly_sub/*` |
| Create | `tb/cbd_sampler/*` |
| Create | `tb/kyber_top/*` |
| Modify | `rtl/kyber_pkg.vh` — Kyber-768 parameters |
| Modify | `ref/kyber_math.py` — poly_add/sub, cbd, keygen/encaps/decaps oracles |
| Modify | `Makefile` — new test/waves targets |
| Modify | `docs/architecture.md` — kyber_top architecture section |
