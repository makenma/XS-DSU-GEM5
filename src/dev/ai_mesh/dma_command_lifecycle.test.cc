#include <gtest/gtest.h>

#include "dev/ai_mesh/dma_command_lifecycle.hh"

using namespace gem5::ai_mesh;

namespace
{

DmaCommandLifecycle::Admission
group(uint32_t command_id, uint32_t transfer_id, uint64_t expected_bytes,
      std::vector<uint32_t> descriptors)
{
    DmaCommandLifecycle::Admission admission;
    admission.command_id = command_id;
    admission.generation = 0;
    admission.completion_event = 7;
    admission.transfer_id = transfer_id;
    admission.expected_bytes = expected_bytes;
    admission.descriptors = std::move(descriptors);
    return admission;
}

void
submitAll(DmaCommandLifecycle &lifecycle, uint32_t command_id)
{
    while (uint32_t descriptor_id = lifecycle.nextDescriptor(command_id)) {
        lifecycle.noteSubmitted(command_id, descriptor_id);
    }
}

} // namespace

TEST(DmaCommandLifecycleTest, PublishesOnceAfterEveryAdmittedWrite)
{
    DmaCommandLifecycle lifecycle;
    lifecycle.admit(group(3, 9, 6, {1, 2, 3}));
    submitAll(lifecycle, 3);

    DmaCommandLifecycle::Retirement first =
        lifecycle.retire(3, 1, 2, true, 1);
    EXPECT_TRUE(first.known);
    EXPECT_FALSE(first.notify);
    EXPECT_EQ(first.progress.committed_descriptors, 1u);
    EXPECT_EQ(first.progress.committed_bytes, 2u);
    EXPECT_FALSE(first.progress.published);

    DmaCommandLifecycle::Retirement duplicate =
        lifecycle.retire(3, 1, 2, true, 1);
    EXPECT_TRUE(duplicate.duplicate);
    EXPECT_EQ(duplicate.progress.committed_descriptors, 1u);
    EXPECT_EQ(duplicate.progress.committed_bytes, 2u);

    lifecycle.retire(3, 3, 2, true, 1);
    DmaCommandLifecycle::Retirement last = lifecycle.retire(3, 2, 2, true, 1);
    EXPECT_TRUE(last.notify);
    EXPECT_TRUE(last.drained);
    EXPECT_EQ(last.progress.notifications, 1u);
    EXPECT_TRUE(last.progress.published);
    EXPECT_EQ(last.progress.committed_descriptors, 3u);
    EXPECT_EQ(last.progress.committed_bytes, 6u);

    lifecycle.finish(3);
    DmaCommandLifecycle::Retirement late = lifecycle.retire(3, 2, 2, true, 1);
    EXPECT_TRUE(late.duplicate);
    EXPECT_FALSE(late.notify);
    EXPECT_EQ(late.progress.notifications, 1u);
}

TEST(DmaCommandLifecycleTest, FailureKeepsCountingLaterPhysicalWritesWithoutPublishing)
{
    DmaCommandLifecycle lifecycle;
    lifecycle.admit(group(4, 2, 8, {5, 6, 7, 8}));
    submitAll(lifecycle, 4);

    DmaCommandLifecycle::Retirement failed =
        lifecycle.retire(4, 5, 2, false, 1);
    EXPECT_TRUE(failed.first_failure);
    EXPECT_TRUE(failed.progress.failed);
    EXPECT_EQ(failed.progress.committed_descriptors, 0u);

    lifecycle.retire(4, 6, 2, true, 1);
    lifecycle.retire(4, 7, 2, true, 1);
    DmaCommandLifecycle::Retirement last =
        lifecycle.retire(4, 8, 2, true, 1);
    EXPECT_FALSE(last.notify);
    EXPECT_FALSE(last.progress.published);
    EXPECT_EQ(last.progress.failed, true);
    EXPECT_EQ(last.progress.committed_descriptors, 3u);
    EXPECT_EQ(last.progress.committed_bytes, 6u);
    EXPECT_TRUE(last.drained);
}

TEST(DmaCommandLifecycleTest, CancelledGroupNeverPublishesLateSuccesses)
{
    DmaCommandLifecycle lifecycle;
    lifecycle.admit(group(6, 4, 4, {11, 12}));
    submitAll(lifecycle, 6);
    EXPECT_FALSE(lifecycle.cancel(6));

    lifecycle.retire(6, 11, 2, true, 1);
    DmaCommandLifecycle::Retirement last = lifecycle.retire(6, 12, 2, true, 1);
    EXPECT_FALSE(last.notify);
    EXPECT_FALSE(last.progress.published);
    EXPECT_TRUE(last.progress.failed);
    EXPECT_EQ(last.progress.committed_descriptors, 2u);
    EXPECT_EQ(last.progress.committed_bytes, 4u);
    EXPECT_TRUE(last.drained);
}

TEST(DmaCommandLifecycleTest, UnknownAndUnsubmittedDescriptorsDoNotPublish)
{
    DmaCommandLifecycle lifecycle;
    lifecycle.admit(group(8, 1, 4, {21, 22}));
    EXPECT_FALSE(lifecycle.retire(99, 21, 2, true, 1).known);

    lifecycle.noteSubmitted(8, 21);
    lifecycle.retire(8, 21, 2, true, 1);
    DmaCommandLifecycle::Retirement never = lifecycle.retire(8, 22, 2, true, 1);
    EXPECT_TRUE(never.known);
    EXPECT_FALSE(never.notify);
    EXPECT_TRUE(lifecycle.cancel(8));
    EXPECT_FALSE(lifecycle.submitting(8, 0));
}

TEST(DmaCommandLifecycleTest, ZeroSubmittedCancellationDrainsImmediately)
{
    DmaCommandLifecycle lifecycle;
    lifecycle.admit(group(10, 1, 4, {31, 32}));
    EXPECT_TRUE(lifecycle.cancel(10));
    EXPECT_FALSE(lifecycle.submitting(10, 0));
    EXPECT_FALSE(lifecycle.admitted(77));
    lifecycle.finish(10);
    EXPECT_TRUE(lifecycle.drained());
}
