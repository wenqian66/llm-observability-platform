from time import perf_counter_ns
def now_ms() -> float:
    return perf_counter_ns() / 1e6
