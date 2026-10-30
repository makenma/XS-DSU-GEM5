// Real SLC/SF/datapath; ACK and deallocation at the POC interface are controlled.
for (integer test = 0; test < 13; test = test + 1) begin
    trace_active = 0;
    scenario = test;
    reset_por = 1;
    hp_l3_req_h1_q = 0;
    hp_replay_h4 = 0;
    hl_seq_req_ack_h1 = 0;
    hp_seq_dealloc0_nohaz_h2 = 0;
    hp_seq_dealloc1_hx = 0;
    #20;
    reset_por = 0;
    hm_pm_idle_hx = 1;
    hn_cfg_l3sf_ctrl_out[`HN_CFG_L3SF_CTRL_OUT_ECC_DISABLE_RANGE] = 1;
    hm_aux_ctl_grp_out[`HM_AUX_CTL_GRP_OUT_SLC_ECC_PIPE_MODE_DIS_RANGE] = 1;
    hp_poc_to_l3req_h3 = '0;
    hp_poc_to_l3req_h3.opcode = CHIX_REQ_READUNIQUE;
    hp_poc_to_l3req_h3.ns = 1;
    hp_l3_addr_h3_q = 0;
    l3_tag_way_h4_q = '0;
    l3_tag_way_ecc_h5_q = '0;
    sf_tag_way_h5_q = '0;
    for (integer way = 0; way < 16; way = way + 1) begin
        sf_tag_way_h5_q[way].mesi = 2'b10;
        sf_tag_way_h5_q[way].uniq = test == 5 ? 0 : 1;
        sf_tag_way_h5_q[way].ns = 1;
        sf_tag_way_h5_q[way].pa = 1;
        sf_tag_way_h5_q[way].rnfid = 1;
        sf_tag_way_h5_q[way].rnfvec = 2;
    end
    sf_tag_way_ecc_h6_q = sf_tag_way_h5_q;
    #20;
    base_time = $time;
    trace_active = 1;
    hp_l3_req_h1_q = 1;
    if (test >= 7) begin
        // Sweep a single-cycle external hp_replay_h4 pulse. LAT2 must
        // align only the pulse at cycle three with this accepted request.
        for (integer edge_cycle = 0; edge_cycle < 6; edge_cycle = edge_cycle + 1) begin
            hp_replay_h4 = edge_cycle == test - 7;
            #10;
            hp_l3_req_h1_q = 0;
        end
        hp_replay_h4 = 0;
        #140;
    end else begin
        #10;
        hp_l3_req_h1_q = 0;
        #190;
    end
    // Cycle 20: present a POC ACK, or a foreground lookup of the victim.
    if (test < 2 || test == 4) begin
        hl_seq_req_ack_h1 = 1;
        #10;
        hl_seq_req_ack_h1 = 0;
        if (test == 0 || test == 4) begin
            // POC no-hazard H2 pulse follows H1 acceptance.
            hp_seq_dealloc0_nohaz_h2 = 1;
            hp_seq_dealloc0_entry_h2 = 0;
            #10;
            hp_seq_dealloc0_nohaz_h2 = 0;
            #80;
        end else begin
            #90;
        end
        if (test == 4) begin
            // Reuse slot zero after the read pointer has moved to slot one.
            #100;
            hp_l3_req_h1_q = 1;
            #10;
            hp_l3_req_h1_q = 0;
            #150;
        end
        if (test == 1) begin
            hp_seq_dealloc1_hx = 1;
            hp_seq_dealloc1_entry_hx = 0;
            #10;
            hp_seq_dealloc1_hx = 0;
        end
    end else if (test < 4 || test == 6) begin
        // Fixture uses zero address reconstruction configuration.
        hp_l3_addr_h3_q = 0;
        sf_tag_way_h5_q = '0;
        sf_tag_way_ecc_h6_q = '0;
        hp_l3_req_h1_q = 1;
        if (test == 2) hp_replay_h4 = 1;
        if (test == 6) hp_poc_to_l3req_h3.opcode = CHIX_REQ_WRITENOSNPFULL;
        if (test == 3) begin
            hp_poc_to_l3req_h3.is_l3fill = 1;
            hp_poc_to_l3req_h3.opcode = CHIX_REQ_WRITEBACKFULL;
            hp_poc_to_l3req_h3.l3_fill_state = 3'b110;
        end
        #10;
        hp_l3_req_h1_q = 0;
        #90;
    end
    #150;
end
$finish;
end
integer scenario = -1;
logic trace_active = 0;
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
    if (trace_active) $display("SEQ test=%0d cycle=%0d victim=%0d valid=%0h sent=%0h ptr=%0d req=%0d reqslot=%0d ack=%0d dealloc0=%0d dealloc1=%0d hit=%0d broadcast=%0h replay=%0d seqaddr=%0h pipeaddr=%0h pipens=%0d snpbrd=%0d snpdir=%0d nofill=%0d", scenario, ($time-base_time)/10,
        cmn_hns_sf_tagpipe.sf_victim_val_h6,
        cmn_hns_sf_tagpipe.sf_seqvld_q, cmn_hns_sf_tagpipe.sf_seqsent_q,
        cmn_hns_sf_tagpipe.sf_seqrdptr_q, sf_seq_req_h0,
        cmn_hns_sf_tagpipe.sf_seq_req_entry_h0_q,
        l3_sf_seq_req_ack_h1, hp_seq_dealloc0_nohaz_h2, hp_seq_dealloc1_hx,
        cmn_hns_sf_tagpipe.sf_seqhit_h5,
        cmn_hns_sf_tagpipe.sf_seqbroadcast_q, l3_to_poc_tag_h9.replay,
        cmn_hns_sf_tagpipe.sf_seqaddr_q[0], cmn_hns_sf_tagpipe.sf_pa_hx_q[5], cmn_hns_sf_tagpipe.sf_ns_hx_q[5],
        sf_to_l3_h6.snp_brd, sf_to_l3_h6.snp_dir, sf_to_l3_h6.nofill);
end
endmodule
