// decaps_ctrl — ML-KEM-768 decryption (K-PKE.Decrypt) micro-op sequencer
//
// Sequences 32 micro-ops to perform Decrypt:
//   Phase 0: Decompress (4 ops) — u[0..2] (D=10), v (D=4)
//   Phase 1: NTT(u) (9 ops) — forward NTT of u[0..2] in-place
//   Phase 2: Inner product (14 ops) — s_hat^T · u_hat → accumulator
//   Phase 3: INTT + message recovery (5 ops) — INTT, subtract, compress D=1
//
// Slot allocation (20 slots in kyber_top bank):
//   0-2:   compressed u[0..2] (preloaded by host, D=10 values)
//   3:     compressed v (preloaded by host, D=4 values)
//   4:     m' output (compress D=1 result)
//   9-11:  s_hat (preloaded by host, read-only)
//
// After completion:
//   0:     inner product accumulator (intermediate, overwritten)
//   3:     v - w (intermediate)
//   4:     m' (recovered message, each coeff 0 or 1)
//   9-11:  s_hat (preserved)

module decaps_ctrl (
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
    reg [5:0] step;    // 0..31 (32 total micro-ops)

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
            // ═══ Phase 0: Decompress (4 ops) ═══
            // Decompress u[0..2] in-place (D=10): slot_a=src, slot_b=dst
            6'd0:  begin dec_op = OP_DECOMPRESS; dec_slot_a = 5'd0; dec_slot_b = 5'd0; dec_param = 4'd10; end
            6'd1:  begin dec_op = OP_DECOMPRESS; dec_slot_a = 5'd1; dec_slot_b = 5'd1; dec_param = 4'd10; end
            6'd2:  begin dec_op = OP_DECOMPRESS; dec_slot_a = 5'd2; dec_slot_b = 5'd2; dec_param = 4'd10; end
            // Decompress v in-place (D=4)
            6'd3:  begin dec_op = OP_DECOMPRESS; dec_slot_a = 5'd3; dec_slot_b = 5'd3; dec_param = 4'd4;  end

            // ═══ Phase 1: NTT(u) (9 ops) — 3 polys × 3 ops each ═══
            // NTT(u[0]) in-place at slot 0
            6'd4:  begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd0; end
            6'd5:  begin dec_op = OP_RUN_NTT;        dec_param  = 4'd0; end  // forward
            6'd6:  begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd0; end
            // NTT(u[1]) in-place at slot 1
            6'd7:  begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd1; end
            6'd8:  begin dec_op = OP_RUN_NTT;        dec_param  = 4'd0; end
            6'd9:  begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd1; end
            // NTT(u[2]) in-place at slot 2
            6'd10: begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd2; end
            6'd11: begin dec_op = OP_RUN_NTT;        dec_param  = 4'd0; end
            6'd12: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd2; end

            // ═══ Phase 2: Inner product s_hat^T · u_hat (14 ops) ═══
            // s_hat[0]=slot9 * u_hat[0]=slot0 → acc in slot 0
            6'd13: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd9;  end
            6'd14: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd0;  end
            6'd15: begin dec_op = OP_RUN_BASEMUL;   end
            6'd16: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd0;  end
            // s_hat[1]=slot10 * u_hat[1]=slot1 → temp in slot 1
            6'd17: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd10; end
            6'd18: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd1;  end
            6'd19: begin dec_op = OP_RUN_BASEMUL;   end
            6'd20: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd1;  end
            6'd21: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd0;  dec_slot_b = 5'd1; end
            // s_hat[2]=slot11 * u_hat[2]=slot2 → temp in slot 2
            6'd22: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd11; end
            6'd23: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd2;  end
            6'd24: begin dec_op = OP_RUN_BASEMUL;   end
            6'd25: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd2;  end
            6'd26: begin dec_op = OP_POLY_ADD;       dec_slot_a = 5'd0;  dec_slot_b = 5'd2; end

            // ═══ Phase 3: INTT + message recovery (5 ops) ═══
            // INTT of inner product accumulator (slot 0)
            6'd27: begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd0; end
            6'd28: begin dec_op = OP_RUN_NTT;        dec_param  = 4'd1; end  // inverse
            6'd29: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd0; end
            // v - w: slot 3 = slot 3 - slot 0
            6'd30: begin dec_op = OP_POLY_SUB;       dec_slot_a = 5'd3;  dec_slot_b = 5'd0; end
            // Compress D=1: slot 3 → slot 4
            6'd31: begin dec_op = OP_COMPRESS;       dec_slot_a = 5'd3;  dec_slot_b = 5'd4; dec_param = 4'd1; end

            default: begin
                dec_op = OP_NOP;
            end
        endcase
    end

    // ─── FSM ────────────────────────────────────────────────────────
    localparam LAST_STEP = 6'd31;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state     <= S_IDLE;
            step      <= 6'd0;
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
                        step  <= 6'd0;
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
                            step  <= step + 6'd1;
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
