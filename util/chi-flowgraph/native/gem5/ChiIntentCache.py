from m5.objects.Cache import Cache

class ChiIntentCache(Cache):
    type = 'ChiIntentCache'
    cxx_header = 'mem/cache/CHI/ChiIntentCache.hh'
    cxx_class = 'gem5::Chi::ChiIntentCache'
