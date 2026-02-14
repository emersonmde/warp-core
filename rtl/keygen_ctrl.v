// keygen_ctrl — ML-KEM-768 key generation micro-op sequencer
//
// Sequences 69 micro-ops to perform KeyGen_inner():
//   Phase 0: CBD sampling (6 ops) — s[0..2], e[0..2]
//   Phase 1: NTT (18 ops) — NTT(s[0..2]) and NTT(e[0..2]) in-place
//   Phase 2: Matmul row 0 (15 ops) — A[0] · s_hat + e_hat[0] → t_hat[0]
//   Phase 3: Matmul row 1 (15 ops) — A[1] · s_hat + e_hat[1] → t_hat[1]
//   Phase 4: Matmul row 2 (15 ops) — A[2] · s_hat + e_hat[2] → t_hat[2]
//
// Slot allocation (20 slots in kyber_top bank):
//   0-8:   A_hat[i*3+j] (row-major) — preloaded by host
//   9-11:  s → s_hat (CBD sampled, NTT'd in-place)
//   12-14: e → e_hat (CBD sampled, NTT'd in-place)
//
// After completion:
//   0:     t_hat[0] (from row 0 matmul, accumulated into slot 0)
//   3:     t_hat[1] (from row 1 matmul, accumulated into slot 3)
//   6:     t_hat[2] (from row 2 matmul, accumulated into slot 6)
//   9-11:  s_hat (secret key, preserved)
//   12-14: e_hat (read-only during matmul, preserved)
//
// Note: keygen uses A · s_hat (row access: A[i][j] = slot i*3+j),
// while encaps uses A^T · r_hat (column access). Same slot layout.

module keygen_ctrl (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    output reg         done,
    output wire        busy,

    // Micro-op command interface (wired to kyber_top)
    output reg  [3:0]  cmd_op,
    output reg  [4:0]  cmd_slot_a,
    output reg  [4:0]  cmd_slot_b,
    output reg  [3:0]  cmd_param,
    output reg         cmd_start,

    // Feedback from kyber_top
    input  wire        cmd_done
);

    // Opcodes (must match kyber_top.v)
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
    localparam S_IDLE  = 2'd0;
    localparam S_ISSUE = 2'd1;
    localparam S_WAIT  = 2'd2;
    localparam S_DONE  = 2'd3;

    reg [1:0] state;
    reg [6:0] step;    // 0..68 (69 total micro-ops)

    assign busy = (state != S_IDLE);

    // ─── Micro-op decode: step → (op, slot_a, slot_b, param) ────────
    reg [3:0]  dec_op;
    reg [4:0]  dec_slot_a;
    reg [4:0]  dec_slot_b;
    reg [3:0]  dec_param;

    always @(*) begin
        // Defaults
        dec_op     = OP_NOP;
        dec_slot_a = 5'd0;
        dec_slot_b = 5'd0;
        dec_param  = 4'd0;

        case (step)
            // ═══ Phase 0: CBD sampling (6 ops) ═══
            // s[0..2] → slots 9-11
            7'd0:  begin dec_op = OP_CBD_SAMPLE; dec_slot_a = 5'd9;  end
            7'd1:  begin dec_op = OP_CBD_SAMPLE; dec_slot_a = 5'd10; end
            7'd2:  begin dec_op = OP_CBD_SAMPLE; dec_slot_a = 5'd11; end
            // e[0..2] → slots 12-14
            7'd3:  begin dec_op = OP_CBD_SAMPLE; dec_slot_a = 5'd12; end
            7'd4:  begin dec_op = OP_CBD_SAMPLE; dec_slot_a = 5'd13; end
            7'd5:  begin dec_op = OP_CBD_SAMPLE; dec_slot_a = 5'd14; end

            // ═══ Phase 1: NTT (18 ops) — 6 polys × 3 ops each ═══
            // NTT(s[0]) in-place at slot 9
            7'd6:  begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd9;  end
            7'd7:  begin dec_op = OP_RUN_NTT;        dec_param  = 4'd0;  end  // forward
            7'd8:  begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd9;  end
            // NTT(s[1]) in-place at slot 10
            7'd9:  begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd10; end
            7'd10: begin dec_op = OP_RUN_NTT;        dec_param  = 4'd0;  end
            7'd11: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd10; end
            // NTT(s[2]) in-place at slot 11
            7'd12: begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd11; end
            7'd13: begin dec_op = OP_RUN_NTT;        dec_param  = 4'd0;  end
            7'd14: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd11; end
            // NTT(e[0]) in-place at slot 12
            7'd15: begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd12; end
            7'd16: begin dec_op = OP_RUN_NTT;        dec_param  = 4'd0;  end
            7'd17: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd12; end
            // NTT(e[1]) in-place at slot 13
            7'd18: begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd13; end
            7'd19: begin dec_op = OP_RUN_NTT;        dec_param  = 4'd0;  end
            7'd20: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd13; end
            // NTT(e[2]) in-place at slot 14
            7'd21: begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd14; end
            7'd22: begin dec_op = OP_RUN_NTT;        dec_param  = 4'd0;  end
            7'd23: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd14; end

            // ═══ Phase 2: Matmul row 0 (15 ops, steps 24..38) ═══
            // A_hat[0][0]=slot0 * s_hat[0]=slot9 → acc in slot 0
            7'd24: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd0;  end
            7'd25: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd9;  end
            7'd26: begin dec_op = OP_RUN_BASEMUL;   end
            7'd27: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd0;  end
            // A_hat[0][1]=slot1 * s_hat[1]=slot10 → temp in slot 1
            7'd28: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd1;  end
            7'd29: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd10; end
            7'd30: begin dec_op = OP_RUN_BASEMUL;   end
            7'd31: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd1;  end
            7'd32: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd0;  dec_slot_b = 5'd1; end
            // A_hat[0][2]=slot2 * s_hat[2]=slot11 → temp in slot 2
            7'd33: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd2;  end
            7'd34: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd11; end
            7'd35: begin dec_op = OP_RUN_BASEMUL;   end
            7'd36: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd2;  end
            7'd37: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd0;  dec_slot_b = 5'd2; end
            // Add e_hat[0] (slot 12)
            7'd38: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd0;  dec_slot_b = 5'd12; end

            // ═══ Phase 3: Matmul row 1 (15 ops, steps 39..53) ═══
            // A_hat[1][0]=slot3 * s_hat[0]=slot9 → acc in slot 3
            7'd39: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd3;  end
            7'd40: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd9;  end
            7'd41: begin dec_op = OP_RUN_BASEMUL;   end
            7'd42: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd3;  end
            // A_hat[1][1]=slot4 * s_hat[1]=slot10 → temp in slot 4
            7'd43: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd4;  end
            7'd44: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd10; end
            7'd45: begin dec_op = OP_RUN_BASEMUL;   end
            7'd46: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd4;  end
            7'd47: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd3;  dec_slot_b = 5'd4; end
            // A_hat[1][2]=slot5 * s_hat[2]=slot11 → temp in slot 5
            7'd48: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd5;  end
            7'd49: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd11; end
            7'd50: begin dec_op = OP_RUN_BASEMUL;   end
            7'd51: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd5;  end
            7'd52: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd3;  dec_slot_b = 5'd5; end
            // Add e_hat[1] (slot 13)
            7'd53: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd3;  dec_slot_b = 5'd13; end

            // ═══ Phase 4: Matmul row 2 (15 ops, steps 54..68) ═══
            // A_hat[2][0]=slot6 * s_hat[0]=slot9 → acc in slot 6
            7'd54: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd6;  end
            7'd55: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd9;  end
            7'd56: begin dec_op = OP_RUN_BASEMUL;   end
            7'd57: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd6;  end
            // A_hat[2][1]=slot7 * s_hat[1]=slot10 → temp in slot 7
            7'd58: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd7;  end
            7'd59: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd10; end
            7'd60: begin dec_op = OP_RUN_BASEMUL;   end
            7'd61: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd7;  end
            7'd62: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd6;  dec_slot_b = 5'd7; end
            // A_hat[2][2]=slot8 * s_hat[2]=slot11 → temp in slot 8
            7'd63: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd8;  end
            7'd64: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd11; end
            7'd65: begin dec_op = OP_RUN_BASEMUL;   end
            7'd66: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd8;  end
            7'd67: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd6;  dec_slot_b = 5'd8; end
            // Add e_hat[2] (slot 14)
            7'd68: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd6;  dec_slot_b = 5'd14; end

            default: begin
                dec_op = OP_NOP;
            end
        endcase
    end

    // ─── FSM ────────────────────────────────────────────────────────
    localparam LAST_STEP = 7'd68;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state     <= S_IDLE;
            step      <= 7'd0;
            done      <= 1'b0;
            cmd_op    <= 4'd0;
            cmd_slot_a <= 5'd0;
            cmd_slot_b <= 5'd0;
            cmd_param  <= 4'd0;
            cmd_start  <= 1'b0;
        end else begin
            done      <= 1'b0;
            cmd_start <= 1'b0;

            case (state)
                S_IDLE: begin
                    if (start) begin
                        step  <= 7'd0;
                        state <= S_ISSUE;
                    end
                end

                S_ISSUE: begin
                    cmd_op     <= dec_op;
                    cmd_slot_a <= dec_slot_a;
                    cmd_slot_b <= dec_slot_b;
                    cmd_param  <= dec_param;
                    cmd_start  <= 1'b1;
                    state      <= S_WAIT;
                end

                S_WAIT: begin
                    if (cmd_done) begin
                        if (step == LAST_STEP) begin
                            state <= S_DONE;
                        end else begin
                            step  <= step + 7'd1;
                            state <= S_ISSUE;
                        end
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

endmodule
