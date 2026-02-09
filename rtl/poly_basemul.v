// poly_basemul — Pointwise polynomial multiplication in NTT domain
//
// Performs 128 basemul operations (64 pairs × 2 per pair: +zeta and -zeta)
// on two polynomials stored in internal dual-port RAMs. Result is stored
// in-place in RAM A.
//
// Each pair processes 4 coefficients:
//   +zeta basemul: c[4i], c[4i+1]   from a[4i..4i+1] * b[4i..4i+1]
//   -zeta basemul: c[4i+2], c[4i+3] from a[4i+2..4i+3] * b[4i+2..4i+3]
//
// Zeta values come from ntt_rom at addresses 64..127 (reuses existing ROM).
// neg_zeta = Q - zeta (combinational).
//
// FSM: Each pair takes 4 cycles (READ_POS → WRITE_POS → READ_NEG → WRITE_NEG).
// Total: 64 × 4 + 1 (DONE) = 257 cycles
//
// External interface: during IDLE, both RAMs are accessible for load/read.
// During compute, the engine owns both RAMs.

module poly_basemul (
    input  wire        clk,
    input  wire        rst_n,

    // Control
    input  wire        start,
    output reg         done,
    output wire        busy,

    // Polynomial A I/O (result stored here in-place)
    input  wire        a_we,
    input  wire [7:0]  a_addr,
    input  wire [11:0] a_din,
    output wire [11:0] a_dout,

    // Polynomial B I/O
    input  wire        b_we,
    input  wire [7:0]  b_addr,
    input  wire [11:0] b_din,
    output wire [11:0] b_dout
);

`include "kyber_pkg.vh"

    // ─── FSM states ─────────────────────────────────────────────
    localparam S_IDLE      = 3'd0;
    localparam S_READ_POS  = 3'd1;
    localparam S_WRITE_POS = 3'd2;
    localparam S_READ_NEG  = 3'd3;
    localparam S_WRITE_NEG = 3'd4;
    localparam S_DONE      = 3'd5;

    reg [2:0] state;
    reg [5:0] pair_idx;   // 0..63

    // ─── Address generation ─────────────────────────────────────
    wire [7:0] base_addr = {pair_idx, 2'b00};  // 4 * pair_idx

    wire [7:0] pos_even_addr = base_addr;           // 4i
    wire [7:0] pos_odd_addr  = base_addr | 8'd1;    // 4i+1
    wire [7:0] neg_even_addr = base_addr | 8'd2;    // 4i+2
    wire [7:0] neg_odd_addr  = base_addr | 8'd3;    // 4i+3

    // ─── ROM ────────────────────────────────────────────────────
    wire [6:0] rom_addr_val = 7'd64 + {1'b0, pair_idx};
    reg  [6:0] rom_addr;
    wire [11:0] rom_zeta;

    ntt_rom u_rom (
        .addr (rom_addr),
        .zeta (rom_zeta)
    );

    // neg_zeta = Q - zeta (combinational)
    wire [11:0] neg_zeta = KYBER_Q - rom_zeta;

    // ─── Basemul unit ───────────────────────────────────────────
    reg  [11:0] bm_a0, bm_a1, bm_b0, bm_b1, bm_zeta;
    wire [11:0] bm_c0, bm_c1;

    basemul_unit u_basemul (
        .a0   (bm_a0),
        .a1   (bm_a1),
        .b0   (bm_b0),
        .b1   (bm_b1),
        .zeta (bm_zeta),
        .c0   (bm_c0),
        .c1   (bm_c1)
    );

    // ─── RAM A signals ──────────────────────────────────────────
    reg        ram_a_we_a, ram_a_we_b;
    reg  [7:0] ram_a_addr_a, ram_a_addr_b;
    reg [11:0] ram_a_din_a, ram_a_din_b;
    wire [11:0] ram_a_dout_a, ram_a_dout_b;

    poly_ram u_ram_a (
        .clk    (clk),
        .we_a   (ram_a_we_a),
        .addr_a (ram_a_addr_a),
        .din_a  (ram_a_din_a),
        .dout_a (ram_a_dout_a),
        .we_b   (ram_a_we_b),
        .addr_b (ram_a_addr_b),
        .din_b  (ram_a_din_b),
        .dout_b (ram_a_dout_b)
    );

    // ─── RAM B signals ──────────────────────────────────────────
    reg        ram_b_we_a, ram_b_we_b;
    reg  [7:0] ram_b_addr_a, ram_b_addr_b;
    reg [11:0] ram_b_din_a, ram_b_din_b;
    wire [11:0] ram_b_dout_a, ram_b_dout_b;

    poly_ram u_ram_b (
        .clk    (clk),
        .we_a   (ram_b_we_a),
        .addr_a (ram_b_addr_a),
        .din_a  (ram_b_din_a),
        .dout_a (ram_b_dout_a),
        .we_b   (ram_b_we_b),
        .addr_b (ram_b_addr_b),
        .din_b  (ram_b_din_b),
        .dout_b (ram_b_dout_b)
    );

    // ─── Busy + external dout ───────────────────────────────────
    assign busy   = (state != S_IDLE);
    assign a_dout = ram_a_dout_a;
    assign b_dout = ram_b_dout_a;

    // ─── FSM ────────────────────────────────────────────────────
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state    <= S_IDLE;
            done     <= 1'b0;
            pair_idx <= 6'd0;
        end else begin
            done <= 1'b0;

            case (state)
                S_IDLE: begin
                    if (start) begin
                        pair_idx <= 6'd0;
                        state    <= S_READ_POS;
                    end
                end

                // 2-cycle pattern for +zeta basemul:
                // READ_POS: present pos addresses → RAM latches on posedge
                // WRITE_POS: dout valid → basemul computes → write results
                S_READ_POS: begin
                    state <= S_WRITE_POS;
                end

                S_WRITE_POS: begin
                    // Results written; transition to read neg pair
                    state <= S_READ_NEG;
                end

                // 2-cycle pattern for -zeta basemul:
                // READ_NEG: present neg addresses → RAM latches on posedge
                // WRITE_NEG: dout valid → basemul computes → write results
                S_READ_NEG: begin
                    state <= S_WRITE_NEG;
                end

                S_WRITE_NEG: begin
                    if (pair_idx == 6'd63) begin
                        state <= S_DONE;
                    end else begin
                        pair_idx <= pair_idx + 6'd1;
                        state    <= S_READ_POS;
                    end
                end

                S_DONE: begin
                    done  <= 1'b1;
                    state <= S_IDLE;
                end

                default: state <= S_IDLE;
            endcase
        end
    end

    // ─── RAM + basemul port mux ─────────────────────────────────
    //
    // The 2-cycle read-compute-write pattern (same as ntt_engine):
    //   READ  cycle: present addresses to RAM ports (no write)
    //   WRITE cycle: RAM dout valid from previous cycle's addresses
    //                → feed basemul (combinational) → write results
    //
    // For the +zeta → -zeta transition, WRITE_POS also sets up RAM B
    // with neg addresses (port pre-read), but RAM A ports are busy
    // writing, so RAM A neg read is deferred to READ_NEG.

    always @(*) begin
        // Defaults: no writes, external access
        ram_a_we_a   = 1'b0;
        ram_a_we_b   = 1'b0;
        ram_a_addr_a = a_addr;
        ram_a_addr_b = 8'd0;
        ram_a_din_a  = a_din;
        ram_a_din_b  = 12'd0;

        ram_b_we_a   = 1'b0;
        ram_b_we_b   = 1'b0;
        ram_b_addr_a = b_addr;
        ram_b_addr_b = 8'd0;
        ram_b_din_a  = b_din;
        ram_b_din_b  = 12'd0;

        rom_addr  = rom_addr_val;

        bm_a0   = 12'd0;
        bm_a1   = 12'd0;
        bm_b0   = 12'd0;
        bm_b1   = 12'd0;
        bm_zeta = 12'd0;

        case (state)
            S_IDLE: begin
                ram_a_we_a   = a_we;
                ram_a_addr_a = a_addr;
                ram_a_din_a  = a_din;
                ram_b_we_a   = b_we;
                ram_b_addr_a = b_addr;
                ram_b_din_a  = b_din;
            end

            // ─── +zeta pair ───────────────────────────────────
            S_READ_POS: begin
                // Present pos addresses for RAM read
                ram_a_addr_a = pos_even_addr;   // a[4i]
                ram_a_addr_b = pos_odd_addr;    // a[4i+1]
                ram_b_addr_a = pos_even_addr;   // b[4i]
                ram_b_addr_b = pos_odd_addr;    // b[4i+1]
            end

            S_WRITE_POS: begin
                // RAM dout valid: a[4i], a[4i+1], b[4i], b[4i+1]
                // Feed basemul with +zeta
                bm_a0   = ram_a_dout_a;   // a[4i]
                bm_a1   = ram_a_dout_b;   // a[4i+1]
                bm_b0   = ram_b_dout_a;   // b[4i]
                bm_b1   = ram_b_dout_b;   // b[4i+1]
                bm_zeta = rom_zeta;       // +zeta

                // Write basemul results to RAM A
                ram_a_we_a   = 1'b1;
                ram_a_we_b   = 1'b1;
                ram_a_addr_a = pos_even_addr;   // c0 → a[4i]
                ram_a_addr_b = pos_odd_addr;    // c1 → a[4i+1]
                ram_a_din_a  = bm_c0;
                ram_a_din_b  = bm_c1;

                // Pre-read: set up RAM B with neg addresses
                ram_b_addr_a = neg_even_addr;
                ram_b_addr_b = neg_odd_addr;
            end

            // ─── -zeta pair ──────────────────────────────────
            S_READ_NEG: begin
                // Present neg addresses for RAM A read
                // (RAM B was pre-read in WRITE_POS, but we re-present
                //  the same addresses to keep dout stable)
                ram_a_addr_a = neg_even_addr;   // a[4i+2]
                ram_a_addr_b = neg_odd_addr;    // a[4i+3]
                ram_b_addr_a = neg_even_addr;
                ram_b_addr_b = neg_odd_addr;
            end

            S_WRITE_NEG: begin
                // RAM dout valid: a[4i+2], a[4i+3], b[4i+2], b[4i+3]
                // Feed basemul with -zeta
                bm_a0   = ram_a_dout_a;   // a[4i+2]
                bm_a1   = ram_a_dout_b;   // a[4i+3]
                bm_b0   = ram_b_dout_a;   // b[4i+2]
                bm_b1   = ram_b_dout_b;   // b[4i+3]
                bm_zeta = neg_zeta;       // Q - zeta

                // Write basemul results to RAM A
                ram_a_we_a   = 1'b1;
                ram_a_we_b   = 1'b1;
                ram_a_addr_a = neg_even_addr;   // c0 → a[4i+2]
                ram_a_addr_b = neg_odd_addr;    // c1 → a[4i+3]
                ram_a_din_a  = bm_c0;
                ram_a_din_b  = bm_c1;
            end

            default: begin
                // Keep defaults
            end
        endcase
    end

endmodule
