# Warp Core Architecture

## Module Hierarchy

Currently implemented modules (Milestones 1-4: modular arithmetic, NTT butterfly, NTT/INTT engine, basemul):

```mermaid
graph TD
    subgraph barrett_reduce
        A["a (INPUT_WIDTH bits)"] --> MUL1["a * V (quotient estimate)"]
        MUL1 --> SHIFT[">> 26"]
        SHIFT --> T["t (quotient)"]
        T --> MUL2["t * Q"]
        MUL2 --> SUB["a - t*Q"]
        SUB --> R["r (13 bits, range 0..2q-1)"]
        R --> CSQ

        subgraph cond_sub_q
            CSQ["r - Q"] --> MUX{{"borrow?"}}
            MUX -->|yes: r < Q| PASS["r"]
            MUX -->|no: r >= Q| REDUCED["r - Q"]
        end

        PASS --> RESULT["result (12 bits, 0..q-1)"]
        REDUCED --> RESULT
    end
```

## NTT Butterfly

The NTT butterfly is the core datapath of CRYSTALS-Kyber. Each butterfly performs:
- `t = zeta * odd_coeff mod q` (Barrett reduction)
- `even' = even + t mod q`
- `odd'  = even - t mod q`

Barrett reduction sits at the heart of each butterfly, reducing the product back to Z_q.

```mermaid
graph TD
    subgraph "ntt_butterfly"
        EVEN["even (12 bits)"] --> ADD
        ODD["odd (12 bits)"] --> ZMUL["zeta * odd (24 bits)"]
        ZMUL --> BARRETT["barrett_reduce #(.INPUT_WIDTH(24))"]
        BARRETT --> T["t (12 bits)"]

        T --> ADD

        subgraph "mod_add"
            ADD["+"] --> CSQ["cond_sub_q"]
        end

        T --> SUB2

        subgraph "mod_sub"
            EVEN --> SUB2["-"]
            SUB2 --> CAQ["cond_add_q"]
        end

        CSQ --> EVEN_OUT["even_out (12 bits)"]
        CAQ --> ODD_OUT["odd_out (12 bits)"]
    end
```

## NTT/INTT Engine

The NTT engine is a sequential FSM that performs 7 layers of 128 butterflies each.
It contains the polynomial RAM, twiddle factor ROM, and both butterfly types internally.

```mermaid
graph TD
    subgraph "ntt_engine"
        EXT["ext_addr/din/we"] --> MUX["RAM port mux"]
        FSM["FSM + addr gen"] --> MUX

        MUX --> RAM["poly_ram (256x12 dual-port)"]
        FSM --> ROM["ntt_rom (128x12)"]

        RAM -->|dout_a: even| CT["ntt_butterfly (CT)"]
        RAM -->|dout_b: odd| CT
        ROM -->|zeta| CT

        RAM -->|dout_a: even| GS["intt_butterfly (GS)"]
        RAM -->|dout_b: odd| GS
        ROM -->|zeta| GS

        CT -->|"mode=0"| BMUX["butterfly mux"]
        GS -->|"mode=1"| BMUX
        BMUX --> RAM

        RAM -->|dout_a| SCALE["barrett_reduce (×3303)"]
        SCALE -->|"INTT scaling"| RAM
    end
```

**Timing:**
- Forward NTT: 1800 cycles (7 layers × 257 + 1 done)
- Inverse NTT: 2313 cycles (1800 + 1 init + 512 scale)
- At 100 MHz: 18 µs / 23 µs per NTT/INTT

**FSM States:** `IDLE → LAYER_INIT → BF_READ → BF_WRITE → ... → [SCALE_INIT → SCALE_READ → SCALE_WRITE → ...] → DONE → IDLE`

## Basemul

Pointwise polynomial multiplication in the NTT domain. Kyber's NTT is "incomplete" —
it decomposes a degree-256 polynomial into 128 degree-1 polynomials in Z_q[X]/(X^2 - γ_i).
So "pointwise multiplication" is 128 independent 2×2 basemul operations, processed as
64 pairs (each with +zeta and -zeta).

### basemul_unit (combinational)

Single 2×2 basemul: `(a0 + a1·X)(b0 + b1·X) mod (X² - ζ)`:
- `c0 = a0·b0 + a1·b1·ζ mod q`
- `c1 = a0·b1 + a1·b0 mod q`

Optimized to 3 Barrett reductions (not 5) by accumulating products before reducing:

```mermaid
graph LR
    subgraph "basemul_unit"
        A1B1["a1 × b1 (24-bit)"] --> BR1["barrett_reduce #(24)"]
        BR1 --> T1["t1 (12-bit)"]
        T1 --> T1Z["t1 × zeta (24-bit)"]
        A0B0["a0 × b0 (24-bit)"] --> ACC0["+"]
        T1Z --> ACC0
        ACC0 --> BR2["barrett_reduce #(25)"]
        BR2 --> C0["c0"]

        A0B1["a0 × b1 (24-bit)"] --> ACC1["+"]
        A1B0["a1 × b0 (24-bit)"] --> ACC1
        ACC1 --> BR3["barrett_reduce #(25)"]
        BR3 --> C1["c1"]
    end
```

### poly_basemul (sequential FSM)

Wraps `basemul_unit` with two `poly_ram` instances and one `ntt_rom`:

```mermaid
graph TD
    subgraph "poly_basemul"
        EXT_A["a_addr/din/we"] --> MUXA["RAM A port mux"]
        EXT_B["b_addr/din/we"] --> MUXB["RAM B port mux"]
        FSM["FSM + addr gen"] --> MUXA
        FSM --> MUXB
        FSM --> ROM["ntt_rom (addrs 64..127)"]

        MUXA --> RAMA["poly_ram A (result in-place)"]
        MUXB --> RAMB["poly_ram B (read-only)"]

        RAMA -->|"a0, a1"| BM["basemul_unit"]
        RAMB -->|"b0, b1"| BM
        ROM -->|"±zeta"| BM

        BM -->|"c0, c1"| RAMA
    end
```

**Timing:** 257 cycles (64 pairs × 4 cycles + 1 done). At 100 MHz: 2.57 µs.

**FSM States:** `IDLE → READ_POS → WRITE_POS → READ_NEG → WRITE_NEG → ... → DONE → IDLE`

## Development Roadmap

### Milestone 1 -- Modular Arithmetic (complete)
| Module | Status | Description |
|--------|--------|-------------|
| `cond_sub_q` | Done | Conditional subtraction, [0,2q-1] to [0,q-1] |
| `barrett_reduce` | Done | Barrett reduction mod 3329, parameterized width |

### Milestone 2 -- NTT Butterfly (complete)
| Module | Status | Description |
|--------|--------|-------------|
| `cond_add_q` | Done | Conditional addition for subtraction underflow |
| `mod_add` | Done | Modular addition in Z_q |
| `mod_sub` | Done | Modular subtraction in Z_q |
| `ntt_butterfly` | Done | Cooley-Tukey butterfly (multiply + add/sub) |

### Milestone 3 -- NTT/INTT Engine (complete)
| Module | Status | Description |
|--------|--------|-------------|
| `intt_butterfly` | Done | Gentleman-Sande inverse butterfly (add/sub + multiply) |
| `ntt_rom` | Done | Twiddle factor (zeta) lookup ROM, 128 x 12-bit |
| `poly_ram` | Done | True dual-port synchronous RAM, 256 x 12-bit |
| `ntt_engine` | Done | Full 7-layer NTT/INTT FSM with address generation |

### Milestone 4 -- Kyber Operations (in progress)
| Module | Status | Description |
|--------|--------|-------------|
| `basemul_unit` | Done | Single 2×2 basemul, combinational (3 Barrett reductions) |
| `poly_basemul` | Done | Pointwise multiply in NTT domain (257 cycles) |
| `compress` / `decompress` | Planned | Bit compression for ciphertext |
| `kyber_top` | Planned | Top-level encaps/decaps controller |

## FPGA Target Notes

**Artix-7 (XC7A35T):**
- DSP48E1 slices: 90 available. Barrett multiply (a * V) maps to one DSP.
- Block RAM: 50 x 36Kb. Coefficient storage + twiddle ROM fit comfortably.
- Target clock: 100-200 MHz (TBD after place-and-route).
