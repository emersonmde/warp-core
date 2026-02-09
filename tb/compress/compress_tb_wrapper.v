// compress_tb_wrapper — Instantiates compress for all 5 Kyber D values
//
// Allows a single testbench to test all D values in one simulation.

module compress_tb_wrapper (
    input  wire [11:0] x,
    output wire [0:0]  result_d1,
    output wire [3:0]  result_d4,
    output wire [4:0]  result_d5,
    output wire [9:0]  result_d10,
    output wire [10:0] result_d11
);

    compress #(.D(1))  u_d1  (.x(x), .result(result_d1));
    compress #(.D(4))  u_d4  (.x(x), .result(result_d4));
    compress #(.D(5))  u_d5  (.x(x), .result(result_d5));
    compress #(.D(10)) u_d10 (.x(x), .result(result_d10));
    compress #(.D(11)) u_d11 (.x(x), .result(result_d11));

endmodule
