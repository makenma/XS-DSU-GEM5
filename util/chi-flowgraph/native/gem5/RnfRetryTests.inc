
namespace gem5::Chi {
TEST(Cache2ChiBridgeProtocolTest, RetryCreditBeforeOrAfterAckIgnoresTxnID)
{
    for (bool early : {false, true}) {
        Cache2ChiBridgeProtocolTestPeer::RetryHarness h;
        h.add(11);
        RawRsp ack{}; ack.opcode=3; ack.srcid=TestHome;
        ack.tgtid=TestRequester; ack.txnid=11; ack.pcrdtype=7;
        auto credit=ack; credit.opcode=7; credit.txnid=0xfefe;
        EXPECT_TRUE(h.accept(early?credit:ack).empty());
        const auto ready=h.accept(early?ack:credit);
        ASSERT_EQ(ready, std::vector<uint32_t>({11}));
        EXPECT_EQ(h.txns.at(11).req.AllowRetry, 0);
        EXPECT_EQ(h.txns.at(11).req.pcrdtype, 7);
        EXPECT_EQ(h.txns.at(11).retryCount, 1);
        EXPECT_TRUE(h.accept(credit).empty()); // no duplicate enqueue
    }
}
TEST(Cache2ChiBridgeProtocolTest, RetryCreditRequiresMatchingNodePairAndType)
{
    Cache2ChiBridgeProtocolTestPeer::RetryHarness h;
    h.add(11);
    RawRsp ack{}; ack.opcode=3; ack.srcid=TestHome;
    ack.tgtid=TestRequester; ack.txnid=11; ack.pcrdtype=7;
    EXPECT_TRUE(h.accept(ack).empty());
    auto credit=ack; credit.opcode=7; credit.txnid=0;
    credit.pcrdtype=6; EXPECT_TRUE(h.accept(credit).empty());
    credit.pcrdtype=7; credit.srcid=TestHome+1;
    EXPECT_TRUE(h.accept(credit).empty());
    credit.srcid=TestHome; credit.tgtid=TestRequester+1;
    EXPECT_TRUE(h.accept(credit).empty());
    credit.tgtid=TestRequester;
    EXPECT_EQ(h.accept(credit), std::vector<uint32_t>({11}));
}
TEST(Cache2ChiBridgeProtocolTest, OneCreditCannotReissueTwoRejectedRequests)
{
    Cache2ChiBridgeProtocolTestPeer::RetryHarness h;
    h.add(11); h.add(12);
    RawRsp ack{}; ack.opcode=3; ack.srcid=TestHome;
    ack.tgtid=TestRequester; ack.pcrdtype=7;
    ack.txnid=12; EXPECT_TRUE(h.accept(ack).empty());
    ack.txnid=11; EXPECT_TRUE(h.accept(ack).empty());
    auto credit=ack; credit.opcode=7; credit.txnid=0xffffffff;
    EXPECT_EQ(h.accept(credit), std::vector<uint32_t>({11}));
    EXPECT_EQ(h.accept(credit), std::vector<uint32_t>({12}));
}
} // namespace gem5::Chi
