// encaps_top — ML-KEM-768 encapsulation top-level wrapper
//
// Wires encaps_ctrl (sequencer) to kyber_top (micro-op engine).
// When encaps is idle, the host can read/write polynomial bank slots.
// When encaps is busy, the controller owns the command interface.
//
// Usage:
//   1. Host preloads A_hat[3×3] (slots 0-8), t_hat[3] (slots 9-11),
//      and message m (slot 12) via host_* interface.
//   2. Assert encaps_start for 1 cycle.
//   3. Feed CBD random bytes via cbd_byte_* interface (7 × 128 = 896 bytes).
//   4. Wait for encaps_done pulse.
//   5. Read results: compressed u[0..2] in slots 16-18, compressed v in slot 19.
//      Uncompressed u[0..2] in slots 0-2, uncompressed v in slot 9.

module encaps_top (
    input  wire        clk,
    input  wire        rst_n,

    // Host polynomial I/O (active when encaps is idle)
    input  wire        host_we,
    input  wire [4:0]  host_slot,
    input  wire [7:0]  host_addr,
    input  wire [11:0] host_din,
    output wire [11:0] host_dout,

    // Encaps control
    input  wire        encaps_start,
    output wire        encaps_done,
    output wire        encaps_busy,

    // CBD byte stream (passed through to kyber_top)
    input  wire        cbd_byte_valid,
    input  wire [7:0]  cbd_byte_data,
    output wire        cbd_byte_ready
);

    // ─── encaps_ctrl outputs ─────────────────────────────────────────
    wire [3:0]  ctrl_cmd_op;
    wire [4:0]  ctrl_cmd_slot_a;
    wire [4:0]  ctrl_cmd_slot_b;
    wire [3:0]  ctrl_cmd_param;
    wire        ctrl_cmd_start;

    // ─── kyber_top feedback ──────────────────────────────────────────
    wire        kt_done;
    wire        kt_busy;

    // ─── encaps_ctrl ─────────────────────────────────────────────────
    encaps_ctrl u_ctrl (
        .clk       (clk),
        .rst_n     (rst_n),
        .start     (encaps_start),
        .done      (encaps_done),
        .busy      (encaps_busy),
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

        // Command interface — driven by encaps_ctrl
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
