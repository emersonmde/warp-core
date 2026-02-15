// ntt_engine — 7-layer NTT/INTT engine for CRYSTALS-Kyber
//
// Ping-pong architecture: two poly_ram instances alternate as source/dest
// per layer, enabling 1-butterfly-per-cycle throughput after a 1-cycle prime.
//
// Forward NTT:  7 × (1 INIT + 1 PRIME + 127 OVERLAP + 1 FLUSH) + 1 DONE = 911 cycles
// Inverse NTT:  911 + 1 SCALE_INIT + 128×2 SCALE + 1 DONE = 1168 cycles
//
// External interface: during IDLE, port A of the DATA ram is available for
// loading/reading coefficients. data_in_b tracks which RAM currently holds
// the polynomial (flips after each 7-layer transform).

module ntt_engine (
    input  wire        clk,
    input  wire        rst_n,

    // Control
    input  wire        start,
    input  wire        mode,       // 0 = forward NTT, 1 = inverse NTT
    output reg         done,
    output wire        busy,

    // External coefficient I/O (active during IDLE only)
    input  wire        ext_we,
    input  wire [7:0]  ext_addr,
    input  wire [11:0] ext_din,
    output wire [11:0] ext_dout
);

`include "kyber_pkg.vh"

    // ─── FSM states ─────────────────────────────────────────────
    localparam S_IDLE       = 4'd0;
    localparam S_LAYER_INIT = 4'd1;
    localparam S_BF_PRIME   = 4'd2;
    localparam S_BF_OVERLAP = 4'd3;
    localparam S_BF_FLUSH   = 4'd4;
    localparam S_SCALE_INIT = 4'd5;
    localparam S_SCALE_READ = 4'd6;
    localparam S_SCALE_WRITE= 4'd7;
    localparam S_DONE       = 4'd8;

    reg [3:0] state;

    // ─── Counters ───────────────────────────────────────────────
    reg [2:0]  layer;
    reg [6:0]  group;
    reg [6:0]  j_offset;
    reg [6:0]  scale_idx;   // 0..127 for dual-port scaling
    reg        mode_reg;

    // ─── Ping-pong control ─────────────────────────────────────
    reg        data_in_b;   // which RAM holds the polynomial (0=A, 1=B)
    reg        src_sel;     // source RAM for current layer (0=A, 1=B)

    // ─── Pipeline registers ────────────────────────────────────
    reg  [7:0] wr_addr_even;
    reg  [7:0] wr_addr_odd;
    reg [11:0] zeta_reg;

    // ─── Address generation ─────────────────────────────────────
    wire [7:0] fwd_length = 8'd128 >> layer;
    wire [6:0] fwd_groups = 7'd1 << layer;
    wire [6:0] fwd_zeta   = (7'd1 << layer) + group;

    wire [7:0] inv_length = 8'd2 << layer;
    wire [6:0] inv_groups = 7'd64 >> layer;
    wire [6:0] inv_zeta   = (8'd128 >> layer) - 8'd1 - {1'b0, group};

    reg [7:0] bf_start;
    always @(*) begin
        if (mode_reg)
            bf_start = {1'b0, group} << (layer + 4'd2);
        else
            bf_start = {1'b0, group} << (4'd8 - {1'b0, layer});
    end

    wire [7:0] bf_length   = mode_reg ? inv_length : fwd_length;
    wire [6:0] bf_groups   = mode_reg ? inv_groups : fwd_groups;
    wire [6:0] bf_zeta_idx = mode_reg ? inv_zeta   : fwd_zeta;

    wire [7:0] addr_even = bf_start + {1'b0, j_offset};
    wire [7:0] addr_odd  = bf_start + {1'b0, j_offset} + bf_length;

    // ─── RAM A ─────────────────────────────────────────────────
    reg        rama_we_a, rama_we_b;
    reg  [7:0] rama_addr_a, rama_addr_b;
    reg [11:0] rama_din_a, rama_din_b;
    wire [11:0] rama_dout_a, rama_dout_b;

    poly_ram u_ram_a (
        .clk(clk),
        .we_a(rama_we_a), .addr_a(rama_addr_a), .din_a(rama_din_a), .dout_a(rama_dout_a),
        .we_b(rama_we_b), .addr_b(rama_addr_b), .din_b(rama_din_b), .dout_b(rama_dout_b)
    );

    // ─── RAM B ─────────────────────────────────────────────────
    reg        ramb_we_a, ramb_we_b;
    reg  [7:0] ramb_addr_a, ramb_addr_b;
    reg [11:0] ramb_din_a, ramb_din_b;
    wire [11:0] ramb_dout_a, ramb_dout_b;

    poly_ram u_ram_b (
        .clk(clk),
        .we_a(ramb_we_a), .addr_a(ramb_addr_a), .din_a(ramb_din_a), .dout_a(ramb_dout_a),
        .we_b(ramb_we_b), .addr_b(ramb_addr_b), .din_b(ramb_din_b), .dout_b(ramb_dout_b)
    );

    // ─── Output muxes ──────────────────────────────────────────
    // Source RAM outputs (for butterfly during BF states)
    wire [11:0] src_dout_a = src_sel ? ramb_dout_a : rama_dout_a;
    wire [11:0] src_dout_b = src_sel ? ramb_dout_b : rama_dout_b;

    // Data RAM outputs (for ext I/O and scaling)
    wire [11:0] data_dout_a = data_in_b ? ramb_dout_a : rama_dout_a;
    wire [11:0] data_dout_b = data_in_b ? ramb_dout_b : rama_dout_b;

    // ─── ROM ────────────────────────────────────────────────────
    reg  [6:0] rom_addr;
    wire [11:0] rom_zeta;

    ntt_rom u_rom (
        .addr (rom_addr),
        .zeta (rom_zeta)
    );

    // ─── Forward butterfly (Cooley-Tukey) ───────────────────────
    wire [11:0] ct_even_out, ct_odd_out;

    ntt_butterfly u_ct_bf (
        .even     (src_dout_a),
        .odd      (src_dout_b),
        .zeta     (zeta_reg),
        .even_out (ct_even_out),
        .odd_out  (ct_odd_out)
    );

    // ─── Inverse butterfly (Gentleman-Sande) ────────────────────
    wire [11:0] gs_even_out, gs_odd_out;

    intt_butterfly u_gs_bf (
        .even     (src_dout_a),
        .odd      (src_dout_b),
        .zeta     (zeta_reg),
        .even_out (gs_even_out),
        .odd_out  (gs_odd_out)
    );

    // ─── Butterfly output mux ───────────────────────────────────
    wire [11:0] bf_even_out = mode_reg ? gs_even_out : ct_even_out;
    wire [11:0] bf_odd_out  = mode_reg ? gs_odd_out  : ct_odd_out;

    // ─── Scale multipliers (INTT: coeff * 3303 mod q) ──────────
    localparam SCALE_PROD_WIDTH = 2 * COEFF_WIDTH;

    wire [SCALE_PROD_WIDTH-1:0] scale_product_a = data_dout_a * KYBER_N_INV;
    wire [SCALE_PROD_WIDTH-1:0] scale_product_b = data_dout_b * KYBER_N_INV;
    wire [11:0] scale_result_a, scale_result_b;

    barrett_reduce #(.INPUT_WIDTH(SCALE_PROD_WIDTH)) u_scale_barrett_a (
        .a(scale_product_a), .result(scale_result_a)
    );

    barrett_reduce #(.INPUT_WIDTH(SCALE_PROD_WIDTH)) u_scale_barrett_b (
        .a(scale_product_b), .result(scale_result_b)
    );

    // ─── Busy & external I/O ───────────────────────────────────
    assign busy = (state != S_IDLE);
    assign ext_dout = data_dout_a;

    // ─── FSM ────────────────────────────────────────────────────
    wire last_j_in_group = ({1'b0, j_offset} == bf_length - 8'd1);
    wire last_group_in_layer = (group == bf_groups - 7'd1);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state        <= S_IDLE;
            done         <= 1'b0;
            layer        <= 3'd0;
            group        <= 7'd0;
            j_offset     <= 7'd0;
            scale_idx    <= 7'd0;
            mode_reg     <= 1'b0;
            data_in_b    <= 1'b0;
            src_sel      <= 1'b0;
            wr_addr_even <= 8'd0;
            wr_addr_odd  <= 8'd0;
            zeta_reg     <= 12'd0;
        end else begin
            done <= 1'b0;

            case (state)
                S_IDLE: begin
                    if (start) begin
                        mode_reg <= mode;
                        layer    <= 3'd0;
                        src_sel  <= data_in_b;
                        state    <= S_LAYER_INIT;
                    end
                end

                S_LAYER_INIT: begin
                    group    <= 7'd0;
                    j_offset <= 7'd0;
                    state    <= S_BF_PRIME;
                end

                S_BF_PRIME: begin
                    // Capture pipeline registers for butterfly 0
                    wr_addr_even <= addr_even;
                    wr_addr_odd  <= addr_odd;
                    zeta_reg     <= rom_zeta;
                    // Advance to butterfly 1
                    if (last_j_in_group) begin
                        group    <= group + 7'd1;
                        j_offset <= 7'd0;
                    end else begin
                        j_offset <= j_offset + 7'd1;
                    end
                    state <= S_BF_OVERLAP;
                end

                S_BF_OVERLAP: begin
                    // Capture pipeline for current read
                    wr_addr_even <= addr_even;
                    wr_addr_odd  <= addr_odd;
                    zeta_reg     <= rom_zeta;
                    // Last butterfly being read?
                    if (last_j_in_group && last_group_in_layer) begin
                        state <= S_BF_FLUSH;
                    end else begin
                        if (last_j_in_group) begin
                            group    <= group + 7'd1;
                            j_offset <= 7'd0;
                        end else begin
                            j_offset <= j_offset + 7'd1;
                        end
                    end
                end

                S_BF_FLUSH: begin
                    if (layer == 3'd6) begin
                        data_in_b <= ~data_in_b;
                        if (mode_reg) begin
                            scale_idx <= 7'd0;
                            state     <= S_SCALE_INIT;
                        end else begin
                            state <= S_DONE;
                        end
                    end else begin
                        layer   <= layer + 3'd1;
                        src_sel <= ~src_sel;
                        state   <= S_LAYER_INIT;
                    end
                end

                S_SCALE_INIT: begin
                    state <= S_SCALE_READ;
                end

                S_SCALE_READ: begin
                    state <= S_SCALE_WRITE;
                end

                S_SCALE_WRITE: begin
                    if (scale_idx == 7'd127) begin
                        state <= S_DONE;
                    end else begin
                        scale_idx <= scale_idx + 7'd1;
                        state     <= S_SCALE_READ;
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

    // ─── RAM port mux ──────────────────────────────────────────
    always @(*) begin
        // Defaults: all ports idle
        rama_we_a = 1'b0; rama_addr_a = 8'd0; rama_din_a = 12'd0;
        rama_we_b = 1'b0; rama_addr_b = 8'd0; rama_din_b = 12'd0;
        ramb_we_a = 1'b0; ramb_addr_a = 8'd0; ramb_din_a = 12'd0;
        ramb_we_b = 1'b0; ramb_addr_b = 8'd0; ramb_din_b = 12'd0;
        rom_addr  = 7'd0;

        case (state)
            S_IDLE: begin
                if (data_in_b) begin
                    ramb_we_a   = ext_we;
                    ramb_addr_a = ext_addr;
                    ramb_din_a  = ext_din;
                end else begin
                    rama_we_a   = ext_we;
                    rama_addr_a = ext_addr;
                    rama_din_a  = ext_din;
                end
            end

            S_BF_PRIME: begin
                rom_addr = bf_zeta_idx;
                if (src_sel) begin
                    ramb_addr_a = addr_even;
                    ramb_addr_b = addr_odd;
                end else begin
                    rama_addr_a = addr_even;
                    rama_addr_b = addr_odd;
                end
            end

            S_BF_OVERLAP: begin
                rom_addr = bf_zeta_idx;
                if (src_sel) begin
                    // Source = B (read), Dest = A (write)
                    ramb_addr_a = addr_even;
                    ramb_addr_b = addr_odd;
                    rama_we_a   = 1'b1;
                    rama_we_b   = 1'b1;
                    rama_addr_a = wr_addr_even;
                    rama_addr_b = wr_addr_odd;
                    rama_din_a  = bf_even_out;
                    rama_din_b  = bf_odd_out;
                end else begin
                    // Source = A (read), Dest = B (write)
                    rama_addr_a = addr_even;
                    rama_addr_b = addr_odd;
                    ramb_we_a   = 1'b1;
                    ramb_we_b   = 1'b1;
                    ramb_addr_a = wr_addr_even;
                    ramb_addr_b = wr_addr_odd;
                    ramb_din_a  = bf_even_out;
                    ramb_din_b  = bf_odd_out;
                end
            end

            S_BF_FLUSH: begin
                if (src_sel) begin
                    // Dest = A
                    rama_we_a   = 1'b1;
                    rama_we_b   = 1'b1;
                    rama_addr_a = wr_addr_even;
                    rama_addr_b = wr_addr_odd;
                    rama_din_a  = bf_even_out;
                    rama_din_b  = bf_odd_out;
                end else begin
                    // Dest = B
                    ramb_we_a   = 1'b1;
                    ramb_we_b   = 1'b1;
                    ramb_addr_a = wr_addr_even;
                    ramb_addr_b = wr_addr_odd;
                    ramb_din_a  = bf_even_out;
                    ramb_din_b  = bf_odd_out;
                end
            end

            S_SCALE_INIT, S_SCALE_READ: begin
                if (data_in_b) begin
                    ramb_addr_a = {scale_idx, 1'b0};
                    ramb_addr_b = {scale_idx, 1'b1};
                end else begin
                    rama_addr_a = {scale_idx, 1'b0};
                    rama_addr_b = {scale_idx, 1'b1};
                end
            end

            S_SCALE_WRITE: begin
                if (data_in_b) begin
                    ramb_we_a   = 1'b1;
                    ramb_we_b   = 1'b1;
                    ramb_addr_a = {scale_idx, 1'b0};
                    ramb_addr_b = {scale_idx, 1'b1};
                    ramb_din_a  = scale_result_a;
                    ramb_din_b  = scale_result_b;
                end else begin
                    rama_we_a   = 1'b1;
                    rama_we_b   = 1'b1;
                    rama_addr_a = {scale_idx, 1'b0};
                    rama_addr_b = {scale_idx, 1'b1};
                    rama_din_a  = scale_result_a;
                    rama_din_b  = scale_result_b;
                end
            end

            default: begin
                // Keep defaults
            end
        endcase
    end

endmodule
