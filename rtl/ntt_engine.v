// ntt_engine — 7-layer NTT/INTT engine for CRYSTALS-Kyber
//
// Performs forward NTT (Cooley-Tukey) or inverse NTT (Gentleman-Sande)
// on a 256-element polynomial stored in internal dual-port RAM.
//
// Forward NTT:  7 layers × 128 butterflies × 2 cycles = 1792 cycles
// Inverse NTT:  1792 cycles + 256 scaling cycles = 2048 cycles
//
// External interface: during IDLE, port A is available for loading/reading
// coefficients. During NTT/INTT computation, the engine owns both ports.
//
// Each butterfly takes 2 clock cycles:
//   Phase 0 (READ):  present addresses to RAM + ROM, no writes
//   Phase 1 (WRITE): butterfly outputs written back to RAM

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
    localparam S_IDLE       = 3'd0;
    localparam S_LAYER_INIT = 3'd1;
    localparam S_BF_READ    = 3'd2;
    localparam S_BF_WRITE   = 3'd3;
    localparam S_SCALE_INIT = 3'd4;
    localparam S_SCALE_READ = 3'd5;
    localparam S_SCALE_WRITE= 3'd6;
    localparam S_DONE       = 3'd7;

    reg [2:0] state;

    // ─── Counters ───────────────────────────────────────────────
    reg [2:0]  layer;       // 0..6 (7 layers)
    reg [6:0]  group;       // group index within layer
    reg [6:0]  j_offset;    // butterfly offset within group
    reg [7:0]  scale_addr;  // 0..255 for scaling pass
    reg        mode_reg;    // latched mode

    // ─── Address generation ─────────────────────────────────────
    // Forward: length = 128 >> layer, groups = 1 << layer
    //          start = group * 2 * length = group << (8 - layer)
    //          zeta_idx = (1 << layer) + group
    // Inverse: length = 2 << layer, groups = 64 >> layer
    //          start = group * 2 * length = group << (layer + 2)
    //          zeta_idx = (128 >> layer) - 1 - group

    // Forward NTT parameters
    // length=128>>layer, groups=1<<layer, start=group*(256>>layer)
    wire [7:0] fwd_length = 8'd128 >> layer;
    wire [6:0] fwd_groups = 7'd1 << layer;
    wire [6:0] fwd_zeta   = (7'd1 << layer) + group;

    // Inverse NTT parameters
    // length=2<<layer, groups=64>>layer, start=group*(4<<layer)
    wire [7:0] inv_length = 8'd2 << layer;
    wire [6:0] inv_groups = 7'd64 >> layer;
    wire [6:0] inv_zeta   = (8'd128 >> layer) - 8'd1 - {1'b0, group};

    // Start address: group * 2 * length
    // Forward: group * (256 >> layer)  →  group << (8 - layer)
    // Inverse: group * (4 << layer)    →  group << (layer + 2)
    reg [7:0] bf_start;
    always @(*) begin
        if (mode_reg)
            bf_start = {1'b0, group} << (layer + 4'd2);
        else
            bf_start = {1'b0, group} << (4'd8 - {1'b0, layer});
    end

    wire [7:0] bf_length;
    wire [6:0] bf_groups;
    wire [6:0] bf_zeta_idx;

    assign bf_length   = mode_reg ? inv_length : fwd_length;
    assign bf_groups   = mode_reg ? inv_groups : fwd_groups;
    assign bf_zeta_idx = mode_reg ? inv_zeta   : fwd_zeta;

    wire [7:0] addr_even = bf_start + {1'b0, j_offset};
    wire [7:0] addr_odd  = bf_start + {1'b0, j_offset} + bf_length;

    // ─── RAM signals ────────────────────────────────────────────
    reg        ram_we_a, ram_we_b;
    reg  [7:0] ram_addr_a, ram_addr_b;
    reg [11:0] ram_din_a, ram_din_b;
    wire [11:0] ram_dout_a, ram_dout_b;

    poly_ram u_ram (
        .clk    (clk),
        .we_a   (ram_we_a),
        .addr_a (ram_addr_a),
        .din_a  (ram_din_a),
        .dout_a (ram_dout_a),
        .we_b   (ram_we_b),
        .addr_b (ram_addr_b),
        .din_b  (ram_din_b),
        .dout_b (ram_dout_b)
    );

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
        .even     (ram_dout_a),
        .odd      (ram_dout_b),
        .zeta     (rom_zeta),
        .even_out (ct_even_out),
        .odd_out  (ct_odd_out)
    );

    // ─── Inverse butterfly (Gentleman-Sande) ────────────────────
    wire [11:0] gs_even_out, gs_odd_out;

    intt_butterfly u_gs_bf (
        .even     (ram_dout_a),
        .odd      (ram_dout_b),
        .zeta     (rom_zeta),
        .even_out (gs_even_out),
        .odd_out  (gs_odd_out)
    );

    // ─── Butterfly output mux ───────────────────────────────────
    wire [11:0] bf_even_out = mode_reg ? gs_even_out : ct_even_out;
    wire [11:0] bf_odd_out  = mode_reg ? gs_odd_out  : ct_odd_out;

    // ─── Scale multiplier (INTT: coeff * 3303 mod q) ────────────
    localparam SCALE_PROD_WIDTH = 2 * COEFF_WIDTH;  // 24

    wire [SCALE_PROD_WIDTH-1:0] scale_product;
    wire [11:0] scale_result;

    assign scale_product = ram_dout_a * KYBER_N_INV;

    barrett_reduce #(
        .INPUT_WIDTH (SCALE_PROD_WIDTH)
    ) u_scale_barrett (
        .a      (scale_product),
        .result (scale_result)
    );

    // ─── Busy signal ────────────────────────────────────────────
    assign busy = (state != S_IDLE);

    // ─── External I/O ───────────────────────────────────────────
    assign ext_dout = ram_dout_a;

    // ─── FSM ────────────────────────────────────────────────────
    // Determine end-of-group and end-of-layer
    wire last_j_in_group = ({1'b0, j_offset} == bf_length - 8'd1);
    wire last_group_in_layer = (group == bf_groups - 7'd1);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state      <= S_IDLE;
            done       <= 1'b0;
            layer      <= 3'd0;
            group      <= 7'd0;
            j_offset   <= 7'd0;
            scale_addr <= 8'd0;
            mode_reg   <= 1'b0;
        end else begin
            done <= 1'b0;

            case (state)
                S_IDLE: begin
                    if (start) begin
                        mode_reg <= mode;
                        layer    <= 3'd0;
                        state    <= S_LAYER_INIT;
                    end
                end

                S_LAYER_INIT: begin
                    group    <= 7'd0;
                    j_offset <= 7'd0;
                    state    <= S_BF_READ;
                end

                S_BF_READ: begin
                    // RAM + ROM addressed in this cycle; outputs available next cycle
                    state <= S_BF_WRITE;
                end

                S_BF_WRITE: begin
                    // Butterfly results written back to RAM
                    if (last_j_in_group) begin
                        if (last_group_in_layer) begin
                            // Layer complete
                            if (layer == 3'd6) begin
                                // All 7 layers done
                                if (mode_reg) begin
                                    // INTT: need scaling pass
                                    scale_addr <= 8'd0;
                                    state      <= S_SCALE_INIT;
                                end else begin
                                    state <= S_DONE;
                                end
                            end else begin
                                layer <= layer + 3'd1;
                                state <= S_LAYER_INIT;
                            end
                        end else begin
                            group    <= group + 7'd1;
                            j_offset <= 7'd0;
                            state    <= S_BF_READ;
                        end
                    end else begin
                        j_offset <= j_offset + 7'd1;
                        state    <= S_BF_READ;
                    end
                end

                S_SCALE_INIT: begin
                    // Set up first scale read
                    state <= S_SCALE_READ;
                end

                S_SCALE_READ: begin
                    // RAM output available next cycle
                    state <= S_SCALE_WRITE;
                end

                S_SCALE_WRITE: begin
                    if (scale_addr == 8'd255) begin
                        state <= S_DONE;
                    end else begin
                        scale_addr <= scale_addr + 8'd1;
                        state      <= S_SCALE_READ;
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

    // ─── RAM port mux (active in same cycle as state) ───────────
    always @(*) begin
        // Defaults: no writes, external access on port A
        ram_we_a   = 1'b0;
        ram_we_b   = 1'b0;
        ram_addr_a = ext_addr;
        ram_addr_b = 8'd0;
        ram_din_a  = ext_din;
        ram_din_b  = 12'd0;
        rom_addr   = 7'd0;

        case (state)
            S_IDLE: begin
                ram_we_a   = ext_we;
                ram_addr_a = ext_addr;
                ram_din_a  = ext_din;
            end

            S_BF_READ: begin
                // Present butterfly addresses for read
                ram_addr_a = addr_even;
                ram_addr_b = addr_odd;
                rom_addr   = bf_zeta_idx;
            end

            S_BF_WRITE: begin
                // Write butterfly results back
                ram_we_a   = 1'b1;
                ram_we_b   = 1'b1;
                ram_addr_a = addr_even;
                ram_addr_b = addr_odd;
                ram_din_a  = bf_even_out;
                ram_din_b  = bf_odd_out;
                rom_addr   = bf_zeta_idx;  // keep ROM addressed for butterfly
            end

            S_SCALE_INIT, S_SCALE_READ: begin
                ram_addr_a = scale_addr;
            end

            S_SCALE_WRITE: begin
                ram_we_a   = 1'b1;
                ram_addr_a = scale_addr;
                ram_din_a  = scale_result;
            end

            default: begin
                // Keep defaults
            end
        endcase
    end

endmodule
