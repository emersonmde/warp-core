// decaps_top — ML-KEM-768 decryption (K-PKE.Decrypt) top-level wrapper
//
// Wires decaps_ctrl (sequencer) to kyber_top (micro-op engine).
// When decaps is idle, the host can read/write polynomial bank slots.
// When decaps is busy, the controller owns the command interface.
//
// Simpler than encaps_top: no CBD byte stream needed (decrypt doesn't
// sample noise). CBD inputs on kyber_top are tied to 0.
//
// Usage:
//   1. Host preloads compressed u[0..2] (D=10) in slots 0-2,
//      compressed v (D=4) in slot 3, s_hat[0..2] in slots 9-11.
//   2. Assert decrypt_start for 1 cycle.
//   3. Wait for decrypt_done pulse.
//   4. Read m' from slot 4 (256 coefficients, each 0 or 1).

module decaps_top (
    input  wire        clk,
    input  wire        rst_n,

    // Host polynomial I/O (active when decaps is idle)
    input  wire        host_we,
    input  wire [4:0]  host_slot,
    input  wire [7:0]  host_addr,
    input  wire [11:0] host_din,
    output wire [11:0] host_dout,

    // Decrypt control
    input  wire        decrypt_start,
    output wire        decrypt_done,
    output wire        decrypt_busy
);

    // ─── decaps_ctrl outputs ─────────────────────────────────────────
    wire [3:0]  ctrl_cmd_op;
    wire [4:0]  ctrl_cmd_slot_a;
    wire [4:0]  ctrl_cmd_slot_b;
    wire [3:0]  ctrl_cmd_param;
    wire        ctrl_cmd_start;

    // ─── kyber_top feedback ──────────────────────────────────────────
    wire        kt_done;
    wire        kt_busy;

    // ─── decaps_ctrl ─────────────────────────────────────────────────
    decaps_ctrl u_ctrl (
        .clk       (clk),
        .rst_n     (rst_n),
        .start     (decrypt_start),
        .done      (decrypt_done),
        .busy      (decrypt_busy),
        .cmd_op    (ctrl_cmd_op),
        .cmd_slot_a(ctrl_cmd_slot_a),
        .cmd_slot_b(ctrl_cmd_slot_b),
        .cmd_param (ctrl_cmd_param),
        .cmd_start (ctrl_cmd_start),
        .cmd_done  (kt_done)
    );

    // ─── kyber_top ───────────────────────────────────────────────────
    kyber_top u_kt (
        .clk            (clk),
        .rst_n          (rst_n),

        // Host I/O — only effective when kyber_top is IDLE
        .host_we        (host_we),
        .host_slot      (host_slot),
        .host_addr      (host_addr),
        .host_din       (host_din),
        .host_dout      (host_dout),

        // Command interface — driven by decaps_ctrl
        .cmd_op         (ctrl_cmd_op),
        .cmd_slot_a     (ctrl_cmd_slot_a),
        .cmd_slot_b     (ctrl_cmd_slot_b),
        .cmd_param      (ctrl_cmd_param),
        .start          (ctrl_cmd_start),
        .done           (kt_done),
        .busy           (kt_busy),

        // CBD byte stream — not used in decrypt, tie off
        .cbd_byte_valid (1'b0),
        .cbd_byte_data  (8'd0),
        .cbd_byte_ready ()       // left unconnected
    );

endmodule
