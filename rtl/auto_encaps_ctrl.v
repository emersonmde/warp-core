// auto_encaps_ctrl — Autonomous ML-KEM-768 Encaps sequencer with Keccak
//
// Performs full ML-KEM.Encaps_internal (FIPS 203 Algorithm 17) autonomously:
//   Phase 1: INPUT_M    — Accept 32 bytes of message m
//   Phase 2: INPUT_EK   — Accept 1184 bytes of ek, absorb to SHA3-256,
//                          parse ByteDecode(12) for t_hat → slots 9-11,
//                          extract rho (32 bytes)
//   Phase 3: H_SQUEEZE  — Squeeze 32 bytes → h = H(ek)
//   Phase 4: G_ABSORB   — Absorb m || h to SHA3-512
//   Phase 5: G_SQUEEZE  — Squeeze 64 bytes → K (shared secret) || r
//   Phase 6: EXPAND_A   — 9x SampleNTT via SHAKE-128(rho || j || i)
//   Phase 7: LOAD_M     — Write Decompress(1, m) to slot 12
//   Phase 8: PRF_CBD    — 7x SHAKE-256(r || nonce) → CBD sampling
//   Phase 9: POLY_OPS   — 86 micro-ops (encaps_ctrl steps 7-92)
//   Phase 10: DONE
//
// Host provides 32 bytes of m + 1184 bytes of ek. Hardware does all hashing.
//
// Slot allocation (same as encaps_ctrl):
//   0-8:   A_hat[i*3+j] (row-major, written during EXPAND_A)
//   9-11:  t_hat[0..2] (decoded from ek during INPUT_EK)
//   12:    mu (decompressed message, written during LOAD_M)
//   13-15: y / y_hat (CBD nonces 0-2)
//   16-18: e1[0..2] (CBD nonces 3-5)
//   19:    e2 (CBD nonce 6)
//
// After completion:
//   Compressed u[0..2] in slots 16-18 (D=10)
//   Compressed v in slot 19 (D=4)
//   K (shared secret) in k_reg (32 bytes, readable via k_reg_out)

module auto_encaps_ctrl (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    output reg         done,
    output wire        busy,

    // Data input: m (32 bytes) then ek (1184 bytes)
    input  wire        din_valid,
    input  wire [7:0]  din_data,
    output wire        din_ready,

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

    // Kyber_top host port (for EK writes, EXPAND_A writes, LOAD_M writes)
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

    // K register readback
    output wire [255:0] k_reg_out
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
    localparam OP_COMPRESS      = 4'd10;
    localparam OP_CBD_SAMPLE    = 4'd12;

    // ─── Top-level FSM phases ────────────────────────────────────
    localparam PH_IDLE       = 4'd0;
    localparam PH_INPUT_M    = 4'd1;
    localparam PH_INPUT_EK   = 4'd2;
    localparam PH_H_SQUEEZE  = 4'd3;
    localparam PH_G_ABSORB   = 4'd4;
    localparam PH_G_SQUEEZE  = 4'd5;
    localparam PH_EXPAND_A   = 4'd6;
    localparam PH_LOAD_M     = 4'd7;
    localparam PH_PRF_CBD    = 4'd8;
    localparam PH_POLY_OPS   = 4'd9;
    localparam PH_DONE       = 4'd10;

    // ─── INPUT_EK sub-states ─────────────────────────────────────
    localparam EK_BYTE      = 2'd0;
    localparam EK_WRITE_C0  = 2'd1;
    localparam EK_WRITE_C1  = 2'd2;

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
    reg [255:0] m_reg;        // 32 bytes of message
    reg [255:0] rho;          // 32 bytes from ek tail
    reg [255:0] h_reg;        // 32 bytes from H(ek)
    reg [255:0] k_reg;        // 32 bytes shared secret K
    reg [255:0] r_reg;        // 32 bytes encryption randomness r
    reg [10:0]  byte_cnt;     // general byte counter (up to 1184)

    // INPUT_EK parse state
    reg [1:0]   ek_sub;       // EK sub-state
    reg [1:0]   byte_in_group; // 0,1,2 within 3-byte ByteDecode(12) group
    reg [7:0]   ek_b0;        // first byte of group
    reg [7:0]   ek_b1;        // second byte of group
    reg [7:0]   ek_b2;        // third byte of group
    reg [1:0]   ek_poly_idx;  // which t_hat polynomial (0-2)
    reg [8:0]   ek_coeff_count; // coefficient count within polynomial

    // EXPAND_A
    reg [2:0]   xof_state;
    reg [3:0]   expand_idx;    // 0..8
    reg [8:0]   coeff_count;   // 0..256 (9 bits)
    reg [7:0]   sample_b0;
    reg [7:0]   sample_b1;
    reg [7:0]   sample_b2;

    // Squeeze handshake
    reg         squeeze_ack;

    // PRF_CBD
    reg [1:0]   prf_state;
    reg [2:0]   prf_nonce;     // 0..6

    // POLY_OPS
    reg         po_state;
    reg [6:0]   po_step;       // 0..85

    // ─── Derived signals ─────────────────────────────────────────
    // ByteDecode(12) coefficients from ek bytes
    wire [11:0] ek_c0 = {ek_b1[3:0], ek_b0};
    wire [11:0] ek_c1 = {ek_b2, ek_b1[7:4]};

    // SampleNTT candidate values
    wire [11:0] d1 = {sample_b1[3:0], sample_b0};
    wire [11:0] d2 = {sample_b2, sample_b1[7:4]};

    assign busy = (phase != PH_IDLE);
    assign din_ready = (phase == PH_INPUT_M) ? 1'b1 :
                       (phase == PH_INPUT_EK && ek_sub == EK_BYTE) ? absorb_ready :
                       1'b0;
    assign k_reg_out = k_reg;

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

    // PRF target slots: nonce 0-2 -> slots 13-15, 3-5 -> 16-18, 6 -> 19
    wire [4:0] prf_target_slot = 5'd13 + {2'd0, prf_nonce};

    // ─── POLY_OPS micro-op decode ────────────────────────────────
    // Replicates encaps_ctrl steps 7-92 (86 ops, indexed 0..85)
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
            // ═══ NTT(r[0..2]) — 9 ops ═══
            7'd0:  begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd13; end
            7'd1:  begin dec_op = OP_RUN_NTT;        dec_param  = 4'd0;  end
            7'd2:  begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd13; end
            7'd3:  begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd14; end
            7'd4:  begin dec_op = OP_RUN_NTT;        dec_param  = 4'd0;  end
            7'd5:  begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd14; end
            7'd6:  begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd15; end
            7'd7:  begin dec_op = OP_RUN_NTT;        dec_param  = 4'd0;  end
            7'd8:  begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd15; end
            // ═══ Column 0: A_hat^T[*][0] · r_hat + e1[0] → u[0] (18 ops) ═══
            7'd9:  begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd0;  end
            7'd10: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd13; end
            7'd11: begin dec_op = OP_RUN_BASEMUL;   end
            7'd12: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd0;  end
            7'd13: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd3;  end
            7'd14: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd14; end
            7'd15: begin dec_op = OP_RUN_BASEMUL;   end
            7'd16: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd3;  end
            7'd17: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd0;  dec_slot_b = 5'd3; end
            7'd18: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd6;  end
            7'd19: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd15; end
            7'd20: begin dec_op = OP_RUN_BASEMUL;   end
            7'd21: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd6;  end
            7'd22: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd0;  dec_slot_b = 5'd6; end
            7'd23: begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd0;  end
            7'd24: begin dec_op = OP_RUN_NTT;        dec_param  = 4'd1;  end  // inverse
            7'd25: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd0;  end
            7'd26: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd0;  dec_slot_b = 5'd16; end
            // ═══ Column 1: A_hat^T[*][1] · r_hat + e1[1] → u[1] (18 ops) ═══
            7'd27: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd1;  end
            7'd28: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd13; end
            7'd29: begin dec_op = OP_RUN_BASEMUL;   end
            7'd30: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd1;  end
            7'd31: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd4;  end
            7'd32: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd14; end
            7'd33: begin dec_op = OP_RUN_BASEMUL;   end
            7'd34: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd4;  end
            7'd35: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd1;  dec_slot_b = 5'd4; end
            7'd36: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd7;  end
            7'd37: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd15; end
            7'd38: begin dec_op = OP_RUN_BASEMUL;   end
            7'd39: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd7;  end
            7'd40: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd1;  dec_slot_b = 5'd7; end
            7'd41: begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd1;  end
            7'd42: begin dec_op = OP_RUN_NTT;        dec_param  = 4'd1;  end
            7'd43: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd1;  end
            7'd44: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd1;  dec_slot_b = 5'd17; end
            // ═══ Column 2: A_hat^T[*][2] · r_hat + e1[2] → u[2] (18 ops) ═══
            7'd45: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd2;  end
            7'd46: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd13; end
            7'd47: begin dec_op = OP_RUN_BASEMUL;   end
            7'd48: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd2;  end
            7'd49: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd5;  end
            7'd50: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd14; end
            7'd51: begin dec_op = OP_RUN_BASEMUL;   end
            7'd52: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd5;  end
            7'd53: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd2;  dec_slot_b = 5'd5; end
            7'd54: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd8;  end
            7'd55: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd15; end
            7'd56: begin dec_op = OP_RUN_BASEMUL;   end
            7'd57: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd8;  end
            7'd58: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd2;  dec_slot_b = 5'd8; end
            7'd59: begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd2;  end
            7'd60: begin dec_op = OP_RUN_NTT;        dec_param  = 4'd1;  end
            7'd61: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd2;  end
            7'd62: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd2;  dec_slot_b = 5'd18; end
            // ═══ Inner product: t_hat^T · r_hat + e2 + mu → v (19 ops) ═══
            7'd63: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd9;  end
            7'd64: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd13; end
            7'd65: begin dec_op = OP_RUN_BASEMUL;   end
            7'd66: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd9;  end
            7'd67: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd10; end
            7'd68: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd14; end
            7'd69: begin dec_op = OP_RUN_BASEMUL;   end
            7'd70: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd10; end
            7'd71: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd9;  dec_slot_b = 5'd10; end
            7'd72: begin dec_op = OP_COPY_TO_BM_A;  dec_slot_a = 5'd11; end
            7'd73: begin dec_op = OP_COPY_TO_BM_B;  dec_slot_a = 5'd15; end
            7'd74: begin dec_op = OP_RUN_BASEMUL;   end
            7'd75: begin dec_op = OP_COPY_FROM_BM;  dec_slot_a = 5'd11; end
            7'd76: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd9;  dec_slot_b = 5'd11; end
            7'd77: begin dec_op = OP_COPY_TO_NTT;   dec_slot_a = 5'd9;  end
            7'd78: begin dec_op = OP_RUN_NTT;        dec_param  = 4'd1;  end
            7'd79: begin dec_op = OP_COPY_FROM_NTT;  dec_slot_a = 5'd9;  end
            7'd80: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd9;  dec_slot_b = 5'd19; end
            7'd81: begin dec_op = OP_POLY_ADD;      dec_slot_a = 5'd9;  dec_slot_b = 5'd12; end
            // ═══ Compress (4 ops) ═══
            7'd82: begin dec_op = OP_COMPRESS; dec_slot_a = 5'd0;  dec_slot_b = 5'd16; dec_param = 4'd10; end
            7'd83: begin dec_op = OP_COMPRESS; dec_slot_a = 5'd1;  dec_slot_b = 5'd17; dec_param = 4'd10; end
            7'd84: begin dec_op = OP_COMPRESS; dec_slot_a = 5'd2;  dec_slot_b = 5'd18; dec_param = 4'd10; end
            7'd85: begin dec_op = OP_COMPRESS; dec_slot_a = 5'd9;  dec_slot_b = 5'd19; dec_param = 4'd4;  end

            default: dec_op = OP_NOP;
        endcase
    end

    localparam PO_LAST_STEP = 7'd85;

    // ─── Main FSM ────────────────────────────────────────────────
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase          <= PH_IDLE;
            done           <= 1'b0;
            m_reg          <= 256'd0;
            rho            <= 256'd0;
            h_reg          <= 256'd0;
            k_reg          <= 256'd0;
            r_reg          <= 256'd0;
            byte_cnt       <= 11'd0;
            ek_sub         <= EK_BYTE;
            byte_in_group  <= 2'd0;
            ek_b0          <= 8'd0;
            ek_b1          <= 8'd0;
            ek_b2          <= 8'd0;
            ek_poly_idx    <= 2'd0;
            ek_coeff_count <= 9'd0;
            xof_state      <= XOF_INIT;
            expand_idx     <= 4'd0;
            coeff_count    <= 9'd0;
            sample_b0      <= 8'd0;
            sample_b1      <= 8'd0;
            sample_b2      <= 8'd0;
            squeeze_ack    <= 1'b0;
            prf_state      <= PRF_INIT;
            prf_nonce      <= 3'd0;
            po_state       <= PO_ISSUE;
            po_step        <= 7'd0;
            keccak_mode    <= 2'd0;
            keccak_start   <= 1'b0;
            absorb_valid   <= 1'b0;
            absorb_data    <= 8'd0;
            absorb_last    <= 1'b0;
            squeeze_ready  <= 1'b0;
            kt_host_we     <= 1'b0;
            kt_host_slot   <= 5'd0;
            kt_host_addr   <= 8'd0;
            kt_host_din    <= 12'd0;
            cmd_op         <= 4'd0;
            cmd_slot_a     <= 5'd0;
            cmd_slot_b     <= 5'd0;
            cmd_param      <= 4'd0;
            cmd_start      <= 1'b0;
            cbd_bridge_en  <= 1'b0;
        end else begin
            // Defaults: single-cycle pulses
            done           <= 1'b0;
            keccak_start   <= 1'b0;
            cmd_start      <= 1'b0;
            kt_host_we     <= 1'b0;
            squeeze_ready  <= 1'b0;

            // Clear absorb_valid only if keccak accepted (absorb_ready=1)
            // or nothing pending (!absorb_valid). This prevents byte loss
            // when keccak transitions to PERMUTE on the same posedge that
            // auto_ctrl sets absorb_valid — the pulse persists until the
            // keccak returns to ABSORB and consumes it.
            if (absorb_ready || !absorb_valid) begin
                absorb_valid <= 1'b0;
                absorb_last  <= 1'b0;
            end

            case (phase)

            // ═════════════════════════════════════════════════════════
            PH_IDLE: begin
                if (start) begin
                    phase    <= PH_INPUT_M;
                    byte_cnt <= 11'd0;
                end
            end

            // ═════════════════════════════════════════════════════════
            // INPUT_M — Accept 32 bytes of message m
            // ═════════════════════════════════════════════════════════
            PH_INPUT_M: begin
                if (din_valid) begin
                    m_reg[byte_cnt[4:0]*8 +: 8] <= din_data;
                    if (byte_cnt == 11'd31) begin
                        // Last m byte accepted; start SHA3-256, move to INPUT_EK
                        phase          <= PH_INPUT_EK;
                        byte_cnt       <= 11'd0;
                        ek_sub         <= EK_BYTE;
                        byte_in_group  <= 2'd0;
                        ek_poly_idx    <= 2'd0;
                        ek_coeff_count <= 9'd0;
                        keccak_mode    <= 2'd0;   // SHA3-256
                        keccak_start   <= 1'b1;
                    end else begin
                        byte_cnt <= byte_cnt + 11'd1;
                    end
                end
            end

            // ═════════════════════════════════════════════════════════
            // INPUT_EK — Accept 1184 bytes, absorb to SHA3-256,
            //            parse ByteDecode(12) for t_hat, extract rho
            // ═════════════════════════════════════════════════════════
            PH_INPUT_EK: begin
                case (ek_sub)

                EK_BYTE: begin
                    if (din_valid && absorb_ready) begin
                        // Absorb every ek byte to Keccak
                        absorb_valid <= 1'b1;
                        absorb_data  <= din_data;

                        if (byte_cnt < 11'd1152) begin
                            // t_hat region: accumulate ByteDecode(12) bytes
                            case (byte_in_group)
                                2'd0: begin
                                    ek_b0 <= din_data;
                                    byte_in_group <= 2'd1;
                                    byte_cnt <= byte_cnt + 11'd1;
                                end
                                2'd1: begin
                                    ek_b1 <= din_data;
                                    byte_in_group <= 2'd2;
                                    byte_cnt <= byte_cnt + 11'd1;
                                end
                                2'd2: begin
                                    ek_b2 <= din_data;
                                    byte_in_group <= 2'd0;
                                    byte_cnt <= byte_cnt + 11'd1;
                                    ek_sub <= EK_WRITE_C0;
                                end
                                default: byte_in_group <= 2'd0;
                            endcase
                        end else begin
                            // rho region (bytes 1152-1183)
                            rho[byte_cnt[4:0]*8 +: 8] <= din_data;
                            if (byte_cnt == 11'd1183) begin
                                absorb_last <= 1'b1;
                                phase       <= PH_H_SQUEEZE;
                                byte_cnt    <= 11'd0;
                                squeeze_ack <= 1'b0;
                            end else begin
                                byte_cnt <= byte_cnt + 11'd1;
                            end
                        end
                    end
                end

                EK_WRITE_C0: begin
                    kt_host_we   <= 1'b1;
                    kt_host_slot <= 5'd9 + {3'd0, ek_poly_idx};
                    kt_host_addr <= ek_coeff_count[7:0];
                    kt_host_din  <= ek_c0;
                    ek_coeff_count <= ek_coeff_count + 9'd1;
                    ek_sub <= EK_WRITE_C1;
                end

                EK_WRITE_C1: begin
                    kt_host_we   <= 1'b1;
                    kt_host_slot <= 5'd9 + {3'd0, ek_poly_idx};
                    kt_host_addr <= ek_coeff_count[7:0];
                    kt_host_din  <= ek_c1;
                    if (ek_coeff_count == 9'd255) begin
                        ek_poly_idx    <= ek_poly_idx + 2'd1;
                        ek_coeff_count <= 9'd0;
                    end else begin
                        ek_coeff_count <= ek_coeff_count + 9'd1;
                    end
                    ek_sub <= EK_BYTE;
                end

                default: ek_sub <= EK_BYTE;
                endcase
            end

            // ═════════════════════════════════════════════════════════
            // H_SQUEEZE — Squeeze 32 bytes from SHA3-256 → h_reg
            // Two-cycle per byte: capture when ack=0, wait when ack=1
            // ═════════════════════════════════════════════════════════
            PH_H_SQUEEZE: begin
                if (squeeze_valid && !squeeze_ack) begin
                    squeeze_ready <= 1'b1;
                    squeeze_ack   <= 1'b1;
                    h_reg[byte_cnt[4:0]*8 +: 8] <= squeeze_data;
                    byte_cnt <= byte_cnt + 11'd1;
                end else if (squeeze_ack) begin
                    squeeze_ack <= 1'b0;
                    if (byte_cnt == 11'd32) begin
                        // Start SHA3-512 for G(m || h)
                        phase        <= PH_G_ABSORB;
                        byte_cnt     <= 11'd0;
                        keccak_mode  <= 2'd1;   // SHA3-512
                        keccak_start <= 1'b1;
                    end
                end
            end

            // ═════════════════════════════════════════════════════════
            // G_ABSORB — Absorb m[0..31] || h[0..31] to SHA3-512
            // ═════════════════════════════════════════════════════════
            PH_G_ABSORB: begin
                if (absorb_ready) begin
                    absorb_valid <= 1'b1;
                    if (byte_cnt < 11'd32)
                        absorb_data <= m_reg[byte_cnt[4:0]*8 +: 8];
                    else
                        absorb_data <= h_reg[byte_cnt[4:0]*8 +: 8];

                    if (byte_cnt == 11'd63) begin
                        absorb_last <= 1'b1;
                        phase       <= PH_G_SQUEEZE;
                        byte_cnt    <= 11'd0;
                        squeeze_ack <= 1'b0;
                    end else begin
                        byte_cnt <= byte_cnt + 11'd1;
                    end
                end
            end

            // ═════════════════════════════════════════════════════════
            // G_SQUEEZE — Squeeze 64 bytes: K[0..31] then r[0..31]
            // ═════════════════════════════════════════════════════════
            PH_G_SQUEEZE: begin
                if (squeeze_valid && !squeeze_ack) begin
                    squeeze_ready <= 1'b1;
                    squeeze_ack   <= 1'b1;
                    if (byte_cnt < 11'd32)
                        k_reg[byte_cnt[4:0]*8 +: 8] <= squeeze_data;
                    else
                        r_reg[byte_cnt[4:0]*8 +: 8] <= squeeze_data;
                    byte_cnt <= byte_cnt + 11'd1;
                end else if (squeeze_ack) begin
                    squeeze_ack <= 1'b0;
                    if (byte_cnt == 11'd64) begin
                        phase      <= PH_EXPAND_A;
                        expand_idx <= 4'd0;
                        xof_state  <= XOF_INIT;
                    end
                end
            end

            // ═════════════════════════════════════════════════════════
            // EXPAND_A — 9x SampleNTT via SHAKE-128(rho || j || i)
            // (identical to auto_keygen_ctrl)
            // ═════════════════════════════════════════════════════════
            PH_EXPAND_A: begin
                case (xof_state)

                // --- Start new SHAKE-128 for this (i,j) pair ---
                XOF_INIT: begin
                    keccak_mode  <= 2'd2;   // SHAKE-128
                    keccak_start <= 1'b1;
                    byte_cnt     <= 11'd0;
                    coeff_count  <= 9'd0;
                    xof_state    <= XOF_ABSORB;
                end

                // --- Absorb rho[0..31] || j || i (34 bytes) ---
                XOF_ABSORB: begin
                    if (absorb_ready) begin
                        absorb_valid <= 1'b1;
                        if (byte_cnt < 11'd32) begin
                            absorb_data <= rho[byte_cnt[4:0]*8 +: 8];
                        end else if (byte_cnt == 11'd32) begin
                            absorb_data <= {6'd0, expand_j};
                        end else begin
                            absorb_data <= {6'd0, expand_i};
                            absorb_last <= 1'b1;
                        end

                        if (byte_cnt == 11'd33) begin
                            xof_state <= XOF_B0;
                        end else begin
                            byte_cnt <= byte_cnt + 11'd1;
                        end
                    end
                end

                // --- Squeeze 3 bytes for rejection sampling ---
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
                        if (coeff_count == 9'd255) begin
                            if (expand_idx == 4'd8) begin
                                phase       <= PH_LOAD_M;
                                coeff_count <= 9'd0;
                            end else begin
                                expand_idx <= expand_idx + 4'd1;
                                xof_state  <= XOF_INIT;
                            end
                        end else begin
                            xof_state <= XOF_WRITE_D2;
                        end
                    end else begin
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
                                phase       <= PH_LOAD_M;
                                coeff_count <= 9'd0;
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
            // LOAD_M — Write Decompress(1, m) to slot 12
            // Decompress(1, bit) = bit ? 1665 : 0
            // ═════════════════════════════════════════════════════════
            PH_LOAD_M: begin
                kt_host_we   <= 1'b1;
                kt_host_slot <= 5'd12;
                kt_host_addr <= coeff_count[7:0];
                kt_host_din  <= m_reg[coeff_count[7:0]] ? 12'd1665 : 12'd0;

                if (coeff_count == 9'd255) begin
                    phase     <= PH_PRF_CBD;
                    prf_state <= PRF_INIT;
                    prf_nonce <= 3'd0;
                    byte_cnt  <= 11'd0;
                end else begin
                    coeff_count <= coeff_count + 9'd1;
                end
            end

            // ═════════════════════════════════════════════════════════
            // PRF_CBD — 7x SHAKE-256(r || nonce) → CBD sampling
            // ═════════════════════════════════════════════════════════
            PH_PRF_CBD: begin
                case (prf_state)

                // Start new SHAKE-256 for this nonce
                PRF_INIT: begin
                    keccak_mode  <= 2'd3;   // SHAKE-256
                    keccak_start <= 1'b1;
                    byte_cnt     <= 11'd0;
                    prf_state    <= PRF_ABSORB;
                end

                // Absorb r[0..31] || nonce (33 bytes)
                PRF_ABSORB: begin
                    if (absorb_ready) begin
                        absorb_valid <= 1'b1;
                        if (byte_cnt < 11'd32) begin
                            absorb_data <= r_reg[byte_cnt[4:0]*8 +: 8];
                        end else begin
                            absorb_data <= {5'd0, prf_nonce};
                            absorb_last <= 1'b1;
                        end

                        if (byte_cnt == 11'd32) begin
                            prf_state     <= PRF_BRIDGE;
                            cbd_bridge_en <= 1'b1;
                        end else begin
                            byte_cnt <= byte_cnt + 11'd1;
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
                        if (prf_nonce == 3'd6) begin
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
            // POLY_OPS — 86 micro-ops (encaps steps 7-92)
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
