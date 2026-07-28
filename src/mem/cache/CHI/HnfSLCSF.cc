#include "mem/cache/CHI/HnfSLCSF.hh"

#include <stdexcept>

#include "base/logging.hh"

namespace gem5::Chi
{

namespace
{

enum class RequestPipe : uint8_t
{
    Lookup,
    Fill,
    Update
};

RequestPipe
requestPipe(const SlcSfRequest& request)
{
    return std::visit(
        [](const auto& typed_request) {
            using Request = std::decay_t<decltype(typed_request)>;
            if constexpr (std::is_same_v<Request, SlcSfLookupReq>) {
                return RequestPipe::Lookup;
            } else if constexpr (std::is_same_v<Request, SlcSfFillReq>) {
                return RequestPipe::Fill;
            } else {
                return RequestPipe::Update;
            }
        },
        request);
}

} // anonymous namespace

HnfSLCSF::HnfSLCSF(uint32_t block_size, uint32_t slc_num_sets,
                   uint32_t slc_num_ways, uint32_t sf_num_sets,
                   uint32_t sf_num_ways, uint32_t seq_entries,
                   HnfSLCSFPipelineConfig pipeline_config)
    : HnfSLCSFBackend(block_size, slc_num_sets, slc_num_ways,
                      sf_num_sets, sf_num_ways, seq_entries),
      config(pipeline_config), visibleReqCredits(config.reqQueueEntries)
{
    validateConfig(config);
    assertRequestAccounting();
}

void
HnfSLCSF::validateConfig(const HnfSLCSFPipelineConfig& config)
{
    if (config.reqQueueEntries == 0 || config.respQueueEntries == 0 ||
        config.maxInflight == 0 || config.lookupIssueWidth == 0 ||
        config.fillIssueWidth == 0 || config.updateIssueWidth == 0) {
        throw std::invalid_argument(
            "HnfSLCSF queue sizes, max inflight, and issue widths must be "
            "positive");
    }
    if (config.maxInflight > config.reqQueueEntries) {
        throw std::invalid_argument(
            "HnfSLCSF max inflight exceeds request queue entries");
    }
}

SlcSfEnqueueResult
HnfSLCSF::tryEnqueue(SlcSfRequest&& request)
{
    assertRequestAccounting();
    if (draining) {
        return SlcSfEnqueueResult::Draining;
    }
    if (!initialized) {
        return SlcSfEnqueueResult::Initializing;
    }
    if (visibleReqCredits == 0 ||
        reqOutstanding() == config.reqQueueEntries) {
        return SlcSfEnqueueResult::NoCredit;
    }

    reqIngress.push_back(std::move(request));
    --visibleReqCredits;
    assertRequestAccounting();
    return SlcSfEnqueueResult::Accepted;
}

void
HnfSLCSF::wakeup()
{
    promoteIngressRequests();
    issueReadyRequests();
    updateRegisteredCredits();
    assertRequestAccounting();
}

size_t
HnfSLCSF::reqOutstanding() const
{
    return reqIngress.size() + reqReady.size() + inflightRequests.size();
}

bool
HnfSLCSF::hasWork() const
{
    return reqOutstanding() != 0;
}

void
HnfSLCSF::beginInitialization()
{
    initialized = false;
    visibleReqCredits = 0;
}

void
HnfSLCSF::finishInitialization()
{
    initialized = true;
}

void
HnfSLCSF::beginDraining()
{
    draining = true;
    visibleReqCredits = 0;
}

void
HnfSLCSF::resumeFromDrain()
{
    draining = false;
}

void
HnfSLCSF::promoteIngressRequests()
{
    while (!reqIngress.empty()) {
        reqReady.push_back(std::move(reqIngress.front()));
        reqIngress.pop_front();
    }
}

void
HnfSLCSF::issueReadyRequests()
{
    size_t lookup_issued = 0;
    size_t fill_issued = 0;
    size_t update_issued = 0;

    for (auto request = reqReady.begin();
         request != reqReady.end() &&
         inflightRequests.size() < config.maxInflight;) {
        const RequestPipe pipe = requestPipe(*request);
        size_t* issued = nullptr;
        size_t width = 0;
        switch (pipe) {
          case RequestPipe::Lookup:
            issued = &lookup_issued;
            width = config.lookupIssueWidth;
            break;
          case RequestPipe::Fill:
            issued = &fill_issued;
            width = config.fillIssueWidth;
            break;
          case RequestPipe::Update:
            issued = &update_issued;
            width = config.updateIssueWidth;
            break;
        }

        if (*issued == width) {
            ++request;
            continue;
        }

        inflightRequests.push_back(std::move(*request));
        request = reqReady.erase(request);
        ++*issued;
    }
}

void
HnfSLCSF::updateRegisteredCredits()
{
    if (!initialized || draining) {
        visibleReqCredits = 0;
        return;
    }
    visibleReqCredits = config.reqQueueEntries - reqOutstanding();
}

void
HnfSLCSF::assertRequestAccounting() const
{
    panic_if(reqOutstanding() > config.reqQueueEntries,
             "HnfSLCSF request accounting exceeds capacity (%zu/%zu)\n",
             reqOutstanding(), config.reqQueueEntries);
    panic_if(visibleReqCredits > config.reqQueueEntries - reqOutstanding(),
             "HnfSLCSF visible request credit exceeds free capacity "
             "(%zu/%zu)\n", visibleReqCredits,
             config.reqQueueEntries - reqOutstanding());
}

} // namespace gem5::Chi
