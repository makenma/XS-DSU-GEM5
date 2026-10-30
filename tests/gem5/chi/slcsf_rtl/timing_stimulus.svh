// Directed history/replay boundaries. Tag SRAM response ports contain fixed
// rows; these tests measure the real tag-pipeline registers and write enables.
for (integer test = 0; test < 9; test = test + 1) begin
    scenario = test;
    reset_por = 1; hp_l3_req_h1_q = 0;
    #20;
    reset_por = 0;
    hm_pm_idle_hx = 1;
    hn_cfg_l3sf_ctrl_out[`HN_CFG_L3SF_CTRL_OUT_ECC_DISABLE_RANGE] = 1;
    hm_aux_ctl_grp_out[`HM_AUX_CTL_GRP_OUT_SLC_ECC_PIPE_MODE_DIS_RANGE] = 1;
    hp_poc_to_l3req_h3 = '0;
    hp_poc_to_l3req_h3.opcode = CHIX_REQ_READONCE;
    hp_poc_to_l3req_h3.ns = 1;
    hp_replay_h4 = 0;
    hp_fill_data_ow_val_h5_q = 0;
    l3_tag_way_h4_q = '0; l3_tag_way_ecc_h5_q = '0;
    sf_tag_way_h5_q = '0; sf_tag_way_ecc_h6_q = '0;
    if (test == 0 || test == 1 || test == 7) begin
        l3_tag_way_h4_q[0].mesi = 3;
        l3_tag_way_h4_q[0].ns = 1;
        l3_tag_way_ecc_h5_q = l3_tag_way_h4_q;
    end
    if (test >= 2 && test <= 4) begin
        for (integer way = 0; way < 16; way = way + 1) begin
            sf_tag_way_h5_q[way].mesi = 2;
            sf_tag_way_h5_q[way].uniq = 1;
            sf_tag_way_h5_q[way].ns = 1;
            sf_tag_way_h5_q[way].pa = way + 1;
        end
        sf_tag_way_ecc_h6_q = sf_tag_way_h5_q;
    end
    if (test == 5 || test == 6) begin
        hp_poc_to_l3req_h3.opcode = CHIX_REQ_WRITEBACKFULL;
        hp_poc_to_l3req_h3.is_l3fill = 1;
        hp_poc_to_l3req_h3.l3_fill_state = 6;
        hp_fill_data_ow_val_h5_q = 3;
    end
    if (test == 7 || test == 8)
        hp_poc_to_l3req_h3.opcode = CHIX_REQ_CLEANINVALID;
    if (test == 8) begin
        sf_tag_way_h5_q[0].mesi = 2;
        sf_tag_way_h5_q[0].uniq = 1;
        sf_tag_way_h5_q[0].ns = 1;
        sf_tag_way_h5_q[0].rnfid = 1;
        sf_tag_way_h5_q[0].rnfvec = 2;
        sf_tag_way_ecc_h6_q = sf_tag_way_h5_q;
    end
    gap = (test == 1 || test == 4) ? 10 : test == 5 ? 12 :
          (test == 2 || test == 6) ? 2 : 8;
    #20;
    base_time = $time;
    hp_l3_req_h1_q = 1; #10; hp_l3_req_h1_q = 0;
    #((gap - 1) * 10);
    hp_l3_req_h1_q = 1; #10; hp_l3_req_h1_q = 0;
    if (test == 6) begin
        // First fill H4 sees POC replay, while its raw H5/H6 way must still
        // reserve way 0 for the second fill's replacement selection.
        hp_replay_h4 = 1; #20; hp_replay_h4 = 0;
    end
    #320;
end
$finish;
end
integer scenario = -1;
integer gap = 0;
time base_time = 0;
always #5 begin
    clk_por = ~clk_por; clk_por_free = ~clk_por_free;
    ck_qw0 = ~ck_qw0; ck_qw1 = ~ck_qw1;
    ck_qw2 = ~ck_qw2; ck_qw3 = ~ck_qw3;
end
always @(negedge clk_por) begin
    #1;
    $display("TIMING test=%0d cycle=%0d accept=%0d busy=%0d valid=%0d replay=%0d l3haz=%0d sfhaz=%0d l3hist=%0d sfhist=%0d l3write=%0d sfwrite=%0d data0=%0d data2=%0d beats=%0d",
        scenario, ($time - base_time)/10, hp_l3_req_h1_q & ~l3_busy_h1_q,
        l3_busy_h1_q,
        cmn_hns_l3_tagpipe.l3_pipeval_hx_q[7] &
          (cmn_hns_l3_tagpipe.l3_optype_hx_q[7][`L3_OP_READ] |
           cmn_hns_l3_tagpipe.l3_optype_hx_q[7][`L3_OP_EVICT]),
        l3_to_poc_tag_h9.replay,
        cmn_hns_l3_tagpipe.l3_setway_haz_h6,
        cmn_hns_sf_tagpipe.sf_setway_haz_h6,
        cmn_hns_l3_tagpipe.l3_setway_val_hx_q,
        cmn_hns_sf_tagpipe.sf_setway_val_hx_q,
        l3_tag_wr_en_h2, sf_tag_wr_en_h3,
        l3_qw0_lo_wr_en_h5_q, l3_qw2_lo_wr_en_h6_q,
        l3_hp_rd_data_owval_h11);
end
endmodule
