# Design Decisions

Technical design document for Warp Core, a CRYSTALS-Kyber (ML-KEM / FIPS 203) hardware accelerator targeting Xilinx Artix-7 FPGAs. Each section covers what the decision is, what the alternative was, why this approach wins, and what the trade-off is.

## 1. Barrett Reduction: V=20158 (Floor, Not Ceiling)

The pq-crystals C reference implementation uses `V = ceil(2^26 / 3329) = 20159` with signed 16-bit arithmetic. The ceiling value can produce a quotient estimate `t` that overshoots `floor(a/q)` by 1, yielding a negative remainder — which is fine in C where signed comparison handles it.

Hardware uses `V = floor(2^26 / 3329) = 20158`. With the floor value, `t` never exceeds `floor(a/q)`, so the remainder `r = a - t*q` is always non-negative. This eliminates signed comparison and sign-extension logic from the datapath entirely. The trade-off is that `r` can land in `[0, 2q-1]` instead of `[-(q-1), q-1]`, requiring a conditional subtraction (`cond_sub_q`) as the final step — but that subtraction is a single 13-bit adder with mux, trivial in hardware.

The floor constant is exhaustively verified correct for all 65,536 possible 16-bit inputs and sampled up to 27 bits (safe for inputs up to 77,517,490). This covers every use site in the design: 24-bit NTT butterfly products, 25-bit basemul accumulator sums, and 24-bit INTT scaling products.

**Key files:** `rtl/barrett_reduce.v`, `rtl/kyber_pkg.vh`

## 2. Subtract-and-Select in cond_sub_q

Conditional subtraction reduces a value in `[0, 2q-1]` to `[0, q-1]`. The naive approach uses a comparator (`a >= Q`) to decide whether to subtract, then a separate subtractor for the actual reduction. This requires two 13-bit operations.

The subtract-and-select pattern computes `diff = a - Q` as a single 13-bit unsigned subtraction. If `a < Q`, the subtraction underflows and the borrow bit `diff[12]` is set to 1. This borrow bit directly drives the output mux: `result = diff[12] ? a : diff`. One subtractor does both the comparison and the reduction — the borrow is the comparator, and `diff[11:0]` is the reduced value.

This is the same pattern used in many hardware modular arithmetic implementations (e.g., Montgomery reduction finalization). The dual use of the subtractor borrow as a mux select is a standard FPGA optimization that saves one level of logic.

**Key file:** `rtl/cond_sub_q.v`

## 3. Basemul: 3 Barrett Reductions Instead of 5

A single 2x2 basemul computes `(a0 + a1*X)(b0 + b1*X) mod (X^2 - zeta)`, producing:
- `c0 = a0*b0 + a1*b1*zeta mod q`
- `c1 = a0*b1 + a1*b0 mod q`

The naive implementation reduces each of the four 12x12 multiplications (24-bit products) individually, then reduces after each addition — 5 Barrett reductions total. Each Barrett reduction instantiates a multiplier (`a * V`) and a multiply-subtract (`t * Q`), so this is expensive in DSP and LUT resources.

The optimized approach uses 3 reductions:
1. Reduce `a1*b1` (24-bit) to get `t1` (12-bit).
2. Accumulate `a0*b0 + t1*zeta` (25-bit, max value `3328^2 + 3328^2 = 22,151,168`) and reduce once.
3. Accumulate `a0*b1 + a1*b0` (25-bit, same max) and reduce once.

The key insight is that 22,151,168 is well within Barrett's safe input range of 77,517,490, so the accumulated sum can be reduced directly without intermediate reduction. Step 1 must happen first (to keep `t1*zeta` within 24 bits), but the c0 and c1 paths are independent and can execute in parallel.

The trade-off is that Barrett is parameterized to 25-bit input width for the accumulator reductions (slightly wider multiplier), but this saves 2 full Barrett instances — a net win in both area and critical path depth.

**Key file:** `rtl/basemul_unit.v`

## 4. Compress via Barrett Quotient Extraction

FIPS 203 compression computes `Compress_q(x, d) = round(2^d * x / q) mod 2^d`. This requires division by `q = 3329`, which would normally need a hardware divider — expensive in both area and latency on FPGAs.

The compress module reuses the same Barrett constant `V = 20158` to extract the *quotient* rather than the remainder:
1. Compute `numerator = (x << d) + 1664` (where 1664 = `(q-1)/2` is the rounding bias).
2. Estimate the quotient: `t = (numerator * V) >> 26`.
3. Compute the remainder: `r = numerator - t * q` (13-bit low-bits trick, same as `barrett_reduce`).
4. Correction: if `r >= q`, then `t` undercounted by 1, so add 1.
5. Result: `(t + correction) mod 2^d`.

The correction check reuses the subtract-and-select pattern from `cond_sub_q` — compute `r - Q` and check the borrow bit. The entire compress operation is purely combinational with no DSP divider. The Barrett multiplier (`numerator * V`) maps to a single DSP48E1 slice on Artix-7.

This works for all five D values used in Kyber (1, 4, 5, 10, 11) and is exhaustively verified for every `x` in `[0, 3328]` at each D value, plus round-trip error bounds checked against the FIPS 203 specification.

**Key file:** `rtl/compress.v`

## 5. NTT Engine: Shift-Based Address Generation

The NTT operates on 256 coefficients across 7 layers. Each layer has a different number of groups and butterflies per group, parameterized by the layer index. The start address for each group's butterfly span is:
- Forward: `start = group * (256 >> layer)`, equivalent to `group << (8 - layer)`
- Inverse: `start = group * (4 << layer)`, equivalent to `group << (layer + 2)`

Both forms reduce to a barrel shift of the group index by a function of the layer counter. In hardware, a barrel shift with a small shift amount (3-bit layer index) is free wiring on an FPGA — it maps to a small mux tree with no DSP or arithmetic logic. The same applies to `length` (`128 >> layer` or `2 << layer`) and `groups` (`1 << layer` or `64 >> layer`).

Twiddle factor ROM addressing is similarly computed via shifts:
- Forward: `zeta_idx = (1 << layer) + group`
- Inverse: `zeta_idx = (128 >> layer) - 1 - group`

The entire address generation unit uses zero DSP48E1 slices — all parameters are derived from combinational shift/add of the 3-bit layer counter and 7-bit group counter.

**Key file:** `rtl/ntt_engine.v`

## 6. 2-Cycle Read/Write Butterfly Pattern

The polynomial RAM (`poly_ram`) uses synchronous read-first mode: an address presented on cycle N produces valid data on cycle N+1 (after the clock edge). This is the standard BRAM behavior on Xilinx FPGAs and infers correctly to RAMB18E1 primitives.

Each butterfly therefore takes 2 cycles:
- **READ phase:** Present even and odd addresses to RAM ports A and B, and the zeta index to the twiddle ROM. No writes occur.
- **WRITE phase:** RAM outputs (`dout_a`, `dout_b`) and ROM output (`zeta`) are now valid from the previous cycle's addresses. The combinational butterfly computes on these registered outputs and writes results back to the same addresses.

This 2-cycle pattern guarantees no read-after-write hazard: the butterfly reads a coefficient pair, computes, and writes back before the next pair is read. The same pattern is reused in `poly_basemul` (READ_POS/WRITE_POS/READ_NEG/WRITE_NEG) and the INTT scaling pass (SCALE_READ/SCALE_WRITE).

The alternative — single-cycle butterfly with write-first RAM — would create timing issues: the written value would need to propagate through the RAM and butterfly combinational logic within one clock period, significantly limiting Fmax.

**Key file:** `rtl/ntt_engine.v`

## 7. Separate CT/GS Butterfly Instantiation

The NTT engine instantiates both a Cooley-Tukey (forward) butterfly and a Gentleman-Sande (inverse) butterfly as separate modules. An output mux selects which butterfly's results are written back to RAM, controlled by the latched `mode_reg` signal:

```verilog
wire [11:0] bf_even_out = mode_reg ? gs_even_out : ct_even_out;
wire [11:0] bf_odd_out  = mode_reg ? gs_odd_out  : ct_odd_out;
```

The alternative — a single configurable butterfly with internal muxing — would place the mode mux inside the critical path. The CT butterfly computes `t = zeta * odd` first, then adds/subtracts. The GS butterfly adds/subtracts first, then multiplies the difference by zeta. Muxing between multiply-first and add-first inside one module adds a mux delay before the Barrett reduction, which is already the longest combinational path.

With separate instantiation, the mux sits *after* both butterflies have completed, on the 12-bit results rather than on intermediate 24-bit products. The trade-off is approximately 1.5x the butterfly LUT count, but the critical path is cleaner and the design is simpler to reason about. On Artix-7, LUTs are relatively abundant compared to DSP and BRAM resources, so this trade-off is favorable.

**Key files:** `rtl/ntt_engine.v`, `rtl/ntt_butterfly.v`, `rtl/intt_butterfly.v`

## 8. Direct Operations on RAM Bank (No Extra RAMs)

The `kyber_top` module contains a 20-slot RAM bank where each slot is a 256x12 dual-port `poly_ram`. Polynomial add/sub, compress, and decompress are implemented as *direct operations* that read coefficients from bank slots via Port A and write results back via Port B, using combinational `mod_add`/`mod_sub`/`compress`/`decompress` units inline.

The alternative was to use the `poly_addsub` module, which has its own internal dual-port RAMs for input buffering. This would require copying polynomials into `poly_addsub`'s RAMs, running the operation, then copying results back — 3 copy phases plus the operation itself. Each `poly_addsub` instance consumes 2 BRAMs.

The direct approach uses the bank's existing dual-port structure: Port A reads both source slots by broadcasting the same address to all slots (and selecting the appropriate `dout`), while Port B writes the result to the target slot. This works for in-place operations (same source and target slot) because reads and writes use different ports. The 258-cycle coefficient loop (256 coefficients + 2 pipeline cycles) runs at the same speed as `poly_addsub` but saves 2-3 BRAMs.

The BRAM budget is tight on the target Artix-7 XC7A35T (50 RAMB18E1 available, 24 used by the current design). Every BRAM saved by direct operations is available for future features like Keccak state or DMA buffers.

**Key file:** `rtl/kyber_top.v`

## 9. CBD Dual-Port Write Trick

The Centered Binomial Distribution (CBD) sampler consumes 128 random bytes and produces 256 coefficients. Each byte yields two coefficients: bits `[3:0]` produce the even-indexed coefficient, and bits `[7:4]` produce the odd-indexed coefficient. The naive approach writes one coefficient per cycle (257 cycles for 256 writes + done).

The dual-port write trick exploits the fact that `poly_ram` has two independent write ports. On each accepted byte, the sampler writes both coefficients simultaneously:
- Port A writes `lo_coeff` at address `{byte_idx, 1'b0}` (even index)
- Port B writes `hi_coeff` at address `{byte_idx, 1'b1}` (odd index)

The two addresses always differ in the LSB, so there is no write-write conflict. This halves the sampling time to 129 cycles (128 bytes + 1 done cycle). At 100 MHz, that is 1.29 us per polynomial noise sample.

The CBD arithmetic itself uses the unsigned-only pattern: when `(b0+b1) - (b2+b3)` would be negative, it computes `Q - |difference|` instead, mapping the result to `[0, q-1]` without signed arithmetic.

**Key file:** `rtl/cbd_sampler.v`

## 10. Unsigned-Only Datapath Philosophy

The entire Warp Core design uses unsigned arithmetic exclusively. No module contains signed wire declarations, signed comparisons, or sign-extension logic. This is a deliberate choice that simplifies synthesis, timing analysis, and formal verification.

Key patterns that enable fully unsigned operation:

- **Barrett reduction:** Using `V = floor(2^26/q)` instead of `ceil` guarantees non-negative remainders (Section 1). The C reference's signed approach would require `$signed` wires and signed comparison in Verilog, complicating synthesis tool inference.

- **Modular subtraction:** `mod_sub` computes `a - b` as an unsigned subtraction. If the result underflows (borrow bit set), `cond_add_q` adds `q` back, mapping the result to `[0, q-1]`. This avoids two's complement representation of negative residues.

- **CBD sampling:** Negative CBD outputs `(b0+b1) - (b2+b3) < 0` are handled by computing `Q - |difference|` rather than using signed subtraction (Section 9).

- **NTT butterfly ordering:** The INTT (Gentleman-Sande) butterfly computes `diff = odd - even` (not `even - odd`), matching the pq-crystals C reference convention. The `mod_sub` + `cond_add_q` pattern handles the potential underflow.

The alternative — mixed signed/unsigned — is common in software-oriented RTL but creates subtle issues: Verilog's implicit sign extension rules can introduce bugs when mixing signed and unsigned operands, and synthesis tools may infer wider arithmetic than necessary. Unsigned-only makes bit widths explicit and predictable.

## 11. Flat Sequencer ROM (encaps_ctrl / keygen_ctrl / decaps_ctrl)

The three algorithm controllers (`encaps_ctrl`, `keygen_ctrl`, `decaps_ctrl`) are implemented as flat case statements over a step counter — 93, 69, and 32 entries respectively. Each step maps directly to one micro-op issued to `kyber_top`:

```
case (step)
    7'd0: begin cmd_op <= OP_CBD_SAMPLE; cmd_slot_a <= 5'd13; ... end
    7'd1: begin cmd_op <= OP_CBD_SAMPLE; cmd_slot_a <= 5'd14; ... end
    ...
    7'd92: begin cmd_op <= OP_COMPRESS; cmd_slot_a <= 5'd19; ... end
endcase
```

The alternative — nested loop counters `(phase, i, j, sub_step)` with combinational micro-op generation — would be more compact in source code but harder to verify. With nested loops, bugs in counter overflow, phase transitions, or loop bound calculations can silently produce wrong operation sequences. A flat ROM makes every micro-op visible and auditable in the source.

The trade-off is source code size: the `encaps_ctrl` case statement is ~300 lines vs. perhaps ~80 lines for a nested-loop implementation. But synthesis tools optimize case statements efficiently (the step counter indexes into LUT-based logic), and the flat structure makes it trivial to add, remove, or reorder operations. Verification is also simpler — the testbench can check each step's output against a Python-generated operation sequence.

This pattern is common in microcode engines: Intel's original 8086 used a similar flat microcode ROM rather than nested sequencing logic.

**Key files:** `rtl/encaps_ctrl.v`, `rtl/keygen_ctrl.v`, `rtl/decaps_ctrl.v`

## 12. ACVP Compliance Testing Strategy

NIST's Automated Cryptographic Validation Protocol (ACVP) provides official test vectors for ML-KEM-768. The testing strategy is oracle-first: validate the Python reference implementation against ACVP vectors before using it to drive hardware tests.

**Layer 1 — Python oracle validation:** `ref/kyber_acvp.py` implements the full FIPS 203 encoding layer (ByteEncode/ByteDecode, hash primitives G/H/J/PRF/XOF via SHA3/SHAKE, SampleNTT rejection sampling) on top of the arithmetic oracle in `ref/kyber_math.py`. The `ref/test_acvp_oracle.py` test suite downloads 60 ML-KEM-768 vectors from the NIST ACVP-Server repository and validates all of them: 25 keyGen, 25 encapsulation, 10 decapsulation (including implicit rejection cases).

**Layer 2 — Hardware vector tests:** FIPS 203 separates algorithms into deterministic inner functions that accept randomness as explicit input, enabling reproducible testing. The hardware testbenches exploit this by deriving exact per-vector inputs from ACVP seeds:
- **KeyGen:** Python expands `(d, z)` seeds via SHAKE/SHA3 to produce the NTT-domain matrix `A_hat` and CBD byte streams. These are loaded into `keygen_top`, which produces `t_hat` and `s_hat`. The testbench reads them back, ByteEncodes to `ek`/`dk`, and byte-compares against ACVP expected values.
- **Encaps:** Python parses the encapsulation key `ek`, expands `A_hat`, and derives CBD byte streams from the encryption randomness `r`. These are loaded into `encaps_top`, which produces compressed ciphertext. The testbench ByteEncodes the output and compares `c` and `K` against ACVP expected values.
- **Decaps:** Python parses the decapsulation key `dk` and ciphertext `c`, extracting `s_hat` and compressed coefficients. The testbench loads these into `decaps_top`, reads back the decrypted message `m'`, then performs the Fujisaki-Okamoto re-encryption check in Python (since the hardware performs K-PKE.Decrypt only). The derived key `K` is compared against ACVP expected values. This includes implicit rejection cases where re-encryption fails and the FO transform returns `J(z || c)` instead.

All 60 ACVP vectors pass at both the oracle and hardware levels, confirming bit-exact FIPS 203 compliance for ML-KEM-768.

**Key files:** `ref/kyber_acvp.py`, `ref/test_acvp_oracle.py`, `tb/acvp_keygen/`, `tb/acvp_encaps/`, `tb/acvp_decaps/`
