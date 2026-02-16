// auto_keygen_ctrl — Autonomous ML-KEM-768 KeyGen sequencer with Keccak
//
// Performs full K-PKE.KeyGen (FIPS 203 Algorithm 13) autonomously:
//   Phase 1: SEED_ABSORB — SHA3-512(d || 0x03) via Keccak absorb
//   Phase 2: G_SQUEEZE   — Squeeze 64 bytes -> rho[32], sigma[32]
//   Phase 3: EXPAND_A    — 9x SampleNTT via SHAKE-128(rho || j || i)
//   Phase 4: PRF_CBD     — 6x SHAKE-256(sigma || nonce) -> CBD sampling
//   Phase 5: POLY_OPS    — NTT + matmul (63 micro-ops, keygen_ctrl steps 6-68)
//   Phase 6: DONE
//
// Host provides only 32 bytes of seed d. Hardware does all hashing.
//
// Slot allocation (same as keygen_ctrl):
//   0-8:   A_hat[i*3+j] (row-major, written during EXPAND_A)
//   9-11:  s -> s_hat (CBD sampled, NTT'd)
//   12-14: e -> e_hat (CBD sampled, NTT'd)
//   After: t_hat[0..2] in slots 0,3,6; s_hat in 9-11

module auto_keygen_ctrl (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    output reg         done,
    output wire        busy,

    // Seed input (32 bytes streamed)
    input  wire        seed_valid,
    input  wire [7:0]  seed_data,
    output wire        seed_ready,

    // Keccak sponge interface
    output reg  [1:0]  keccak_mode,
    output reg         keccak_start,
    output reg         absorb_valid,
    output reg  [7:0]  absorb_data,
    output reg         absorb_last,
    input  wire        absorb_ready,
    input  wire [7:0]  squeeze_data,
    input  wire        squeeze_valid,
    output reg         squeeze_ready,

    // Kyber_top host port (for EXPAND_A writes)
    output reg         kt_host_we,
    output reg  [4:0]  kt_host_slot,
    output reg  [7:0]  kt_host_addr,
    output reg  [11:0] kt_host_din,

    // Kyber_top command interface (for PRF_CBD + POLY_OPS)
    output reg  [3:0]  cmd_op,
    output reg  [4:0]  cmd_slot_a,
    output reg  [4:0]  cmd_slot_b,
    output reg  [3:0]  cmd_param,
    output reg         cmd_start,
    input  wire        cmd_done,

    // CBD byte bridge control
    output reg         cbd_bridge_en,

    // rho register readback
    output wire [255:0] rho_reg_out
);

    // ─── Opcodes (must match kyber_top.v) ────────────────────────
    localparam OP_NOP           = 4'd0;
    localparam OP_COPY_TO_NTT   = 4'd1;
    localparam OP_COPY_FROM_NTT = 4'd2;
    localparam OP_RUN_NTT       = 4'd3;
    localparam OP_COPY_TO_BM_A  = 4'd4;
    localparam OP_COPY_TO_BM_B  = 4'd5;
    localparam OP_COPY_FROM_BM  = 4'd6;
    localparam OP_RUN_BASEMUL   = 4'd7;
    localparam OP_POLY_ADD      = 4'd8;
    localparam OP_CBD_SAMPLE    = 4'd12;

    // ─── Top-level FSM phases ────────────────────────────────────
    localparam PH_IDLE        = 4'd0;
    localparam PH_SEED_ABSORB = 4'd1;
    localparam PH_G_SQUEEZE   = 4'd2;
    localparam PH_EXPAND_A    = 4'd3;
    localparam PH_PRF_CBD     = 4'd4;
    localparam PH_POLY_OPS    = 4'd5;
    localparam PH_DONE        = 4'd6;

    // ─── EXPAND_A sub-states ─────────────────────────────────────
    localparam XOF_INIT       = 3'd0;
    localparam XOF_ABSORB     = 3'd1;
    localparam XOF_B0         = 3'd2;
    localparam XOF_B1         = 3'd3;
    localparam XOF_B2         = 3'd4;
    localparam XOF_WRITE_D1   = 3'd5;
    localparam XOF_WRITE_D2   = 3'd6;

    // ─── PRF_CBD sub-states ──────────────────────────────────────
    localparam PRF_INIT       = 2'd0;
    localparam PRF_ABSORB     = 2'd1;
    localparam PRF_BRIDGE     = 2'd2;
    localparam PRF_WAIT       = 2'd3;

    // ─── POLY_OPS sub-states ─────────────────────────────────────
    localparam PO_ISSUE       = 1'd0;
    localparam PO_WAIT        = 1'd1;

    // ─── Registers ───────────────────────────────────────────────
    reg [3:0]   phase;
    reg [255:0] rho;
    reg [255:0] sigma;
    reg [7:0]   byte_cnt;

    // EXPAND_A
    reg [2:0]   xof_state;
    reg [3:0]   expand_idx;    // 0..8
    reg [8:0]   coeff_count;   // 0..256 (9 bits to avoid overflow)
    reg [7:0]   sample_b0;
    reg [7:0]   sample_b1;
    reg [7:0]   sample_b2;

    // PRF_CBD
    reg [1:0]   prf_state;
    reg [2:0]   prf_nonce;     // 0..5

    // Squeeze handshake: registered squeeze_ready means Keccak advances
    // byte_idx one cycle after assertion. squeeze_ack tracks this:
    // capture data when ack=0, skip (wait for advance) when ack=1.
    reg         squeeze_ack;

    // POLY_OPS
    reg         po_state;
    reg [6:0]   po_step;       // 0..62

    // ─── Derived signals ─────────────────────────────────────────
    wire [11:0] d1 = {sample_b1[3:0], sample_b0};
    wire [11:0] d2 = {sample_b2, sample_b1[7:4]};

    assign busy = (phase != PH_IDLE);
    assign seed_ready = (phase == PH_SEED_ABSORB) && absorb_ready
                        && (byte_cnt < 8'd32);
    assign rho_reg_out = rho;

    // ─── Expand_A index decode ───────────────────────────────────
    reg [1:0] expand_i;
    reg [1:0] expand_j;
    always @(*) begin
        case (expand_idx)
            4'd0: begin expand_i = 2'd0; expand_j = 2'd0; end
            4'd1: begin expand_i = 2'd0; expand_j = 2'd1; end
            4'd2: begin expand_i = 2'd0; expand_j = 2'd2; end
            4'd3: begin expand_i = 2'd1; expand_j = 2'd0; end
            4'd4: begin expand_i = 2'd1; expand_j = 2'd1; end
            4'd5: begin expand_i = 2'd1; expand_j = 2'd2; end
            4'd6: begin expand_i = 2'd2; expand_j = 2'd0; end
            4'd7: begin expand_i = 2'd2; expand_j = 2'd1; end
            4'd8: begin expand_i = 2'd2; expand_j = 2'd2; end
            default: begin expand_i = 2'd0; expand_j = 2'd0; end
        endcase
    end

    wire [4:0] expand_slot = {1'b0, expand_idx[3:0]};

    // PRF target slots: nonce 0-2 -> slots 9-11, nonce 3-5 -> 12-14
    wire [4:0] prf_target_slot = 5'd9 + {2'd0, prf_nonce};

    // ─── POLY_OPS micro-op decode ────────────────────────────────
    // Replicates keygen_ctrl steps 6-68 (63 ops, indexed 0..62)
    reg [3:0]  dec_op;
    reg [4:0]  dec_slot_a;
    reg [4:0]  dec_slot_b;
    reg [3:0]  dec_param;

    always @(*) begin
        dec_op     = OP_NOP;
        dec_slot_a = 5'd0;
        dec_slot_b = 5'd0;
        dec_param  = 4'd0;

        case (po_step)
            // ═══ NTT (18 ops: 6 polys × 3 each) ═══
            7'd0:  begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd9;  end
            7'd1:  begin dec_op = OP_RUN_NTT;        dec_param  = 4'd0;  end
            7'd2:  begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd9;  end
            7'd3:  begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd10; end
            7'd4:  begin dec_op = OP_RUN_NTT;        dec_param  = 4'd0;  end
            7'd5:  begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd10; end
            7'd6:  begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd11; end
            7'd7:  begin dec_op = OP_RUN_NTT;        dec_param  = 4'd0;  end
            7'd8:  begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd11; end
            7'd9:  begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd12; end
            7'd10: begin dec_op = OP_RUN_NTT;        dec_param  = 4'd0;  end
            7'd11: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd12; end
            7'd12: begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd13; end
            7'd13: begin dec_op = OP_RUN_NTT;        dec_param  = 4'd0;  end
            7'd14: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd13; end
            7'd15: begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd14; end
            7'd16: begin dec_op = OP_RUN_NTT;        dec_param  = 4'd0;  end
            7'd17: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd14; end
            // ═══ Matmul row 0 (15 ops) ═══
            7'd18: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd0;  end
            7'd19: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd9;  end
            7'd20: begin dec_op = OP_RUN_BASEMUL;   end
            7'd21: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd0;  end
            7'd22: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd1;  end
            7'd23: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd10; end
            7'd24: begin dec_op = OP_RUN_BASEMUL;   end
            7'd25: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd1;  end
            7'd26: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd0;  dec_slot_b = 5'd1; end
            7'd27: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd2;  end
            7'd28: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd11; end
            7'd29: begin dec_op = OP_RUN_BASEMUL;   end
            7'd30: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd2;  end
            7'd31: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd0;  dec_slot_b = 5'd2; end
            7'd32: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd0;  dec_slot_b = 5'd12; end
            // ═══ Matmul row 1 (15 ops) ═══
            7'd33: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd3;  end
            7'd34: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd9;  end
            7'd35: begin dec_op = OP_RUN_BASEMUL;   end
            7'd36: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd3;  end
            7'd37: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd4;  end
            7'd38: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd10; end
            7'd39: begin dec_op = OP_RUN_BASEMUL;   end
            7'd40: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd4;  end
            7'd41: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd3;  dec_slot_b = 5'd4; end
            7'd42: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd5;  end
            7'd43: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd11; end
            7'd44: begin dec_op = OP_RUN_BASEMUL;   end
            7'd45: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd5;  end
            7'd46: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd3;  dec_slot_b = 5'd5; end
            7'd47: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd3;  dec_slot_b = 5'd13; end
            // ═══ Matmul row 2 (15 ops) ═══
            7'd48: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd6;  end
            7'd49: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd9;  end
            7'd50: begin dec_op = OP_RUN_BASEMUL;   end
            7'd51: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd6;  end
            7'd52: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd7;  end
            7'd53: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd10; end
            7'd54: begin dec_op = OP_RUN_BASEMUL;   end
            7'd55: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd7;  end
            7'd56: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd6;  dec_slot_b = 5'd7; end
            7'd57: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd8;  end
            7'd58: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd11; end
            7'd59: begin dec_op = OP_RUN_BASEMUL;   end
            7'd60: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd8;  end
            7'd61: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd6;  dec_slot_b = 5'd8; end
            7'd62: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd6;  dec_slot_b = 5'd14; end
            default: dec_op = OP_NOP;
        endcase
    end

    localparam PO_LAST_STEP = 7'd62;

    // ─── Helper: advance to next EXPAND_A element or PRF_CBD ─────
    // (used by XOF_WRITE_D1 and XOF_WRITE_D2 when coeff_count reaches 256)
    // Encoded as tasks in the sequential block below.

    // ─── Main FSM ────────────────────────────────────────────────
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase         <= PH_IDLE;
            done          <= 1'b0;
            rho           <= 256'd0;
            sigma         <= 256'd0;
            byte_cnt      <= 8'd0;
            xof_state     <= XOF_INIT;
            expand_idx    <= 4'd0;
            coeff_count   <= 9'd0;
            sample_b0     <= 8'd0;
            sample_b1     <= 8'd0;
            sample_b2     <= 8'd0;
            squeeze_ack   <= 1'b0;
            prf_state     <= PRF_INIT;
            prf_nonce     <= 3'd0;
            po_state      <= PO_ISSUE;
            po_step       <= 7'd0;
            keccak_mode   <= 2'd0;
            keccak_start  <= 1'b0;
            absorb_valid  <= 1'b0;
            absorb_data   <= 8'd0;
            absorb_last   <= 1'b0;
            squeeze_ready <= 1'b0;
            kt_host_we    <= 1'b0;
            kt_host_slot  <= 5'd0;
            kt_host_addr  <= 8'd0;
            kt_host_din   <= 12'd0;
            cmd_op        <= 4'd0;
            cmd_slot_a    <= 5'd0;
            cmd_slot_b    <= 5'd0;
            cmd_param     <= 4'd0;
            cmd_start     <= 1'b0;
            cbd_bridge_en <= 1'b0;
        end else begin
            // Defaults: single-cycle pulses
            done          <= 1'b0;
            keccak_start  <= 1'b0;
            cmd_start     <= 1'b0;
            kt_host_we    <= 1'b0;
            absorb_valid  <= 1'b0;
            absorb_last   <= 1'b0;
            squeeze_ready <= 1'b0;

            case (phase)

            // ═════════════════════════════════════════════════════════
            PH_IDLE: begin
                if (start) begin
                    phase        <= PH_SEED_ABSORB;
                    byte_cnt     <= 8'd0;
                    keccak_mode  <= 2'd1;   // SHA3-512
                    keccak_start <= 1'b1;
                end
            end

            // ═════════════════════════════════════════════════════════
            // SEED_ABSORB — 32 seed bytes + 0x03 to SHA3-512
            // ═════════════════════════════════════════════════════════
            PH_SEED_ABSORB: begin
                if (byte_cnt < 8'd32) begin
                    // Pass seed bytes to Keccak via valid/ready handshake
                    if (seed_valid && absorb_ready) begin
                        absorb_valid <= 1'b1;
                        absorb_data  <= seed_data;
                        byte_cnt     <= byte_cnt + 8'd1;
                    end
                end else if (byte_cnt == 8'd32) begin
                    // Append K=3 byte with absorb_last
                    if (absorb_ready) begin
                        absorb_valid <= 1'b1;
                        absorb_data  <= 8'd3;
                        absorb_last  <= 1'b1;
                        byte_cnt     <= byte_cnt + 8'd1;
                    end
                end else begin
                    // All 33 bytes absorbed; move to squeeze
                    phase    <= PH_G_SQUEEZE;
                    byte_cnt <= 8'd0;
                end
            end

            // ═════════════════════════════════════════════════════════
            // G_SQUEEZE — 64 bytes: rho[0:31] then sigma[0:31]
            // Two-cycle per byte: capture when ack=0, wait when ack=1
            // ═════════════════════════════════════════════════════════
            PH_G_SQUEEZE: begin
                if (squeeze_valid && !squeeze_ack) begin
                    // Capture byte and request Keccak to advance
                    squeeze_ready <= 1'b1;
                    squeeze_ack   <= 1'b1;
                    if (byte_cnt < 8'd32)
                        rho[byte_cnt*8 +: 8] <= squeeze_data;
                    else
                        sigma[(byte_cnt - 8'd32)*8 +: 8] <= squeeze_data;
                    byte_cnt <= byte_cnt + 8'd1;
                end else if (squeeze_ack) begin
                    // Wait cycle: Keccak advances byte_idx
                    squeeze_ack <= 1'b0;
                    if (byte_cnt == 8'd64) begin
                        phase      <= PH_EXPAND_A;
                        expand_idx <= 4'd0;
                        xof_state  <= XOF_INIT;
                    end
                end
            end

            // ═════════════════════════════════════════════════════════
            // EXPAND_A — 9x SampleNTT via SHAKE-128(rho || j || i)
            // ═════════════════════════════════════════════════════════
            PH_EXPAND_A: begin
                case (xof_state)

                // --- Start new SHAKE-128 for this (i,j) pair ---
                XOF_INIT: begin
                    keccak_mode  <= 2'd2;   // SHAKE-128
                    keccak_start <= 1'b1;
                    byte_cnt     <= 8'd0;
                    coeff_count  <= 9'd0;
                    xof_state    <= XOF_ABSORB;
                end

                // --- Absorb rho[0..31] || j || i (34 bytes) ---
                XOF_ABSORB: begin
                    if (absorb_ready) begin
                        absorb_valid <= 1'b1;
                        if (byte_cnt < 8'd32) begin
                            absorb_data <= rho[byte_cnt*8 +: 8];
                        end else if (byte_cnt == 8'd32) begin
                            absorb_data <= {6'd0, expand_j};
                        end else begin
                            absorb_data <= {6'd0, expand_i};
                            absorb_last <= 1'b1;
                        end

                        if (byte_cnt == 8'd33) begin
                            xof_state <= XOF_B0;
                        end else begin
                            byte_cnt <= byte_cnt + 8'd1;
                        end
                    end
                end

                // --- Squeeze 3 bytes for rejection sampling ---
                // Two-cycle per byte: capture when ack=0, wait when ack=1
                XOF_B0: begin
                    if (squeeze_valid && !squeeze_ack) begin
                        squeeze_ready <= 1'b1;
                        squeeze_ack   <= 1'b1;
                        sample_b0     <= squeeze_data;
                    end else if (squeeze_ack) begin
                        squeeze_ack <= 1'b0;
                        xof_state   <= XOF_B1;
                    end
                end

                XOF_B1: begin
                    if (squeeze_valid && !squeeze_ack) begin
                        squeeze_ready <= 1'b1;
                        squeeze_ack   <= 1'b1;
                        sample_b1     <= squeeze_data;
                    end else if (squeeze_ack) begin
                        squeeze_ack <= 1'b0;
                        xof_state   <= XOF_B2;
                    end
                end

                XOF_B2: begin
                    if (squeeze_valid && !squeeze_ack) begin
                        squeeze_ready <= 1'b1;
                        squeeze_ack   <= 1'b1;
                        sample_b2     <= squeeze_data;
                    end else if (squeeze_ack) begin
                        squeeze_ack   <= 1'b0;
                        xof_state     <= XOF_WRITE_D1;
                    end
                end

                // --- Evaluate d1 = {b1[3:0], b0} ---
                XOF_WRITE_D1: begin
                    if (d1 < 12'd3329 && coeff_count < 9'd256) begin
                        kt_host_we   <= 1'b1;
                        kt_host_slot <= expand_slot;
                        kt_host_addr <= coeff_count[7:0];
                        kt_host_din  <= d1;
                        coeff_count  <= coeff_count + 9'd1;
                        // If this was the 256th coefficient, done
                        if (coeff_count == 9'd255) begin
                            if (expand_idx == 4'd8) begin
                                phase     <= PH_PRF_CBD;
                                prf_state <= PRF_INIT;
                                prf_nonce <= 3'd0;
                                byte_cnt  <= 8'd0;
                            end else begin
                                expand_idx <= expand_idx + 4'd1;
                                xof_state  <= XOF_INIT;
                            end
                        end else begin
                            xof_state <= XOF_WRITE_D2;
                        end
                    end else begin
                        // d1 rejected or count already 256
                        xof_state <= XOF_WRITE_D2;
                    end
                end

                // --- Evaluate d2 = {b2, b1[7:4]} ---
                XOF_WRITE_D2: begin
                    if (d2 < 12'd3329 && coeff_count < 9'd256) begin
                        kt_host_we   <= 1'b1;
                        kt_host_slot <= expand_slot;
                        kt_host_addr <= coeff_count[7:0];
                        kt_host_din  <= d2;
                        coeff_count  <= coeff_count + 9'd1;
                        if (coeff_count == 9'd255) begin
                            if (expand_idx == 4'd8) begin
                                phase     <= PH_PRF_CBD;
                                prf_state <= PRF_INIT;
                                prf_nonce <= 3'd0;
                                byte_cnt  <= 8'd0;
                            end else begin
                                expand_idx <= expand_idx + 4'd1;
                                xof_state  <= XOF_INIT;
                            end
                        end else begin
                            xof_state <= XOF_B0;
                        end
                    end else begin
                        xof_state <= XOF_B0;
                    end
                end

                default: xof_state <= XOF_INIT;
                endcase
            end

            // ═════════════════════════════════════════════════════════
            // PRF_CBD — 6x SHAKE-256(sigma || nonce) -> CBD sampling
            // ═════════════════════════════════════════════════════════
            PH_PRF_CBD: begin
                case (prf_state)

                // Start new SHAKE-256 for this nonce
                PRF_INIT: begin
                    keccak_mode  <= 2'd3;   // SHAKE-256
                    keccak_start <= 1'b1;
                    byte_cnt     <= 8'd0;
                    prf_state    <= PRF_ABSORB;
                end

                // Absorb sigma[0..31] || nonce (33 bytes)
                PRF_ABSORB: begin
                    if (absorb_ready) begin
                        absorb_valid <= 1'b1;
                        if (byte_cnt < 8'd32) begin
                            absorb_data <= sigma[byte_cnt*8 +: 8];
                        end else begin
                            absorb_data <= {5'd0, prf_nonce};
                            absorb_last <= 1'b1;
                        end

                        if (byte_cnt == 8'd32) begin
                            prf_state     <= PRF_BRIDGE;
                            cbd_bridge_en <= 1'b1;
                        end else begin
                            byte_cnt <= byte_cnt + 8'd1;
                        end
                    end
                end

                // Issue OP_CBD_SAMPLE to kyber_top
                PRF_BRIDGE: begin
                    cmd_op     <= OP_CBD_SAMPLE;
                    cmd_slot_a <= prf_target_slot;
                    cmd_slot_b <= 5'd0;
                    cmd_param  <= 4'd0;
                    cmd_start  <= 1'b1;
                    prf_state  <= PRF_WAIT;
                end

                // Wait for CBD sampling + copy to complete
                PRF_WAIT: begin
                    if (cmd_done) begin
                        cbd_bridge_en <= 1'b0;
                        if (prf_nonce == 3'd5) begin
                            phase    <= PH_POLY_OPS;
                            po_state <= PO_ISSUE;
                            po_step  <= 7'd0;
                        end else begin
                            prf_nonce <= prf_nonce + 3'd1;
                            prf_state <= PRF_INIT;
                        end
                    end
                end

                endcase
            end

            // ═════════════════════════════════════════════════════════
            // POLY_OPS — 63 micro-ops (NTT + matmul)
            // ═════════════════════════════════════════════════════════
            PH_POLY_OPS: begin
                case (po_state)

                PO_ISSUE: begin
                    cmd_op     <= dec_op;
                    cmd_slot_a <= dec_slot_a;
                    cmd_slot_b <= dec_slot_b;
                    cmd_param  <= dec_param;
                    cmd_start  <= 1'b1;
                    po_state   <= PO_WAIT;
                end

                PO_WAIT: begin
                    if (cmd_done) begin
                        if (po_step == PO_LAST_STEP)
                            phase <= PH_DONE;
                        else begin
                            po_step  <= po_step + 7'd1;
                            po_state <= PO_ISSUE;
                        end
                    end
                end

                endcase
            end

            // ═════════════════════════════════════════════════════════
            PH_DONE: begin
                done  <= 1'b1;
                phase <= PH_IDLE;
            end

            default: phase <= PH_IDLE;
            endcase
        end
    end

endmodule
