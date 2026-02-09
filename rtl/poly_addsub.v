// poly_addsub — Coefficient-wise polynomial add/sub in Z_q
//
// Computes a[i] ± b[i] mod q for all 256 coefficients. Result is stored
// in-place in RAM A. Mode input selects addition (0) or subtraction (1).
//
// Both mod_add and mod_sub are instantiated; a mux selects the result
// based on the latched mode register.
//
// Pipelined 258-cycle FSM:
//   S_IDLE  — External RAM access via port A of each RAM
//   S_PRIME — Present addr=0 on read ports (pipeline priming)
//   S_RUN   — 256 cycles: read a[i+1]/b[i+1] while writing result[i]
//   S_DONE  — Assert done for 1 cycle
//
// Port usage during S_RUN:
//   RAM_A port A: write result at coeff_idx
//   RAM_A port B: read a[coeff_idx+1] (pipeline next)
//   RAM_B port A: read b[coeff_idx+1] (pipeline next)
//
// No port conflicts: write addr i != read addr i+1 on RAM_A.

module poly_addsub (
    input  wire        clk,
    input  wire        rst_n,

    // Control
    input  wire        start,
    input  wire        mode,       // 0=add, 1=sub
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
    localparam S_IDLE  = 2'd0;
    localparam S_PRIME = 2'd1;
    localparam S_RUN   = 2'd2;
    localparam S_DONE  = 2'd3;

    reg [1:0] state;
    reg [7:0] coeff_idx;   // 0..255
    reg       mode_reg;    // latched mode

    // ─── Arithmetic units ─────────────────────────────────────────
    wire [11:0] add_result, sub_result;

    mod_add u_add (
        .a      (ram_a_dout_b),
        .b      (ram_b_dout_a),
        .result (add_result)
    );

    mod_sub u_sub (
        .a      (ram_a_dout_b),
        .b      (ram_b_dout_a),
        .result (sub_result)
    );

    wire [11:0] arith_result = mode_reg ? sub_result : add_result;

    // ─── RAM A signals ──────────────────────────────────────────
    reg        ram_a_we_a;
    reg  [7:0] ram_a_addr_a;
    reg [11:0] ram_a_din_a;
    reg  [7:0] ram_a_addr_b;
    wire [11:0] ram_a_dout_a, ram_a_dout_b;

    poly_ram u_ram_a (
        .clk    (clk),
        .we_a   (ram_a_we_a),
        .addr_a (ram_a_addr_a),
        .din_a  (ram_a_din_a),
        .dout_a (ram_a_dout_a),
        .we_b   (1'b0),
        .addr_b (ram_a_addr_b),
        .din_b  (12'd0),
        .dout_b (ram_a_dout_b)
    );

    // ─── RAM B signals ──────────────────────────────────────────
    reg        ram_b_we_a;
    reg  [7:0] ram_b_addr_a;
    reg [11:0] ram_b_din_a;
    wire [11:0] ram_b_dout_a;

    poly_ram u_ram_b (
        .clk    (clk),
        .we_a   (ram_b_we_a),
        .addr_a (ram_b_addr_a),
        .din_a  (ram_b_din_a),
        .dout_a (ram_b_dout_a),
        .we_b   (1'b0),
        .addr_b (8'd0),
        .din_b  (12'd0),
        .dout_b (b_dout)
    );

    // ─── Busy + external dout ───────────────────────────────────
    assign busy   = (state != S_IDLE);
    assign a_dout = ram_a_dout_a;

    // ─── FSM ────────────────────────────────────────────────────
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state     <= S_IDLE;
            done      <= 1'b0;
            coeff_idx <= 8'd0;
            mode_reg  <= 1'b0;
        end else begin
            done <= 1'b0;

            case (state)
                S_IDLE: begin
                    if (start) begin
                        mode_reg  <= mode;
                        coeff_idx <= 8'd0;
                        state     <= S_PRIME;
                    end
                end

                S_PRIME: begin
                    // Read ports already presenting addr 0
                    // Next cycle, dout will have a[0] and b[0]
                    state <= S_RUN;
                end

                S_RUN: begin
                    // Write result for coeff_idx, read coeff_idx+1
                    if (coeff_idx == 8'd255) begin
                        state <= S_DONE;
                    end else begin
                        coeff_idx <= coeff_idx + 8'd1;
                    end
                end

                S_DONE: begin
                    done  <= 1'b1;
                    state <= S_IDLE;
                end
            endcase
        end
    end

    // ─── RAM port mux ─────────────────────────────────────────
    always @(*) begin
        // Defaults: external access, no writes
        ram_a_we_a   = 1'b0;
        ram_a_addr_a = a_addr;
        ram_a_din_a  = a_din;
        ram_a_addr_b = 8'd0;

        ram_b_we_a   = 1'b0;
        ram_b_addr_a = b_addr;
        ram_b_din_a  = b_din;

        case (state)
            S_IDLE: begin
                ram_a_we_a   = a_we;
                ram_a_addr_a = a_addr;
                ram_a_din_a  = a_din;
                ram_b_we_a   = b_we;
                ram_b_addr_a = b_addr;
                ram_b_din_a  = b_din;
            end

            S_PRIME: begin
                // Present addr 0 on read ports
                ram_a_addr_b = 8'd0;
                ram_b_addr_a = 8'd0;
            end

            S_RUN: begin
                // Write result[coeff_idx] to RAM A port A
                ram_a_we_a   = 1'b1;
                ram_a_addr_a = coeff_idx;
                ram_a_din_a  = arith_result;

                // Read a[coeff_idx+1] from RAM A port B
                ram_a_addr_b = coeff_idx + 8'd1;

                // Read b[coeff_idx+1] from RAM B port A
                ram_b_addr_a = coeff_idx + 8'd1;
            end

            default: begin
                // Keep defaults
            end
        endcase
    end

endmodule
