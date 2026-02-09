// cbd_sampler — Centered Binomial Distribution sampler for Kyber noise generation
//
// Consumes 128 random bytes via a valid/ready handshake and produces
// 256 coefficients sampled from CBD η=2, stored in an internal poly_ram.
//
// Algorithm (FIPS 203, Section 4.2.2, η=2):
//   Each byte yields 2 coefficients from its low and high nibbles.
//   For nibble [b3 b2 b1 b0]:
//     coeff = (b0+b1) - (b2+b3)   ∈ [-2, 2]
//   Mapped to [0, q-1]: negative values become q + coeff.
//
// Dual-port write trick: both nibbles processed simultaneously,
// writing lo_coeff to port A at 2*byte_idx and hi_coeff to port B
// at 2*byte_idx+1.  Throughput: 1 byte/cycle when byte_valid is high.
//
// FSM: S_IDLE → S_RUN (128 bytes) → S_DONE (1 cycle) → S_IDLE
// Total: 129 cycles when byte_valid is always asserted.

module cbd_sampler (
    input  wire        clk,
    input  wire        rst_n,

    // Control
    input  wire        start,
    output reg         done,
    output wire        busy,

    // Byte stream input (valid/ready handshake)
    input  wire        byte_valid,
    input  wire [7:0]  byte_data,
    output wire        byte_ready,

    // Result polynomial read port (external, during IDLE)
    input  wire [7:0]  r_addr,
    output wire [11:0] r_dout
);

`include "kyber_pkg.vh"

    // ─── FSM states ─────────────────────────────────────────────
    localparam S_IDLE = 2'd0;
    localparam S_RUN  = 2'd1;
    localparam S_DONE = 2'd2;

    reg [1:0] state;
    reg [6:0] byte_idx;   // 0..127

    // ─── CBD nibble-to-coefficient logic ────────────────────────
    wire [3:0] lo_nibble = byte_data[3:0];
    wire [3:0] hi_nibble = byte_data[7:4];

    // Low nibble: (b0+b1) - (b2+b3)
    wire [1:0] lo_a = {1'b0, lo_nibble[0]} + {1'b0, lo_nibble[1]};
    wire [1:0] lo_b = {1'b0, lo_nibble[2]} + {1'b0, lo_nibble[3]};
    wire [11:0] lo_coeff = (lo_a >= lo_b)
        ? {10'd0, lo_a - lo_b}
        : KYBER_Q - {10'd0, lo_b - lo_a};

    // High nibble: (b0+b1) - (b2+b3)
    wire [1:0] hi_a = {1'b0, hi_nibble[0]} + {1'b0, hi_nibble[1]};
    wire [1:0] hi_b = {1'b0, hi_nibble[2]} + {1'b0, hi_nibble[3]};
    wire [11:0] hi_coeff = (hi_a >= hi_b)
        ? {10'd0, hi_a - hi_b}
        : KYBER_Q - {10'd0, hi_b - hi_a};

    // ─── Handshake: accept bytes only during S_RUN ──────────────
    assign byte_ready = (state == S_RUN);
    wire byte_accepted = byte_valid & byte_ready;

    // ─── Busy / done ────────────────────────────────────────────
    assign busy = (state != S_IDLE);

    // ─── RAM signals ────────────────────────────────────────────
    reg        ram_we_a;
    reg  [7:0] ram_addr_a;
    reg [11:0] ram_din_a;
    reg        ram_we_b;
    reg  [7:0] ram_addr_b;
    reg [11:0] ram_din_b;
    wire [11:0] ram_dout_a;

    poly_ram u_ram (
        .clk    (clk),
        .we_a   (ram_we_a),
        .addr_a (ram_addr_a),
        .din_a  (ram_din_a),
        .dout_a (ram_dout_a),
        .we_b   (ram_we_b),
        .addr_b (ram_addr_b),
        .din_b  (ram_din_b),
        .dout_b ()            // Port B dout unused
    );

    assign r_dout = ram_dout_a;

    // ─── FSM ────────────────────────────────────────────────────
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state    <= S_IDLE;
            done     <= 1'b0;
            byte_idx <= 7'd0;
        end else begin
            done <= 1'b0;

            case (state)
                S_IDLE: begin
                    if (start) begin
                        byte_idx <= 7'd0;
                        state    <= S_RUN;
                    end
                end

                S_RUN: begin
                    if (byte_accepted) begin
                        if (byte_idx == 7'd127)
                            state <= S_DONE;
                        else
                            byte_idx <= byte_idx + 7'd1;
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

    // ─── RAM port mux ───────────────────────────────────────────
    always @(*) begin
        // Defaults: external read, no writes
        ram_we_a   = 1'b0;
        ram_addr_a = r_addr;
        ram_din_a  = 12'd0;
        ram_we_b   = 1'b0;
        ram_addr_b = 8'd0;
        ram_din_b  = 12'd0;

        if (state == S_RUN && byte_accepted) begin
            // Port A: write lo_coeff at 2*byte_idx
            ram_we_a   = 1'b1;
            ram_addr_a = {byte_idx, 1'b0};
            ram_din_a  = lo_coeff;
            // Port B: write hi_coeff at 2*byte_idx+1
            ram_we_b   = 1'b1;
            ram_addr_b = {byte_idx, 1'b1};
            ram_din_b  = hi_coeff;
        end
    end

endmodule
