// keygen_top — ML-KEM-768 key generation top-level wrapper
//
// Wires keygen_ctrl (sequencer) to kyber_top (micro-op engine).
// When keygen is idle, the host can read/write polynomial bank slots.
// When keygen is busy, the controller owns the command interface.
//
// Usage:
//   1. Host preloads A_hat[3×3] (slots 0-8) via host_* interface.
//   2. Assert keygen_start for 1 cycle.
//   3. Feed CBD random bytes via cbd_byte_* interface (6 × 128 = 768 bytes).
//   4. Wait for keygen_done pulse.
//   5. Read results: t_hat[0..2] in slots 0,3,6; s_hat[0..2] in slots 9-11.

module keygen_top (
    input  wire        clk,
    input  wire        rst_n,

    // Host polynomial I/O (active when keygen is idle)
    input  wire        host_we,
    input  wire [4:0]  host_slot,
    input  wire [7:0]  host_addr,
    input  wire [11:0] host_din,
    output wire [11:0] host_dout,

    // Keygen control
    input  wire        keygen_start,
    output wire        keygen_done,
    output wire        keygen_busy,

    // CBD byte stream (passed through to kyber_top)
    input  wire        cbd_byte_valid,
    input  wire [7:0]  cbd_byte_data,
    output wire        cbd_byte_ready
);

    // ─── keygen_ctrl outputs ─────────────────────────────────────────
    wire [3:0]  ctrl_cmd_op;
    wire [4:0]  ctrl_cmd_slot_a;
    wire [4:0]  ctrl_cmd_slot_b;
    wire [3:0]  ctrl_cmd_param;
    wire        ctrl_cmd_start;

    // ─── kyber_top feedback ──────────────────────────────────────────
    wire        kt_done;
    wire        kt_busy;

    // ─── keygen_ctrl ─────────────────────────────────────────────────
    keygen_ctrl u_ctrl (
        .clk       (clk),
        .rst_n     (rst_n),
        .start     (keygen_start),
        .done      (keygen_done),
        .busy      (keygen_busy),
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

        // Command interface — driven by keygen_ctrl
        .cmd_op         (ctrl_cmd_op),
        .cmd_slot_a     (ctrl_cmd_slot_a),
        .cmd_slot_b     (ctrl_cmd_slot_b),
        .cmd_param      (ctrl_cmd_param),
        .start          (ctrl_cmd_start),
        .done           (kt_done),
        .busy           (kt_busy),

        // CBD byte stream
        .cbd_byte_valid (cbd_byte_valid),
        .cbd_byte_data  (cbd_byte_data),
        .cbd_byte_ready (cbd_byte_ready)
    );

endmodule
