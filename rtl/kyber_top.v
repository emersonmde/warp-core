// kyber_top — Top-level Kyber accelerator with micro-op FSM
//
// 20-slot polynomial RAM bank + host I/O + micro-op command interface.
// Each slot is a 256x12 dual-port RAM (poly_ram). During IDLE, Port A
// serves host reads/writes. During micro-ops, the FSM owns both ports.
//
// Sub-engines: ntt_engine, poly_basemul, cbd_sampler (each with internal RAM).
// Direct ops: mod_add, mod_sub, compress(D=1,4,10), decompress(D=1,4,10).
//
// BRAM budget: 20 (bank) + 1 (NTT) + 2 (basemul) + 1 (CBD) = 24 RAMB18E1.

`include "kyber_pkg.vh"

module kyber_top (
    input  wire        clk,
    input  wire        rst_n,

    // Host polynomial I/O (active during IDLE)
    input  wire        host_we,
    input  wire [4:0]  host_slot,     // 0..19
    input  wire [7:0]  host_addr,     // 0..255
    input  wire [11:0] host_din,
    output wire [11:0] host_dout,

    // Micro-op command interface
    input  wire [3:0]  cmd_op,        // opcode
    input  wire [4:0]  cmd_slot_a,    // primary slot operand
    input  wire [4:0]  cmd_slot_b,    // secondary slot operand
    input  wire [3:0]  cmd_param,     // D for compress/decompress, mode for NTT
    input  wire        start,
    output reg         done,
    output wire        busy,

    // CBD byte stream (external random source)
    input  wire        cbd_byte_valid,
    input  wire [7:0]  cbd_byte_data,
    output wire        cbd_byte_ready
);

    // ─── Constants ────────────────────────────────────────────────
    localparam NUM_SLOTS = 20;

    // Opcodes
    localparam OP_NOP           = 4'd0;
    localparam OP_COPY_TO_NTT   = 4'd1;
    localparam OP_COPY_FROM_NTT = 4'd2;
    localparam OP_RUN_NTT       = 4'd3;
    localparam OP_COPY_TO_BM_A  = 4'd4;
    localparam OP_COPY_TO_BM_B  = 4'd5;
    localparam OP_COPY_FROM_BM  = 4'd6;
    localparam OP_RUN_BASEMUL   = 4'd7;
    localparam OP_POLY_ADD      = 4'd8;
    localparam OP_POLY_SUB      = 4'd9;
    localparam OP_COMPRESS      = 4'd10;
    localparam OP_DECOMPRESS    = 4'd11;
    localparam OP_CBD_SAMPLE    = 4'd12;

    // FSM states
    localparam S_IDLE     = 3'd0;
    localparam S_COPY     = 3'd1;
    localparam S_RUN      = 3'd2;
    localparam S_DIRECT   = 3'd3;
    localparam S_CBD_RUN  = 3'd4;
    localparam S_CBD_COPY = 3'd5;
    localparam S_DONE     = 3'd6;

    // ─── FSM registers ───────────────────────────────────────────
    reg [2:0]  state;
    reg [8:0]  counter;       // 0..258 (enough for 258-cycle direct ops)
    reg [3:0]  op_reg;
    reg [4:0]  slot_a_reg;
    reg [4:0]  slot_b_reg;
    reg [3:0]  param_reg;

    assign busy = (state != S_IDLE);

    // ─── Bank RAM signals ─────────────────────────────────────────
    // Port A: broadcast addr/din, selective we per slot
    wire [7:0]  bank_addr_a;
    wire [11:0] bank_din_a;
    wire        slot_we_a   [0:NUM_SLOTS-1];
    wire [11:0] slot_dout_a [0:NUM_SLOTS-1];

    // Port B: broadcast addr/din, selective we per slot
    wire [7:0]  bank_addr_b;
    wire [11:0] bank_din_b;
    wire        slot_we_b   [0:NUM_SLOTS-1];
    wire [11:0] slot_dout_b [0:NUM_SLOTS-1];

    // FSM-driven signals
    reg [7:0]  fsm_addr_a;
    reg [11:0] fsm_din_a;
    reg        fsm_we_a_flag;   // write-enable for slot_a_reg
    reg [4:0]  fsm_we_a_slot;   // which slot to write on Port A
    reg [7:0]  fsm_addr_b;
    reg [11:0] fsm_din_b;
    reg        fsm_we_b_flag;   // write-enable for a Port B slot
    reg [4:0]  fsm_we_b_slot;   // which slot to write on Port B

    // Mux: IDLE → host, else → FSM
    assign bank_addr_a = (state == S_IDLE) ? host_addr : fsm_addr_a;
    assign bank_din_a  = (state == S_IDLE) ? host_din  : fsm_din_a;
    assign bank_addr_b = fsm_addr_b;
    assign bank_din_b  = fsm_din_b;

    // ─── Generate RAM bank ────────────────────────────────────────
    genvar gi;
    generate
        for (gi = 0; gi < NUM_SLOTS; gi = gi + 1) begin : bank
            // Port A write-enable: IDLE → host selects, else → FSM selects
            assign slot_we_a[gi] = (state == S_IDLE)
                ? ((host_slot == gi[4:0]) ? host_we : 1'b0)
                : ((fsm_we_a_slot == gi[4:0]) ? fsm_we_a_flag : 1'b0);

            // Port B write-enable: FSM only
            assign slot_we_b[gi] = (fsm_we_b_slot == gi[4:0]) ? fsm_we_b_flag : 1'b0;

            poly_ram u_ram (
                .clk    (clk),
                .we_a   (slot_we_a[gi]),
                .addr_a (bank_addr_a),
                .din_a  (bank_din_a),
                .dout_a (slot_dout_a[gi]),
                .we_b   (slot_we_b[gi]),
                .addr_b (bank_addr_b),
                .din_b  (bank_din_b),
                .dout_b (slot_dout_b[gi])
            );
        end
    endgenerate

    // Host output mux (valid during IDLE)
    assign host_dout = (state == S_IDLE && host_slot < NUM_SLOTS)
                     ? slot_dout_a[host_slot] : 12'd0;

    // Convenience: slot_a and slot_b dout (Port A reads for direct ops)
    wire [11:0] dout_a_sel = (slot_a_reg < NUM_SLOTS) ? slot_dout_a[slot_a_reg] : 12'd0;
    wire [11:0] dout_b_sel = (slot_b_reg < NUM_SLOTS) ? slot_dout_a[slot_b_reg] : 12'd0;

    // ─── NTT engine ───────────────────────────────────────────────
    reg        ntt_start;
    reg        ntt_ext_we;
    reg  [7:0] ntt_ext_addr;
    reg [11:0] ntt_ext_din;
    wire [11:0] ntt_ext_dout;
    wire        ntt_done;
    wire        ntt_busy;

    ntt_engine u_ntt (
        .clk      (clk),
        .rst_n    (rst_n),
        .start    (ntt_start),
        .mode     (param_reg[0]),
        .done     (ntt_done),
        .busy     (ntt_busy),
        .ext_we   (ntt_ext_we),
        .ext_addr (ntt_ext_addr),
        .ext_din  (ntt_ext_din),
        .ext_dout (ntt_ext_dout)
    );

    // ─── Basemul engine ──────────────────────────────────────────
    reg        bm_start;
    reg        bm_a_we;
    reg  [7:0] bm_a_addr;
    reg [11:0] bm_a_din;
    wire [11:0] bm_a_dout;
    reg        bm_b_we;
    reg  [7:0] bm_b_addr;
    reg [11:0] bm_b_din;
    wire [11:0] bm_b_dout;
    wire        bm_done;
    wire        bm_busy;

    poly_basemul u_basemul (
        .clk    (clk),
        .rst_n  (rst_n),
        .start  (bm_start),
        .done   (bm_done),
        .busy   (bm_busy),
        .a_we   (bm_a_we),
        .a_addr (bm_a_addr),
        .a_din  (bm_a_din),
        .a_dout (bm_a_dout),
        .b_we   (bm_b_we),
        .b_addr (bm_b_addr),
        .b_din  (bm_b_din),
        .b_dout (bm_b_dout)
    );

    // ─── CBD sampler ─────────────────────────────────────────────
    reg        cbd_start;
    reg  [7:0] cbd_r_addr;
    wire [11:0] cbd_r_dout;
    wire        cbd_done;
    wire        cbd_busy;

    cbd_sampler u_cbd (
        .clk        (clk),
        .rst_n      (rst_n),
        .start      (cbd_start),
        .done       (cbd_done),
        .busy       (cbd_busy),
        .byte_valid (cbd_byte_valid),
        .byte_data  (cbd_byte_data),
        .byte_ready (cbd_byte_ready),
        .r_addr     (cbd_r_addr),
        .r_dout     (cbd_r_dout)
    );

    // ─── Combinational arithmetic for direct ops ─────────────────
    wire [11:0] add_result;
    wire [11:0] sub_result;

    mod_add u_mod_add (
        .a      (dout_a_sel),
        .b      (dout_b_sel),
        .result (add_result)
    );

    mod_sub u_mod_sub (
        .a      (dout_a_sel),
        .b      (dout_b_sel),
        .result (sub_result)
    );

    // Compress instances (D=1, 4, 10)
    wire [0:0]  compress_d1_out;
    wire [3:0]  compress_d4_out;
    wire [9:0]  compress_d10_out;

    compress #(.D(1))  u_compress_d1  (.x(dout_a_sel), .result(compress_d1_out));
    compress #(.D(4))  u_compress_d4  (.x(dout_a_sel), .result(compress_d4_out));
    compress #(.D(10)) u_compress_d10 (.x(dout_a_sel), .result(compress_d10_out));

    // Decompress instances (D=1, 4, 10)
    wire [11:0] decompress_d1_out;
    wire [11:0] decompress_d4_out;
    wire [11:0] decompress_d10_out;

    decompress #(.D(1))  u_decompress_d1  (.y(dout_a_sel[0:0]),  .result(decompress_d1_out));
    decompress #(.D(4))  u_decompress_d4  (.y(dout_a_sel[3:0]),  .result(decompress_d4_out));
    decompress #(.D(10)) u_decompress_d10 (.y(dout_a_sel[9:0]),  .result(decompress_d10_out));

    // ─── Compress/decompress output mux ──────────────────────────
    reg [11:0] compress_result;
    reg [11:0] decompress_result;

    always @(*) begin
        case (param_reg)
            4'd1:    compress_result = {11'd0, compress_d1_out};
            4'd4:    compress_result = {8'd0,  compress_d4_out};
            4'd10:   compress_result = {2'd0,  compress_d10_out};
            default: compress_result = 12'd0;
        endcase
    end

    always @(*) begin
        case (param_reg)
            4'd1:    decompress_result = decompress_d1_out;
            4'd4:    decompress_result = decompress_d4_out;
            4'd10:   decompress_result = decompress_d10_out;
            default: decompress_result = 12'd0;
        endcase
    end

    // ─── Direct operation result mux ─────────────────────────────
    reg [11:0] direct_result;

    always @(*) begin
        case (op_reg)
            OP_POLY_ADD:    direct_result = add_result;
            OP_POLY_SUB:    direct_result = sub_result;
            OP_COMPRESS:    direct_result = compress_result;
            OP_DECOMPRESS:  direct_result = decompress_result;
            default:        direct_result = 12'd0;
        endcase
    end

    // ─── FSM sequential logic ─────────────────────────────────────
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state      <= S_IDLE;
            done       <= 1'b0;
            counter    <= 9'd0;
            op_reg     <= 4'd0;
            slot_a_reg <= 5'd0;
            slot_b_reg <= 5'd0;
            param_reg  <= 4'd0;
        end else begin
            done <= 1'b0;

            case (state)
                S_IDLE: begin
                    if (start) begin
                        op_reg     <= cmd_op;
                        slot_a_reg <= cmd_slot_a;
                        slot_b_reg <= cmd_slot_b;
                        param_reg  <= cmd_param;
                        counter    <= 9'd0;

                        case (cmd_op)
                            OP_NOP: begin
                                state <= S_DONE;
                            end
                            OP_COPY_TO_NTT, OP_COPY_FROM_NTT,
                            OP_COPY_TO_BM_A, OP_COPY_TO_BM_B,
                            OP_COPY_FROM_BM: begin
                                state <= S_COPY;
                            end
                            OP_RUN_NTT, OP_RUN_BASEMUL: begin
                                state <= S_RUN;
                            end
                            OP_POLY_ADD, OP_POLY_SUB,
                            OP_COMPRESS, OP_DECOMPRESS: begin
                                state <= S_DIRECT;
                            end
                            OP_CBD_SAMPLE: begin
                                state <= S_CBD_RUN;
                            end
                            default: begin
                                state <= S_DONE;
                            end
                        endcase
                    end
                end

                // ─── Copy: 257 cycles (0=prime read, 1..256=write) ───
                S_COPY: begin
                    if (counter == 9'd256)
                        state <= S_DONE;
                    else
                        counter <= counter + 9'd1;
                end

                // ─── Run sub-engine: wait for done ───────────────────
                S_RUN: begin
                    if (counter == 9'd0) begin
                        // Start pulse was generated combinationally; advance
                        counter <= 9'd1;
                    end else begin
                        if ((op_reg == OP_RUN_NTT && ntt_done) ||
                            (op_reg == OP_RUN_BASEMUL && bm_done))
                            state <= S_DONE;
                    end
                end

                // ─── Direct: 258 cycles (0=prime, 1..256=rw, 257=last wr)
                S_DIRECT: begin
                    if (counter == 9'd257)
                        state <= S_DONE;
                    else
                        counter <= counter + 9'd1;
                end

                // ─── CBD run: wait for sampler done ──────────────────
                S_CBD_RUN: begin
                    if (counter == 9'd0)
                        counter <= 9'd1;
                    else if (cbd_done) begin
                        counter <= 9'd0;
                        state   <= S_CBD_COPY;
                    end
                end

                // ─── CBD copy: 257 cycles (same as S_COPY) ──────────
                S_CBD_COPY: begin
                    if (counter == 9'd256)
                        state <= S_DONE;
                    else
                        counter <= counter + 9'd1;
                end

                S_DONE: begin
                    done  <= 1'b1;
                    state <= S_IDLE;
                end

                default: state <= S_IDLE;
            endcase
        end
    end

    // ─── FSM combinational control ────────────────────────────────
    //
    // Drives: fsm_addr_a/b, fsm_din_a/b, fsm_we_a/b_flag/slot,
    //         ntt_*, bm_*, cbd_* signals.

    always @(*) begin
        // Defaults: no writes, no sub-engine activity
        fsm_addr_a    = 8'd0;
        fsm_din_a     = 12'd0;
        fsm_we_a_flag = 1'b0;
        fsm_we_a_slot = 5'd0;
        fsm_addr_b    = 8'd0;
        fsm_din_b     = 12'd0;
        fsm_we_b_flag = 1'b0;
        fsm_we_b_slot = 5'd0;

        ntt_start    = 1'b0;
        ntt_ext_we   = 1'b0;
        ntt_ext_addr = 8'd0;
        ntt_ext_din  = 12'd0;

        bm_start  = 1'b0;
        bm_a_we   = 1'b0;
        bm_a_addr = 8'd0;
        bm_a_din  = 12'd0;
        bm_b_we   = 1'b0;
        bm_b_addr = 8'd0;
        bm_b_din  = 12'd0;

        cbd_start  = 1'b0;
        cbd_r_addr = 8'd0;

        case (state)
            // ─── Copy operations ──────────────────────────────────
            S_COPY: begin
                case (op_reg)
                    OP_COPY_TO_NTT: begin
                        // Read bank[slot_a] Port A → write NTT ext port
                        fsm_addr_a = counter[7:0];      // present next address
                        fsm_we_a_flag = 1'b0;
                        if (counter > 9'd0) begin
                            // Write previous cycle's dout to NTT
                            ntt_ext_we   = 1'b1;
                            ntt_ext_addr = counter[7:0] - 8'd1;
                            ntt_ext_din  = dout_a_sel;
                        end
                    end

                    OP_COPY_FROM_NTT: begin
                        // Read NTT ext port → write bank[slot_a] Port A
                        ntt_ext_addr = counter[7:0];    // present next address
                        if (counter > 9'd0) begin
                            fsm_we_a_flag = 1'b1;
                            fsm_we_a_slot = slot_a_reg;
                            fsm_addr_a    = counter[7:0] - 8'd1;
                            fsm_din_a     = ntt_ext_dout;
                        end
                    end

                    OP_COPY_TO_BM_A: begin
                        // Read bank[slot_a] Port A → write basemul RAM A
                        fsm_addr_a = counter[7:0];
                        if (counter > 9'd0) begin
                            bm_a_we   = 1'b1;
                            bm_a_addr = counter[7:0] - 8'd1;
                            bm_a_din  = dout_a_sel;
                        end
                    end

                    OP_COPY_TO_BM_B: begin
                        // Read bank[slot_a] Port A → write basemul RAM B
                        fsm_addr_a = counter[7:0];
                        if (counter > 9'd0) begin
                            bm_b_we   = 1'b1;
                            bm_b_addr = counter[7:0] - 8'd1;
                            bm_b_din  = dout_a_sel;
                        end
                    end

                    OP_COPY_FROM_BM: begin
                        // Read basemul RAM A → write bank[slot_a] Port A
                        bm_a_addr = counter[7:0];
                        if (counter > 9'd0) begin
                            fsm_we_a_flag = 1'b1;
                            fsm_we_a_slot = slot_a_reg;
                            fsm_addr_a    = counter[7:0] - 8'd1;
                            fsm_din_a     = bm_a_dout;
                        end
                    end

                    default: begin end
                endcase
            end

            // ─── Run sub-engine ───────────────────────────────────
            S_RUN: begin
                if (counter == 9'd0) begin
                    case (op_reg)
                        OP_RUN_NTT:     ntt_start = 1'b1;
                        OP_RUN_BASEMUL: bm_start  = 1'b1;
                        default: begin end
                    endcase
                end
            end

            // ─── Direct bank operations ───────────────────────────
            S_DIRECT: begin
                // Port A reads both slot_a and slot_b (same broadcast addr)
                // For add/sub: result written to slot_a Port B
                // For compress/decompress: read slot_a, write slot_b Port B
                fsm_addr_a = counter[7:0];   // present current read addr
                fsm_addr_b = (counter > 9'd0) ? (counter[7:0] - 8'd1) : 8'd0;

                if (counter >= 9'd1 && counter <= 9'd256) begin
                    fsm_din_b     = direct_result;
                    fsm_we_b_flag = 1'b1;
                    case (op_reg)
                        OP_POLY_ADD, OP_POLY_SUB:
                            fsm_we_b_slot = slot_a_reg;   // write back to slot_a
                        OP_COMPRESS, OP_DECOMPRESS:
                            fsm_we_b_slot = slot_b_reg;   // write to slot_b
                        default:
                            fsm_we_b_flag = 1'b0;
                    endcase
                end
            end

            // ─── CBD run ──────────────────────────────────────────
            S_CBD_RUN: begin
                if (counter == 9'd0)
                    cbd_start = 1'b1;
            end

            // ─── CBD copy ─────────────────────────────────────────
            S_CBD_COPY: begin
                // Read cbd internal RAM → write bank[slot_a] Port A
                cbd_r_addr = counter[7:0];
                if (counter > 9'd0) begin
                    fsm_we_a_flag = 1'b1;
                    fsm_we_a_slot = slot_a_reg;
                    fsm_addr_a    = counter[7:0] - 8'd1;
                    fsm_din_a     = cbd_r_dout;
                end
            end

            default: begin end
        endcase
    end

endmodule
