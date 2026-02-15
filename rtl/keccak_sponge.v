/*
 * keccak_sponge.v — Multi-mode Keccak sponge controller (FIPS 202)
 *
 * Supports SHA3-256, SHA3-512, SHAKE-128, SHAKE-256
 * Iterative Keccak-f[1600] permutation (24 cycles per permutation)
 * Byte-level absorb/squeeze interface with valid/ready handshake
 *
 * Mode parameters:
 *   Mode 0 (SHA3-256):  rate=136 bytes, suffix=0x06, output=32 bytes
 *   Mode 1 (SHA3-512):  rate=72 bytes,  suffix=0x06, output=64 bytes
 *   Mode 2 (SHAKE-128): rate=168 bytes, suffix=0x1F, unlimited output
 *   Mode 3 (SHAKE-256): rate=136 bytes, suffix=0x1F, unlimited output
 *
 * Module hierarchy:
 *   keccak_sponge
 *   +-- keccak_round (combinational, one round per clock)
 *       +-- keccak_rc (round constant ROM)
 */

`default_nettype none

module keccak_sponge (
    input  wire        clk,
    input  wire        rst_n,

    // Mode selection
    input  wire [1:0]  mode,         // 0=SHA3-256, 1=SHA3-512, 2=SHAKE-128, 3=SHAKE-256
    input  wire        start,        // pulse to begin new hash (zeroes state)

    // Absorb interface
    input  wire        absorb_valid,
    input  wire [7:0]  absorb_data,
    input  wire        absorb_last,  // marks final input byte
    output wire        absorb_ready,

    // Squeeze interface
    output wire [7:0]  squeeze_data,
    output wire        squeeze_valid,
    output wire        squeeze_last, // final output byte (SHA3 fixed-output only)
    input  wire        squeeze_ready,

    // Status
    output wire        busy
);

    // =========================================================================
    // FSM states
    // =========================================================================
    localparam S_IDLE    = 3'd0;
    localparam S_ABSORB  = 3'd1;
    localparam S_PAD     = 3'd2;
    localparam S_PERMUTE = 3'd3;
    localparam S_SQUEEZE = 3'd4;

    reg [2:0]    fsm_state;
    reg [2:0]    return_to;      // state to return to after PERMUTE
    reg [1:0]    mode_reg;       // latched on start
    reg [1599:0] state_reg;      // Keccak state (200 bytes)
    reg [7:0]    byte_idx;       // byte position within rate block
    reg [4:0]    round_ctr;      // Keccak-f round counter (0..23)
    reg [6:0]    out_remaining;  // SHA3 output bytes remaining

    // =========================================================================
    // Mode parameters (combinational from mode_reg)
    // =========================================================================
    reg [7:0] rate_bytes;
    reg [7:0] domain_suffix;
    reg [6:0] output_len;
    reg       is_sha3;

    always @(*) begin
        case (mode_reg)
            2'd0: begin  // SHA3-256
                rate_bytes    = 8'd136;
                domain_suffix = 8'h06;
                output_len    = 7'd32;
                is_sha3       = 1'b1;
            end
            2'd1: begin  // SHA3-512
                rate_bytes    = 8'd72;
                domain_suffix = 8'h06;
                output_len    = 7'd64;
                is_sha3       = 1'b1;
            end
            2'd2: begin  // SHAKE-128
                rate_bytes    = 8'd168;
                domain_suffix = 8'h1F;
                output_len    = 7'd0;
                is_sha3       = 1'b0;
            end
            2'd3: begin  // SHAKE-256
                rate_bytes    = 8'd136;
                domain_suffix = 8'h1F;
                output_len    = 7'd0;
                is_sha3       = 1'b0;
            end
        endcase
    end

    wire [7:0] rate_m1 = rate_bytes - 8'd1;

    // =========================================================================
    // Keccak-f[1600] round (combinational)
    // =========================================================================
    wire [1599:0] round_out;

    keccak_round round_inst (
        .state_in  (state_reg),
        .round_num (round_ctr),
        .state_out (round_out)
    );

    // =========================================================================
    // Output signals
    // =========================================================================
    assign absorb_ready  = (fsm_state == S_ABSORB);
    assign squeeze_valid = (fsm_state == S_SQUEEZE);
    assign squeeze_data  = state_reg[byte_idx * 8 +: 8];
    assign squeeze_last  = (fsm_state == S_SQUEEZE) && is_sha3
                           && (out_remaining == 7'd1);
    assign busy          = (fsm_state != S_IDLE);

    // =========================================================================
    // Main FSM
    // =========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            fsm_state     <= S_IDLE;
            return_to     <= S_IDLE;
            mode_reg      <= 2'd0;
            state_reg     <= 1600'd0;
            byte_idx      <= 8'd0;
            round_ctr     <= 5'd0;
            out_remaining <= 7'd0;
        end else begin
            case (fsm_state)

                // ---------------------------------------------------------
                // IDLE — wait for start, zero state, latch mode
                // ---------------------------------------------------------
                S_IDLE: begin
                    if (start) begin
                        state_reg <= 1600'd0;
                        mode_reg  <= mode;
                        byte_idx  <= 8'd0;
                        fsm_state <= S_ABSORB;
                    end
                end

                // ---------------------------------------------------------
                // ABSORB — accept bytes via valid/ready, XOR into state
                // ---------------------------------------------------------
                S_ABSORB: begin
                    if (absorb_valid) begin
                        // XOR input byte into state at byte_idx position
                        state_reg[byte_idx * 8 +: 8] <=
                            state_reg[byte_idx * 8 +: 8] ^ absorb_data;

                        if (absorb_last && byte_idx == rate_m1) begin
                            // Last byte fills the rate block — permute, then pad
                            byte_idx  <= 8'd0;
                            round_ctr <= 5'd0;
                            return_to <= S_PAD;
                            fsm_state <= S_PERMUTE;
                        end else if (absorb_last) begin
                            // Last byte mid-block — go to padding
                            byte_idx  <= byte_idx + 8'd1;
                            fsm_state <= S_PAD;
                        end else if (byte_idx == rate_m1) begin
                            // Rate block full — permute, continue absorbing
                            byte_idx  <= 8'd0;
                            round_ctr <= 5'd0;
                            return_to <= S_ABSORB;
                            fsm_state <= S_PERMUTE;
                        end else begin
                            byte_idx <= byte_idx + 8'd1;
                        end
                    end else if (absorb_last) begin
                        // Empty message — go directly to padding
                        fsm_state <= S_PAD;
                    end
                end

                // ---------------------------------------------------------
                // PAD — apply FIPS 202 pad10*1
                // XOR domain suffix at byte_idx, XOR 0x80 at rate-1
                // ---------------------------------------------------------
                S_PAD: begin
                    if (byte_idx == rate_m1) begin
                        // Domain suffix and 0x80 overlap in same byte
                        state_reg[byte_idx * 8 +: 8] <=
                            state_reg[byte_idx * 8 +: 8]
                            ^ (domain_suffix | 8'h80);
                    end else begin
                        // Domain suffix at current position
                        state_reg[byte_idx * 8 +: 8] <=
                            state_reg[byte_idx * 8 +: 8] ^ domain_suffix;
                        // 0x80 at last byte of rate block
                        state_reg[rate_m1 * 8 +: 8] <=
                            state_reg[rate_m1 * 8 +: 8] ^ 8'h80;
                    end
                    byte_idx      <= 8'd0;
                    round_ctr     <= 5'd0;
                    out_remaining <= output_len;
                    return_to     <= S_SQUEEZE;
                    fsm_state     <= S_PERMUTE;
                end

                // ---------------------------------------------------------
                // PERMUTE — 24-round iterative Keccak-f[1600]
                // ---------------------------------------------------------
                S_PERMUTE: begin
                    state_reg <= round_out;
                    if (round_ctr == 5'd23) begin
                        round_ctr <= 5'd0;
                        fsm_state <= return_to;
                    end else begin
                        round_ctr <= round_ctr + 5'd1;
                    end
                end

                // ---------------------------------------------------------
                // SQUEEZE — output bytes via valid/ready
                // SHA3: fixed output (32 or 64 bytes), assert squeeze_last
                // SHAKE: unlimited output, consumer terminates via start
                // ---------------------------------------------------------
                S_SQUEEZE: begin
                    if (start) begin
                        // SHAKE termination / new hash start
                        state_reg <= 1600'd0;
                        mode_reg  <= mode;
                        byte_idx  <= 8'd0;
                        fsm_state <= S_ABSORB;
                    end else if (squeeze_ready) begin
                        if (is_sha3 && out_remaining == 7'd1) begin
                            // Final SHA3 output byte — done
                            fsm_state <= S_IDLE;
                        end else if (!is_sha3 && byte_idx == rate_m1) begin
                            // SHAKE: squeeze block exhausted, permute for more
                            byte_idx  <= 8'd0;
                            round_ctr <= 5'd0;
                            return_to <= S_SQUEEZE;
                            fsm_state <= S_PERMUTE;
                        end else begin
                            byte_idx <= byte_idx + 8'd1;
                            if (is_sha3)
                                out_remaining <= out_remaining - 7'd1;
                        end
                    end
                end

                default: fsm_state <= S_IDLE;

            endcase
        end
    end

endmodule
