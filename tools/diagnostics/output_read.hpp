#pragma once
#include "bmlib_runtime.h"
#include <algorithm>
#include <cstddef>
#include <limits>

// Preserve all output bytes and their order. Chunking only changes SDK call size.
inline bm_status_t read_output(bm_handle_t handle, void* host, bm_device_mem_t device,
                               size_t bytes, size_t chunk_bytes = 0) {
    if (!host || !bytes || bytes > bm_mem_get_device_size(device) ||
        bytes > std::numeric_limits<unsigned int>::max()) return BM_ERR_PARAM;
    if (!chunk_bytes || chunk_bytes >= bytes)
        return bm_memcpy_d2s_partial(handle, host, device, static_cast<unsigned int>(bytes));
    for (size_t offset = 0; offset < bytes;) {
        const auto count = static_cast<unsigned int>(std::min(chunk_bytes, bytes - offset));
        const auto ret = bm_memcpy_d2s_partial_offset(handle,
            static_cast<unsigned char*>(host) + offset, device, count,
            static_cast<unsigned int>(offset));
        if (ret != BM_SUCCESS) return ret;
        offset += count;
    }
    return BM_SUCCESS;
}
