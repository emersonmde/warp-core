# Warp Core Architecture

## Module Hierarchy

Currently implemented modules (Milestones 1-3: modular arithmetic, NTT butterfly, NTT/INTT engine):

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

### Milestone 4 -- Kyber Operations
| Module | Status | Description |
|--------|--------|-------------|
| `poly_basemul` | Planned | Pointwise multiply in NTT domain |
| `compress` / `decompress` | Planned | Bit compression for ciphertext |
| `kyber_top` | Planned | Top-level encaps/decaps controller |

## FPGA Target Notes

**Artix-7 (XC7A35T):**
- DSP48E1 slices: 90 available. Barrett multiply (a * V) maps to one DSP.
- Block RAM: 50 x 36Kb. Coefficient storage + twiddle ROM fit comfortably.
- Target clock: 100-200 MHz (TBD after place-and-route).
