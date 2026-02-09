// encaps_ctrl — ML-KEM-768 encapsulation micro-op sequencer
//
// Sequences 93 micro-ops to perform Encaps_inner():
//   Phase 0: CBD sampling (7 ops) — r[0..2], e1[0..2], e2
//   Phase 1: NTT(r) (9 ops) — forward NTT of r[0..2] in-place
//   Phase 2: A_hat^T * r_hat + e1 → u (54 ops) — matrix-vector multiply
//   Phase 3: t_hat^T * r_hat + e2 + m → v (19 ops) — inner product
//   Phase 4: Compress u (D=10) and v (D=4) (4 ops)
//
// Slot allocation (20 slots in kyber_top bank):
//   0-8:   A_hat[j*3+i] (row-major) — preloaded by host, consumed in Phase 2
//   9-11:  t_hat[0..2] — preloaded by host, consumed in Phase 3
//   12:    m (message polynomial) — preloaded by host
//   13-15: r / r_hat — CBD sampled, then NTT'd
//   16-18: e1[0..2] — CBD sampled, then consumed as noise
//   19:    e2 — CBD sampled, consumed as noise
//
// After completion:
//   0-2:   u[0..2] (uncompressed)
//   9:     v (uncompressed)
//   16-18: compressed u[0..2] (D=10)
//   19:    compressed v (D=4)
//
// Interface: Issues one micro-op at a time via cmd_* outputs, waits for
// cmd_done feedback from kyber_top, then advances to the next op.

module encaps_ctrl (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,       // begin encapsulation
    output reg         done,        // pulse when complete
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
    reg [6:0] step;    // 0..92 (93 total micro-ops)

    assign busy = (state != S_IDLE);

    // ─── Micro-op decode: step → (op, slot_a, slot_b, param) ────────
    //
    // Pure combinational lookup. Each step maps to exactly one micro-op.
    // This is essentially a 93-entry instruction ROM.

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
            // ═══ Phase 0: CBD sampling (7 ops) ═══
            // r[0..2] → slots 13-15
            7'd0:  begin dec_op = OP_CBD_SAMPLE; dec_slot_a = 5'd13; end
            7'd1:  begin dec_op = OP_CBD_SAMPLE; dec_slot_a = 5'd14; end
            7'd2:  begin dec_op = OP_CBD_SAMPLE; dec_slot_a = 5'd15; end
            // e1[0..2] → slots 16-18
            7'd3:  begin dec_op = OP_CBD_SAMPLE; dec_slot_a = 5'd16; end
            7'd4:  begin dec_op = OP_CBD_SAMPLE; dec_slot_a = 5'd17; end
            7'd5:  begin dec_op = OP_CBD_SAMPLE; dec_slot_a = 5'd18; end
            // e2 → slot 19
            7'd6:  begin dec_op = OP_CBD_SAMPLE; dec_slot_a = 5'd19; end

            // ═══ Phase 1: NTT(r) (9 ops) — 3 polys × 3 ops each ═══
            // NTT(r[0]) in-place at slot 13
            7'd7:  begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd13; end
            7'd8:  begin dec_op = OP_RUN_NTT;        dec_param = 4'd0;   end  // forward
            7'd9:  begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd13; end
            // NTT(r[1]) in-place at slot 14
            7'd10: begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd14; end
            7'd11: begin dec_op = OP_RUN_NTT;        dec_param = 4'd0;   end
            7'd12: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd14; end
            // NTT(r[2]) in-place at slot 15
            7'd13: begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd15; end
            7'd14: begin dec_op = OP_RUN_NTT;        dec_param = 4'd0;   end
            7'd15: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd15; end

            // ═══ Phase 2: A_hat^T * r_hat + e1 → u (54 ops) ═══
            // Column i=0: 18 ops (steps 16..33)
            //   j=0: acc = A_hat[0][0] * r_hat[0]  (slot 0 × slot 13 → slot 0)
            7'd16: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd0;  end
            7'd17: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd13; end
            7'd18: begin dec_op = OP_RUN_BASEMUL;   end
            7'd19: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd0;  end
            //   j=1: temp = A_hat[1][0] * r_hat[1]  (slot 3 × slot 14 → slot 3)
            7'd20: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd3;  end
            7'd21: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd14; end
            7'd22: begin dec_op = OP_RUN_BASEMUL;   end
            7'd23: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd3;  end
            7'd24: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd0;  dec_slot_b = 5'd3; end
            //   j=2: temp = A_hat[2][0] * r_hat[2]  (slot 6 × slot 15 → slot 6)
            7'd25: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd6;  end
            7'd26: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd15; end
            7'd27: begin dec_op = OP_RUN_BASEMUL;   end
            7'd28: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd6;  end
            7'd29: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd0;  dec_slot_b = 5'd6; end
            //   INTT(acc)
            7'd30: begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd0;  end
            7'd31: begin dec_op = OP_RUN_NTT;        dec_param = 4'd1;   end  // inverse
            7'd32: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd0;  end
            //   u[0] = INTT(sum) + e1[0]
            7'd33: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd0;  dec_slot_b = 5'd16; end

            // Column i=1: 18 ops (steps 34..51)
            //   j=0: acc = A_hat[0][1] * r_hat[0]  (slot 1 × slot 13 → slot 1)
            7'd34: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd1;  end
            7'd35: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd13; end
            7'd36: begin dec_op = OP_RUN_BASEMUL;   end
            7'd37: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd1;  end
            //   j=1: temp = A_hat[1][1] * r_hat[1]  (slot 4 × slot 14 → slot 4)
            7'd38: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd4;  end
            7'd39: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd14; end
            7'd40: begin dec_op = OP_RUN_BASEMUL;   end
            7'd41: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd4;  end
            7'd42: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd1;  dec_slot_b = 5'd4; end
            //   j=2: temp = A_hat[2][1] * r_hat[2]  (slot 7 × slot 15 → slot 7)
            7'd43: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd7;  end
            7'd44: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd15; end
            7'd45: begin dec_op = OP_RUN_BASEMUL;   end
            7'd46: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd7;  end
            7'd47: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd1;  dec_slot_b = 5'd7; end
            //   INTT(acc)
            7'd48: begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd1;  end
            7'd49: begin dec_op = OP_RUN_NTT;        dec_param = 4'd1;   end
            7'd50: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd1;  end
            //   u[1] = INTT(sum) + e1[1]
            7'd51: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd1;  dec_slot_b = 5'd17; end

            // Column i=2: 18 ops (steps 52..69)
            //   j=0: acc = A_hat[0][2] * r_hat[0]  (slot 2 × slot 13 → slot 2)
            7'd52: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd2;  end
            7'd53: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd13; end
            7'd54: begin dec_op = OP_RUN_BASEMUL;   end
            7'd55: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd2;  end
            //   j=1: temp = A_hat[1][2] * r_hat[1]  (slot 5 × slot 14 → slot 5)
            7'd56: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd5;  end
            7'd57: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd14; end
            7'd58: begin dec_op = OP_RUN_BASEMUL;   end
            7'd59: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd5;  end
            7'd60: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd2;  dec_slot_b = 5'd5; end
            //   j=2: temp = A_hat[2][2] * r_hat[2]  (slot 8 × slot 15 → slot 8)
            7'd61: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd8;  end
            7'd62: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd15; end
            7'd63: begin dec_op = OP_RUN_BASEMUL;   end
            7'd64: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd8;  end
            7'd65: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd2;  dec_slot_b = 5'd8; end
            //   INTT(acc)
            7'd66: begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd2;  end
            7'd67: begin dec_op = OP_RUN_NTT;        dec_param = 4'd1;   end
            7'd68: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd2;  end
            //   u[2] = INTT(sum) + e1[2]
            7'd69: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd2;  dec_slot_b = 5'd18; end

            // ═══ Phase 3: t_hat^T * r_hat + e2 + m → v (19 ops) ═══
            //   j=0: acc = t_hat[0] * r_hat[0]  (slot 9 × slot 13 → slot 9)
            7'd70: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd9;  end
            7'd71: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd13; end
            7'd72: begin dec_op = OP_RUN_BASEMUL;   end
            7'd73: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd9;  end
            //   j=1: temp = t_hat[1] * r_hat[1]  (slot 10 × slot 14 → slot 10)
            7'd74: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd10; end
            7'd75: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd14; end
            7'd76: begin dec_op = OP_RUN_BASEMUL;   end
            7'd77: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd10; end
            7'd78: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd9;  dec_slot_b = 5'd10; end
            //   j=2: temp = t_hat[2] * r_hat[2]  (slot 11 × slot 15 → slot 11)
            7'd79: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd11; end
            7'd80: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd15; end
            7'd81: begin dec_op = OP_RUN_BASEMUL;   end
            7'd82: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd11; end
            7'd83: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd9;  dec_slot_b = 5'd11; end
            //   INTT(acc)
            7'd84: begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd9;  end
            7'd85: begin dec_op = OP_RUN_NTT;        dec_param = 4'd1;   end
            7'd86: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd9;  end
            //   v = INTT(sum) + e2 + m
            7'd87: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd9;  dec_slot_b = 5'd19; end
            7'd88: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd9;  dec_slot_b = 5'd12; end

            // ═══ Phase 4: Compress (4 ops) ═══
            // u[0..2] → slots 16-18 (D=10), v → slot 19 (D=4)
            7'd89: begin dec_op = OP_COMPRESS; dec_slot_a = 5'd0;  dec_slot_b = 5'd16; dec_param = 4'd10; end
            7'd90: begin dec_op = OP_COMPRESS; dec_slot_a = 5'd1;  dec_slot_b = 5'd17; dec_param = 4'd10; end
            7'd91: begin dec_op = OP_COMPRESS; dec_slot_a = 5'd2;  dec_slot_b = 5'd18; dec_param = 4'd10; end
            7'd92: begin dec_op = OP_COMPRESS; dec_slot_a = 5'd9;  dec_slot_b = 5'd19; dec_param = 4'd4;  end

            default: begin
                dec_op = OP_NOP;
            end
        endcase
    end

    // ─── FSM ────────────────────────────────────────────────────────
    localparam LAST_STEP = 7'd92;

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
                    // Present decoded micro-op and pulse cmd_start
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
