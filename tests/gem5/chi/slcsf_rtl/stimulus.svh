// Continuation of generated initial block: all unconnected inputs are zero.
for (integer test = 0; test < 14; test = test + 1) begin
    scenario = test;
    reset_por = 1;
    hp_l3_req_h1_q = 0;
    #20;
    reset_por = 0;
    hm_pm_idle_hx = 1;
    hn_cfg_l3sf_ctrl_out[`HN_CFG_L3SF_CTRL_OUT_ECC_DISABLE_RANGE] = 1;
    hm_aux_ctl_grp_out[`HM_AUX_CTL_GRP_OUT_SLC_ECC_PIPE_MODE_DIS_RANGE] = 1;
    hp_poc_to_l3req_h3 = '0;
    hp_poc_to_l3req_h3.opcode = CHIX_REQ_READUNIQUE;
    hp_poc_to_l3req_h3.ns = 1;
    l3_tag_way_h4_q = '0;
    l3_tag_way_ecc_h5_q = '0;
    sf_tag_way_h5_q = '0;
    sf_tag_way_ecc_h6_q = '0;
    hp_replay_h4 = 0;
    if (test == 1 || test == 3 || test == 5 || test == 8 || test == 9 || test == 10 || test == 11 || test == 13) begin
        l3_tag_way_h4_q[0].mesi = 2'b11;
        l3_tag_way_h4_q[0].uniq = 0;
        l3_tag_way_h4_q[0].ns = 1;
        l3_tag_way_ecc_h5_q = l3_tag_way_h4_q;
    end
    if (test == 2 || test == 3 || test == 6 || test == 7 || test == 10) begin
        sf_tag_way_h5_q[0].mesi = 2'b10;
        sf_tag_way_h5_q[0].uniq = 1;
        sf_tag_way_h5_q[0].ns = 1;
        sf_tag_way_h5_q[0].rnfid = 1;
        sf_tag_way_h5_q[0].rnfvec = 2;
        sf_tag_way_ecc_h6_q = sf_tag_way_h5_q;
    end
    if (test == 4) begin
        hp_poc_to_l3req_h3.is_l3fill = 1;
        hp_poc_to_l3req_h3.opcode = CHIX_REQ_WRITEBACKFULL;
        hp_poc_to_l3req_h3.l3_fill_state = 3'b110;
        hp_fill_data_ow_val_h5_q = 3;
    end
    if (test == 5) hp_replay_h4 = 1;
    if (test == 6 || test == 7 || test == 8 || test == 9)
        hp_poc_to_l3req_h3.opcode = CHIX_REQ_READCLEAN;
    if (test == 7) begin
        sf_tag_way_h5_q[0].mesi = 2'b01;
        sf_tag_way_ecc_h6_q = sf_tag_way_h5_q;
    end
    if (test == 8) begin
        l3_tag_way_h4_q[0].mesi = 2'b10;
        l3_tag_way_ecc_h5_q = l3_tag_way_h4_q;
    end
    if (test == 10) hp_poc_to_l3req_h3.opcode = CHIX_REQ_CLEANINVALID;
    if (test == 12 || test == 13) hp_poc_to_l3req_h3.opcode = CHIX_REQ_READONCE;
    #20;
    base_time = $time;
    hp_l3_req_h1_q = 1;
    if (test == 11) begin #80; end else begin #10; end
    hp_l3_req_h1_q = 0;
    #300;
end
$finish;
end
integer scenario = -1;
time base_time = 0;
always #5 begin
    clk_por = ~clk_por;
    clk_por_free = ~clk_por_free;
    ck_qw0 = ~ck_qw0;
    ck_qw1 = ~ck_qw1;
    ck_qw2 = ~ck_qw2;
    ck_qw3 = ~ck_qw3;
end
always @(negedge clk_por) begin
    #1;
    $display("TRACE test=%0d cycle=%0d busy=%0d accept=%0d h5=%0d h6=%0d h7=%0d h8=%0d h8a=%0d h9=%0d result=%0d replay=%0d l3hit=%0d sfhit=%0d l3wr=%0d l3ramwr=%0d lruwr=%0d sfwr=%0d sframwr=%0d owval=%0d datawr0=%0d datawr2=%0d eccpipe=%0d slcstate=%0d sfstate=%0d",
             scenario, ($time - base_time)/10, l3_busy_h1_q, hp_l3_req_h1_q & ~l3_busy_h1_q,
             cmn_hns_l3_tagpipe.l3_pipeval_hx_q[5],
             cmn_hns_l3_tagpipe.l3_pipeval_hx_q[6],
             cmn_hns_l3_tagpipe.l3_pipeval_hx_q[7],
             cmn_hns_l3_tagpipe.l3_pipeval_hx_q[8],
             cmn_hns_l3_tagpipe.l3_pipeval_h8a_q,
             cmn_hns_l3_tagpipe.l3_pipeval_hx_q[9],
             l3_to_poc_tag_h9.tagrd_val, l3_to_poc_tag_h9.replay,
             l3_to_poc_tag_h9.l3_hit, l3_to_poc_tag_h9.sf_hit,
             l3_tag_wr_en_h1_q, |l3_tag_wr_en_h2, l3_lru_wr_en_h2, sf_tag_wr_en_h2,
             |sf_tag_wr_en_h3, l3_hp_rd_data_owval_h11,
             l3_qw0_lo_wr_en_h5_q, l3_qw2_lo_wr_en_h6_q,
             cmn_hns_l3_tagpipe.ecc_pipe_mode,
             l3_to_poc_tag_h9.l3_upd_st, l3_to_poc_tag_h9.sf_upd_st);
end
endmodule
