//===----------------------------------------------------------------------===//
//
// Copyright (C) 2022 Sophgo Technologies Inc.  All rights reserved.
//
// SOPHON-DEMO is licensed under the 2-Clause BSD License except for the
// third-party components.
//
//===----------------------------------------------------------------------===//

#include "yolov8_det.hpp"
#include "score_gate_plan.hpp"
#include <algorithm>
#include <string>
#include <vector>
#include <cmath>
#include <stdexcept>
#include <sys/file.h>
#include <fcntl.h>
#include <unistd.h>
#include <cerrno>
#include <chrono>

namespace {
void destroy_image(bm_image& image) noexcept {
#if BMCV_VERSION_MAJOR > 1
    bm_image_destroy(&image);
#else
    bm_image_destroy(image);
#endif
    image = bm_image{};
}

struct ScopedImage {
    bm_image image{};
    bool created = false;
    ~ScopedImage() { if (created) destroy_image(image); }
};

class TransferLock {
    int fd_ = -1;
public:
    explicit TransferLock(const std::string& path) {
        if (path.empty()) return;
        fd_ = open(path.c_str(), O_RDWR | O_CREAT | O_CLOEXEC, 0600);
        if (fd_ < 0) throw std::runtime_error("Cannot open transfer gate");
        while (flock(fd_, LOCK_EX) != 0) {
            if (errno == EINTR) continue;
            close(fd_); fd_ = -1;
            throw std::runtime_error("Cannot lock transfer gate");
        }
    }
    ~TransferLock() { if (fd_ >= 0) close(fd_); }
};
}

#define USE_ASPECT_RATIO 1
#define DUMP_FILE 0
#define USE_MULTICLASS_NMS 1

const std::vector<std::vector<int>> colors = {
    {255, 0, 0},     {255, 85, 0},    {255, 170, 0},   {255, 255, 0}, {170, 255, 0}, {85, 255, 0},  {0, 255, 0},
    {0, 255, 85},    {0, 255, 170},   {0, 255, 255},   {0, 170, 255}, {0, 85, 255},  {0, 0, 255},   {85, 0, 255},
    {170, 0, 255},   {255, 0, 255},   {255, 0, 170},   {255, 0, 85},  {255, 0, 0},   {255, 0, 255}, {255, 85, 255},
    {255, 170, 255}, {255, 255, 255}, {170, 255, 255}, {85, 255, 255}};

void YoloV8_det::release_preprocess_buffers() noexcept {
    // Converted images borrow resident_input_owned; detach/destroy their image
    // descriptors before releasing that allocation exactly once. The descriptor
    // passed to BMRuntime is a copy and is never used to decide what to free.
    if (converted_memory_attached) {
#if BMCV_VERSION_MAJOR > 1
        bm_image_detach_contiguous_mem(converted_images_created, converted_images.data());
#else
        bm_image_dettach_contiguous_mem(converted_images_created, converted_images.data());
#endif
        converted_memory_attached = false;
    }
    for (int i = 0; i < converted_images_created; ++i) destroy_image(converted_images[i]);
    converted_images_created = 0;
    converted_images.clear();
    if (input_memory_allocated) {
        bm_free_device(handle, resident_input_owned);
        input_memory_allocated = false;
    }
    resident_input = bm_tensor_t{};
    resident_input_owned = bm_device_mem_t{};
    if (resized_memory_allocated) {
        bm_image_free_contiguous_mem(resized_images_created, resized_images.data());
        resized_memory_allocated = false;
    }
    for (int i = 0; i < resized_images_created; ++i) destroy_image(resized_images[i]);
    resized_images_created = 0;
    resized_images.clear();
    preprocess_buffers_ready = false;
}

void YoloV8_det::prepare_preprocess_buffers() {
    if (preprocess_buffers_ready) return;
    const auto& shape = netinfo->stages[0].input_shapes[0];
    if (netinfo->input_num != 1 || shape.num_dims != 4 || shape.dims[0] != batch_size ||
        shape.dims[1] != 3 || shape.dims[2] != m_net_h || shape.dims[3] != m_net_w ||
        batch_size <= 0 || m_net_h <= 0 || m_net_w <= 0)
        throw std::runtime_error("Preprocess buffers require one fixed NCHW RGB input");
    bm_image_data_format_ext img_dtype;
    switch (netinfo->input_dtypes[0]) {
        case BM_FLOAT32: img_dtype = DATA_TYPE_EXT_FLOAT32; break;
        case BM_INT8: img_dtype = DATA_TYPE_EXT_1N_BYTE_SIGNED; break;
        case BM_UINT8: img_dtype = DATA_TYPE_EXT_1N_BYTE; break;
        default: throw std::runtime_error("Preprocess supports FP32, INT8 or UINT8 input tensors");
    }
    try {
        resized_images.resize(batch_size);
        converted_images.resize(batch_size);
        const int aligned_net_w = FFALIGN(m_net_w, 64);
        int strides[3] = {aligned_net_w, aligned_net_w, aligned_net_w};
        // The dependency's create_batch wrapper ignores individual create
        // errors. Track successfully created descriptors for partial rollback.
        for (int i = 0; i < batch_size; ++i) {
            if (bm_image_create(handle, m_net_h, m_net_w, FORMAT_RGB_PLANAR,
                                DATA_TYPE_EXT_1N_BYTE, &resized_images[i], strides) != BM_SUCCESS)
                throw std::runtime_error("Cannot create persistent resize image");
            ++resized_images_created;
        }
        if (bm_image_alloc_contiguous_mem(batch_size, resized_images.data()) != BM_SUCCESS)
            throw std::runtime_error("Cannot allocate persistent resize images");
        resized_memory_allocated = true;
        for (int i = 0; i < batch_size; ++i) {
            if (bm_image_create(handle, m_net_h, m_net_w, FORMAT_RGB_PLANAR,
                                img_dtype, &converted_images[i]) != BM_SUCCESS)
                throw std::runtime_error("Cannot create persistent conversion image");
            ++converted_images_created;
        }
        if (!bmrt_tensor(&resident_input, bmrt, netinfo->input_dtypes[0], shape))
            throw std::runtime_error("Cannot allocate persistent input tensor");
        resident_input_owned = resident_input.device_mem;
        input_memory_allocated = true;
        if (bm_image_attach_contiguous_mem(batch_size, converted_images.data(), resident_input_owned) != BM_SUCCESS)
            throw std::runtime_error("Cannot attach persistent input tensor to conversion images");
        converted_memory_attached = true;
        preprocess_buffers_ready = true;
    } catch (...) {
        release_preprocess_buffers();
        throw;
    }
}

void YoloV8_det::prepare_output_buffers() {
    if (!reuse_output_buffers || !resident_outputs.empty()) return;
    if (misc_info.pcie_soc_mode != 0 || netinfo->is_dynamic || netinfo->stage_num != 1 ||
        netinfo->input_num != 1 || netinfo->output_num != 1 || batch_size != 1 ||
        netinfo->output_dtypes[0] != BM_FLOAT32 || netinfo->stages[0].output_shapes[0].num_dims != 3)
        throw std::runtime_error("Output reuse requires static PCIe batch-1 model with one FP32 output");
    const auto shape = netinfo->stages[0].output_shapes[0];
    const int count = bmrt_shape_count(&shape);
    if (count <= 0) throw std::runtime_error("Invalid output buffer size");
    host_output_cache.resize(count);
    ++host_output_allocations;
    resident_outputs.resize(1);
    if (!bmrt_tensor(&resident_outputs[0], bmrt, BM_FLOAT32, shape)) {
        resident_outputs.clear();
        throw std::runtime_error("Cannot allocate persistent output tensor");
    }
    ++device_output_allocations;
}

void YoloV8_det::prepare_score_gate() {
    if (!score_gate_enabled || gate_output_allocated) return;
    const auto& shape = netinfo->stages[0].output_shapes[0];
    if (misc_info.pcie_soc_mode != 0 || netinfo->is_dynamic || netinfo->stage_num != 1 ||
        network_names.size() != 1 || netinfo->input_num != 1 || netinfo->output_num != 1 || batch_size != 1 ||
        netinfo->output_dtypes[0] != BM_FLOAT32 || shape.num_dims != 3 || shape.dims[0] != 1 ||
        shape.dims[1] != score_gate::rows || shape.dims[2] != score_gate::stride ||
        !is_output_transposed || m_class_num != score_gate::classes || !std::isfinite(m_confThreshold) || m_confThreshold <= 0)
        throw std::runtime_error("Score gate requires static PCIe batch-1 single FP32 [1,8400,84] output and positive confidence");
    if (score_gate_model.empty() || access(score_gate_model.c_str(), R_OK))
        throw std::runtime_error("A readable score gate auxiliary model is required when the gate is on");
    gate_runtime = bmrt_create(handle);
    if (!gate_runtime || !bmrt_load_bmodel(gate_runtime, score_gate_model.c_str()))
        throw std::runtime_error("Cannot load score gate auxiliary model");
    if (bmrt_get_network_number(gate_runtime) != 1)
        throw std::runtime_error("Score gate auxiliary model must contain one network");
    const char** gate_names = nullptr;
    bmrt_get_network_names(gate_runtime, &gate_names);
    if (!gate_names || !gate_names[0]) throw std::runtime_error("Score gate model has no network name");
    gate_network_name = gate_names[0]; free(gate_names);
    gate_netinfo = bmrt_get_network_info(gate_runtime, gate_network_name.c_str());
    if (!gate_netinfo || gate_netinfo->is_dynamic || gate_netinfo->stage_num != 1 ||
        gate_netinfo->input_num != 1 || gate_netinfo->output_num != 1 ||
        gate_netinfo->input_dtypes[0] != BM_FLOAT32 || gate_netinfo->output_dtypes[0] != BM_FLOAT32)
        throw std::runtime_error("Score gate auxiliary model must be static single-stage with one FP32 input/output");
    const auto& gate_input_shape = gate_netinfo->stages[0].input_shapes[0];
    const auto& gate_output_shape = gate_netinfo->stages[0].output_shapes[0];
    if (gate_input_shape.num_dims != 3 || gate_input_shape.dims[0] != 1 || gate_input_shape.dims[1] != score_gate::rows ||
        gate_input_shape.dims[2] != score_gate::stride || gate_output_shape.num_dims != 2 ||
        gate_output_shape.dims[0] != 1 || gate_output_shape.dims[1] != score_gate::rows)
        throw std::runtime_error("Score gate auxiliary shapes must be [1,8400,84] -> [1,8400]");
    gate_host_maxima.resize(score_gate::rows);
    gate_candidate_rows.reserve(score_gate::rows);
    if (!bmrt_tensor(&gate_output, gate_runtime, BM_FLOAT32, gate_output_shape))
        throw std::runtime_error("Cannot allocate resident score gate output");
    gate_output_owned = gate_output.device_mem;
    gate_output_allocated = true; ++score_gate_buffer_allocations;
}

bm_status_t YoloV8_det::read_score_gated_output(bm_tensor_t* tensor, float* host) {
    auto& metrics = score_gate_metrics;
    if (!gate_output_allocated || tensor->dtype != BM_FLOAT32 || tensor->st_mode != BM_STORE_1N ||
        !bmrt_shape_is_same(&tensor->shape, &gate_netinfo->stages[0].input_shapes[0]) || bmrt_tensor_bytesize(tensor) != score_gate::full_bytes ||
        bm_mem_get_device_size(tensor->device_mem) < score_gate::full_bytes)
        return BM_ERR_PARAM;
    const auto owned_output_is_intact = [&]() {
        return gate_output.dtype == BM_FLOAT32 && gate_output.st_mode == BM_STORE_1N &&
            bmrt_shape_is_same(&gate_output.shape, &gate_netinfo->stages[0].output_shapes[0]) &&
            bmrt_tensor_bytesize(&gate_output) == score_gate::score_bytes &&
            bm_mem_get_device_addr(gate_output.device_mem) == bm_mem_get_device_addr(gate_output_owned) &&
            bm_mem_get_device_size(gate_output.device_mem) == bm_mem_get_device_size(gate_output_owned) &&
            bm_mem_get_device_size(gate_output_owned) >= score_gate::score_bytes;
    };
    if (!owned_output_is_intact()) return BM_ERR_PARAM;
    const auto duration_ms = [](std::chrono::steady_clock::time_point a, std::chrono::steady_clock::time_point b) {
        return std::chrono::duration<double, std::milli>(b - a).count();
    };
    const auto fallback = [&](const std::string& reason) {
        gate_candidate_rows_valid = false;
        gate_candidate_rows.clear();
        metrics.fallback = true; metrics.fallback_reason = reason;
        const auto start = std::chrono::steady_clock::now();
        ++metrics.full_calls;
        const auto ret = bm_memcpy_d2s_partial(handle, host, tensor->device_mem, score_gate::full_bytes);
        metrics.full_ms += duration_ms(start, std::chrono::steady_clock::now());
        if (ret == BM_SUCCESS) metrics.full_bytes += score_gate::full_bytes;
        return ret;
    };
    // Only the tensor descriptor is copied. Main output pixels remain on the
    // device; gate_input owns no allocation and is NEVER freed here. The
    // auxiliary model is responsible for slicing columns 4:84 then ReduceMax.
    bm_tensor_t gate_input = *tensor;
    const auto borrowed_input_address = bm_mem_get_device_addr(tensor->device_mem);
    const auto borrowed_input_size = bm_mem_get_device_size(tensor->device_mem);
    auto start = std::chrono::steady_clock::now();
    ++metrics.kernel_calls;
    const bool launched = bmrt_launch_tensor_ex(gate_runtime, gate_network_name.c_str(), &gate_input, 1,
                                                &gate_output, 1, true, false);
    const auto submitted = std::chrono::steady_clock::now();
    if (!launched) return BM_ERR_FAILURE;
    auto ret = bm_thread_sync(handle);
    const auto synced = std::chrono::steady_clock::now();
    metrics.submit_ms = duration_ms(start, submitted);
    metrics.sync_ms = duration_ms(submitted, synced);
    metrics.kernel_ms = duration_ms(start, synced);
    if (ret != BM_SUCCESS) return ret; // SDK failures are not a filtering fallback.
    if (!owned_output_is_intact() || gate_input.dtype != BM_FLOAT32 || gate_input.st_mode != BM_STORE_1N ||
        !bmrt_shape_is_same(&gate_input.shape, &tensor->shape) ||
        bm_mem_get_device_addr(gate_input.device_mem) != borrowed_input_address ||
        bm_mem_get_device_size(gate_input.device_mem) != borrowed_input_size)
        return BM_ERR_PARAM;
    start = std::chrono::steady_clock::now(); ++metrics.score_calls;
    ret = bm_memcpy_d2s_partial(handle, gate_host_maxima.data(), gate_output.device_mem, score_gate::score_bytes);
    metrics.score_ms = duration_ms(start, std::chrono::steady_clock::now());
    if (ret != BM_SUCCESS) return ret;
    metrics.score_bytes = score_gate::score_bytes;
    const auto plan = score_gate::make_plan(gate_host_maxima, m_confThreshold, score_gate_merge_budget_kib * 1024);
    metrics.selected_rows = plan.selected_rows; metrics.ranges = plan.ranges.size();
    metrics.planned_read_ranges = plan.read_ranges.size();
    if (!plan.fallback.empty()) return fallback(plan.fallback);
    if (!score_gate_sparse_cpu) {
        start = std::chrono::steady_clock::now();
        std::fill(host, host + score_gate::rows * score_gate::stride, 0.0f);
        metrics.host_zero_ms = duration_ms(start, std::chrono::steady_clock::now());
    }
    std::size_t candidate_range = 0;
    for (const auto& range : plan.read_ranges) {
        const auto first = range.first * score_gate::stride;
        const auto elements = range.second * score_gate::stride;
        const auto bytes = elements * sizeof(float);
        start = std::chrono::steady_clock::now(); ++metrics.row_calls;
        ret = bm_memcpy_d2s_partial_offset(handle, host + first, tensor->device_mem, bytes, first * sizeof(float));
        metrics.row_ms += duration_ms(start, std::chrono::steady_clock::now());
        if (ret != BM_SUCCESS) return ret;
        metrics.row_bytes += bytes;
        metrics.copied_rows += range.second;
        std::size_t selected_rows = 0;
        while (candidate_range < plan.ranges.size() && plan.ranges[candidate_range].first < range.first + range.second)
            selected_rows += plan.ranges[candidate_range++].second;
        metrics.extra_row_bytes += (range.second - selected_rows) * score_gate::stride * sizeof(float);
    }
    if (!score_gate::prepare_host_candidates(plan, host, score_gate_sparse_cpu ? &gate_candidate_rows : nullptr))
        return fallback("nonfinite_selected_row");
    // Publish the sparse view only after EVERY selected row was read successfully.
    // Unselected host memory is unspecified and must never be scanned in this mode.
    gate_candidate_rows_valid = score_gate_sparse_cpu;
    return BM_SUCCESS;
}

int YoloV8_det::Detect(const std::vector<bm_image>& input_images, std::vector<YoloV8BoxVec>& boxes) {
    if (input_images.empty() || input_images.size() > static_cast<size_t>(batch_size))
        throw std::runtime_error("Detect requires between 1 and model batch_size input images");
    prepare_output_buffers();
    prepare_score_gate();
    int ret = 0;
    bm_tensor_t input_tensor{};
    std::vector<bm_tensor_t> output_tensors;
    output_tensors.resize(netinfo->output_num);
    std::vector<std::pair<int, int>> txy_batch;
    std::vector<std::pair<float, float>> ratios_batch;
    m_ts->save("yolov8 preprocess", input_images.size());
    ret = pre_process(input_images, input_tensor, txy_batch, ratios_batch);
    assert(ret == 0);
    m_ts->save("yolov8 preprocess", input_images.size());

    m_ts->save("yolov8 inference", input_images.size());
    ret = forward(input_tensor, output_tensors);
    assert(ret == 0);
    m_ts->save("yolov8 inference", input_images.size());

    m_ts->save("yolov8 postprocess", input_images.size());
    ret = post_process(input_images, output_tensors, txy_batch, ratios_batch, boxes);
    assert(ret == 0);
    m_ts->save("yolov8 postprocess", input_images.size());
    return ret;
}

float YoloV8_det::get_aspect_scaled_ratio(int src_w, int src_h, int dst_w, int dst_h, bool* pIsAligWidth) {
    float ratio;
    float r_w = (float)dst_w / src_w;
    float r_h = (float)dst_h / src_h;
    if (r_h > r_w) {
        *pIsAligWidth = true;
        ratio = r_w;
    } else {
        *pIsAligWidth = false;
        ratio = r_h;
    }
    return ratio;
}

int YoloV8_det::pre_process(const std::vector<bm_image>& images,
                            bm_tensor_t& input_tensor,
                            std::vector<std::pair<int, int>>& txy_batch,
                            std::vector<std::pair<float, float>>& ratios_batch) {
    int ret = 0;
    prepare_preprocess_buffers();
    if (batch_size != static_cast<int>(resized_images.size()))
        throw std::runtime_error("Model batch size changed after preprocessing buffer allocation");

    int image_n = images.size();
    // 1. resize image letterbox
    for (int i = 0; i < image_n; ++i) {
        bm_image image1 = images[i];
        bm_image image_aligned = image1;
        ScopedImage aligned_copy;
        bool need_copy = image1.width & (64 - 1);
        if (need_copy) {
            int stride1[3], stride2[3];
            if (bm_image_get_stride(image1, stride1) != BM_SUCCESS)
                throw std::runtime_error("Cannot read source image stride");
            stride2[0] = FFALIGN(stride1[0], 64);
            stride2[1] = FFALIGN(stride1[1], 64);
            stride2[2] = FFALIGN(stride1[2], 64);
            if (bm_image_create(handle, image1.height, image1.width, image1.image_format, image1.data_type,
                                &aligned_copy.image, stride2) != BM_SUCCESS)
                throw std::runtime_error("Cannot create aligned source image");
            aligned_copy.created = true;
            image_aligned = aligned_copy.image;
            if (bm_image_alloc_dev_mem(image_aligned, BMCV_IMAGE_FOR_IN) != BM_SUCCESS)
                throw std::runtime_error("Cannot allocate aligned source image");
            bmcv_copy_to_atrr_t copyToAttr;
            memset(&copyToAttr, 0, sizeof(copyToAttr));
            copyToAttr.start_x = 0;
            copyToAttr.start_y = 0;
            copyToAttr.if_padding = 1;
            if (bmcv_image_copy_to(handle, copyToAttr, image1, image_aligned) != BM_SUCCESS)
                throw std::runtime_error("Cannot copy source image to aligned image");
        }
#if USE_ASPECT_RATIO
        bool isAlignWidth = false;
        float ratio = get_aspect_scaled_ratio(images[i].width, images[i].height, m_net_w, m_net_h, &isAlignWidth);
        int tx1 = 0, ty1 = 0;
        bmcv_padding_atrr_t padding_attr;
        memset(&padding_attr, 0, sizeof(padding_attr));
        padding_attr.dst_crop_sty = 0;
        padding_attr.dst_crop_stx = 0;
        padding_attr.padding_b = 114;
        padding_attr.padding_g = 114;
        padding_attr.padding_r = 114;
        padding_attr.if_memset = 1;
        if (isAlignWidth) {
            padding_attr.dst_crop_h = images[i].height * ratio;
            padding_attr.dst_crop_w = m_net_w;

            ty1 = (int)((m_net_h - padding_attr.dst_crop_h) / 2);  // padding 大小
            padding_attr.dst_crop_sty = ty1;
            padding_attr.dst_crop_stx = 0;
        } else {
            padding_attr.dst_crop_h = m_net_h;
            padding_attr.dst_crop_w = images[i].width * ratio;

            tx1 = (int)((m_net_w - padding_attr.dst_crop_w) / 2);
            padding_attr.dst_crop_sty = 0;
            padding_attr.dst_crop_stx = tx1;
        }
        txy_batch.push_back(std::make_pair(tx1, ty1));
        ratios_batch.push_back(std::make_pair(ratio, ratio));
        bmcv_rect_t crop_rect{0, 0, image1.width, image1.height};
        bm_status_t ret;
        if (preprocess_csc >= 0) {
            int count = 1;
            ret = bmcv_image_vpp_basic(handle, 1, &image_aligned, &resized_images[i], &count,
                &crop_rect, &padding_attr, BMCV_INTER_LINEAR, static_cast<csc_type_t>(preprocess_csc), nullptr);
        } else {
            ret = bmcv_image_vpp_convert_padding(handle, 1, image_aligned, &resized_images[i],
                &padding_attr, &crop_rect);
        }
#else
        bm_status_t ret;
        if (preprocess_csc >= 0) {
            int count = 1;
            bmcv_rect_t crop_rect{0, 0, image_aligned.width, image_aligned.height};
            ret = bmcv_image_vpp_basic(handle, 1, &image_aligned, &resized_images[i], &count,
                &crop_rect, nullptr, BMCV_INTER_LINEAR, static_cast<csc_type_t>(preprocess_csc), nullptr);
        } else ret = bmcv_image_vpp_convert(handle, 1, images[i], &resized_images[i]);
        txy_batch.push_back(std::make_pair(0, 0));
        ratios_batch.push_back(std::make_pair((float)m_net_w/images[i].width,(float)m_net_h/images[i].height));
#endif
        if (ret != BM_SUCCESS) {
            throw std::runtime_error("BMRuntime 操作失败");
        }
    }

    // 2. converto img /= 255
    ret = bmcv_image_convert_to(handle, image_n, converto_attr, resized_images.data(),
                                converted_images.data());
    if (ret != BM_SUCCESS) throw std::runtime_error("BMRuntime image conversion failed");
    // All valid batch entries are overwritten before launch; post_process still
    // consumes only image_n outputs. No resized/converted image is retained from
    // a caller, and the input tensor remains owned by this detector instance.
    input_tensor = resident_input;

    return 0;
}

int YoloV8_det::forward(bm_tensor_t& input_tensor, std::vector<bm_tensor_t>& output_tensors){
    // static int count = 0;
    // std::ifstream input_data("../../python/dummy_inputs/"+std::to_string(count++)+".bin", std::ios::binary);
    // static float *input = new float[3*1024*1024];
    // input_data.read((char*)input, 3*1024*1024*sizeof(float));
    // bm_memcpy_s2d(handle, input_tensor.device_mem, input);

    const auto submitted_begin = std::chrono::steady_clock::now();
    bool ok;
    if (reuse_output_buffers) {
        output_tensors = resident_outputs;
        ok = bmrt_launch_tensor_ex(bmrt, netinfo->name, &input_tensor, netinfo->input_num,
                                  output_tensors.data(), netinfo->output_num, true, false);
    } else {
        ok = bmrt_launch_tensor(bmrt, netinfo->name, &input_tensor, netinfo->input_num,
                               output_tensors.data(), netinfo->output_num);
        if (ok) device_output_allocations += netinfo->output_num;
    }
    const auto submitted_end = std::chrono::steady_clock::now();
    if (!ok) throw std::runtime_error("BMRuntime launch failed");
    auto ret = bm_thread_sync(handle);
    const auto synced = std::chrono::steady_clock::now();
    if (ret != BM_SUCCESS) {
        throw std::runtime_error("BMRuntime 操作失败");
    }
    if (input_tensor.dtype != resident_input.dtype || input_tensor.st_mode != resident_input.st_mode ||
        !bmrt_shape_is_same(&input_tensor.shape, &resident_input.shape) ||
        bm_mem_get_device_addr(input_tensor.device_mem) != bm_mem_get_device_addr(resident_input_owned) ||
        bm_mem_get_device_size(input_tensor.device_mem) != bm_mem_get_device_size(resident_input_owned))
        throw std::runtime_error("BMRuntime changed the borrowed persistent input descriptor");
    inference_submit_ms = std::chrono::duration<double, std::milli>(submitted_end - submitted_begin).count();
    inference_sync_ms = std::chrono::duration<double, std::milli>(synced - submitted_end).count();
    input_release_ms = 0; // Persistent input storage is released at detector destruction.
    return 0;
}

/**
 * @name    get_cpu_data
 * @brief   get cpu data of tensor.
 *
 * @param   [in]           tensor   input tensor.
 * @param   [in]           scale    scale of tensor.
 * @retval  float*         tensor's cpu data.
 */
float* YoloV8_det::get_cpu_data(bm_tensor_t* tensor, float scale){
    score_gate_metrics = ScoreGateMetrics{};
    gate_candidate_rows_valid = false;
    gate_candidate_rows.clear();
    int ret = 0;
    float *pFP32 = NULL;
    int count = bmrt_shape_count(&tensor->shape);
    output_allocation_ms = output_copy_ms = -1;
    if(misc_info.pcie_soc_mode == 1){ //soc
        if (tensor->dtype == BM_FLOAT32) {
            unsigned long long addr;
            ret = bm_mem_mmap_device_mem(handle, &tensor->device_mem, &addr);
            if (ret != BM_SUCCESS) {
        throw std::runtime_error("BMRuntime 操作失败");
    }
            ret = bm_mem_invalidate_device_mem(handle, &tensor->device_mem);
            if (ret != BM_SUCCESS) {
        throw std::runtime_error("BMRuntime 操作失败");
    }
            pFP32 = (float*)addr;
        } else if (BM_INT8 == tensor->dtype) {
            int8_t * pI8 = nullptr;
            unsigned long long  addr;
            ret = bm_mem_mmap_device_mem(handle, &tensor->device_mem, &addr);
            if (ret != BM_SUCCESS) {
        throw std::runtime_error("BMRuntime 操作失败");
    }
            ret = bm_mem_invalidate_device_mem(handle, &tensor->device_mem);
            if (ret != BM_SUCCESS) {
        throw std::runtime_error("BMRuntime 操作失败");
    }
            pI8 = (int8_t*)addr;
            // dtype convert
            pFP32 = new float[count];
            assert(pFP32 != nullptr);
            for(int i = 0; i < count; ++i) {
                pFP32[i] = pI8[i] * scale;
            }
            ret = bm_mem_unmap_device_mem(handle, pI8, bm_mem_get_device_size(tensor->device_mem));
            if (ret != BM_SUCCESS) {
        throw std::runtime_error("BMRuntime 操作失败");
    }
        }  else if (BM_UINT8 == tensor->dtype) {
            uint8_t * pUI8 = nullptr;
            unsigned long long  addr;
            ret = bm_mem_mmap_device_mem(handle, &tensor->device_mem, &addr);
            if (ret != BM_SUCCESS) {
        throw std::runtime_error("BMRuntime 操作失败");
    }
            ret = bm_mem_invalidate_device_mem(handle, &tensor->device_mem);
            if (ret != BM_SUCCESS) {
        throw std::runtime_error("BMRuntime 操作失败");
    }
            pUI8 = (uint8_t*)addr;
            // dtype convert
            pFP32 = new float[count];
            assert(pFP32 != nullptr);
            for(int i = 0; i < count; ++i) {
                pFP32[i] = pUI8[i] * scale;
            }
            ret = bm_mem_unmap_device_mem(handle, pUI8, bm_mem_get_device_size(tensor->device_mem));
            if (ret != BM_SUCCESS) {
        throw std::runtime_error("BMRuntime 操作失败");
    }
        } else{
            std::cerr << "unsupport dtype: " << tensor->dtype << std::endl;
        }
    } else { //pcie
        if (tensor->dtype == BM_FLOAT32) {
            const auto begin = std::chrono::steady_clock::now();
            if (reuse_output_buffers) {
                if (count != static_cast<int>(host_output_cache.size()))
                    throw std::runtime_error("Output shape changed; refusing undersized buffer");
                pFP32 = host_output_cache.data();
            } else {
                pFP32 = new float[count];
                ++host_output_allocations;
            }
            const auto allocated = std::chrono::steady_clock::now();
            if (score_gate_enabled) {
                ret = read_score_gated_output(tensor, pFP32);
            } else {
                ret = bm_memcpy_d2s_partial(handle, pFP32, tensor->device_mem, count * sizeof(float));
                score_gate_metrics.full_calls = 1;
                if (ret == BM_SUCCESS) score_gate_metrics.full_bytes = count * sizeof(float);
            }
            const auto copied = std::chrono::steady_clock::now();
            output_allocation_ms = std::chrono::duration<double, std::milli>(allocated - begin).count();
            output_copy_ms = std::chrono::duration<double, std::milli>(copied - allocated).count();
            if (!score_gate_enabled) score_gate_metrics.full_ms = output_copy_ms;
            if (ret != BM_SUCCESS) {
                if (!reuse_output_buffers) delete[] pFP32;
                throw std::runtime_error("Output device-to-host copy failed");
            }
            output_copy_bytes += score_gate_metrics.total_bytes();
        } else if (BM_INT8 == tensor->dtype) {
            int8_t * pI8 = nullptr;
            int tensor_size = bmrt_tensor_bytesize(tensor);
            pI8 = new int8_t[tensor_size];
            assert(pI8 != nullptr);
            // dtype convert
            pFP32 = new float[count];
            assert(pFP32 != nullptr);
            ret = bm_memcpy_d2s_partial(handle, pI8, tensor->device_mem, tensor_size);
            assert(BM_SUCCESS ==ret);
            for(int i = 0;i < count; ++ i) {
                pFP32[i] = pI8[i] * scale;
            }
            delete [] pI8;
        }  else if (BM_UINT8 == tensor->dtype) {
            uint8_t * pUI8 = nullptr;
            int tensor_size = bmrt_tensor_bytesize(tensor);
            pUI8 = new uint8_t[tensor_size];
            assert(pUI8 != nullptr);
            // dtype convert
            pFP32 = new float[count];
            assert(pFP32 != nullptr);
            ret = bm_memcpy_d2s_partial(handle, pUI8, tensor->device_mem, tensor_size);
            assert(BM_SUCCESS ==ret);
            for(int i = 0;i < count; ++ i) {
                pFP32[i] = pUI8[i] * scale;
            }
            delete [] pUI8;
        }else{
            std::cerr << "unsupport dtype: " << tensor->dtype << std::endl;
        }
    }
    return pFP32;
}


int YoloV8_det::post_process(const std::vector<bm_image>& input_images,
                             std::vector<bm_tensor_t>& output_tensors,
                             const std::vector<std::pair<int, int>>& txy_batch,
                             const std::vector<std::pair<float, float>>& ratios_batch,
                             std::vector<YoloV8BoxVec>& detected_boxes) {
    m_ts->save("yolov8 transfer wait", input_images.size());
    float* data_box = NULL;
    bm_tensor_t tensor_box;
    {
    TransferLock gate(transfer_lock_path);
    m_ts->save("yolov8 transfer wait", input_images.size());
    m_ts->save("yolov8 output transfer", input_images.size());
    for(int i = 0; i < output_tensors.size(); i++) {
        if(output_tensors[i].shape.num_dims == 3){
            tensor_box = output_tensors[i];
            data_box = get_cpu_data(&output_tensors[i], netinfo->output_scales[i]);
        }
    }

    m_ts->save("yolov8 output transfer", input_images.size());
    } // Release gate before CPU postprocessing.
    m_ts->save("yolov8 cpu postprocess", input_images.size());
    for (int batch_idx = 0; batch_idx < input_images.size(); ++batch_idx) {
        YoloV8BoxVec yolobox_vec;
        auto& frame = input_images[batch_idx];
        int frame_width = frame.width;
        int frame_height = frame.height;

        int box_num = is_output_transposed ? tensor_box.shape.dims[1] : tensor_box.shape.dims[2];
        int nout = is_output_transposed ? tensor_box.shape.dims[2] : tensor_box.shape.dims[1];
        float* batch_data_box =  data_box + batch_idx * box_num * nout; //output_tensor: [bs, box_num, class_num + 5]
        int offset = is_output_transposed ? 1 : box_num;

        // Candidates
        if (gate_candidate_rows_valid && (batch_size != 1 || batch_idx != 0 || !is_output_transposed ||
                                         box_num != score_gate::rows || nout != score_gate::stride))
            throw std::runtime_error("Sparse CPU candidate view requires the validated single-image output");
        const int candidate_count = gate_candidate_rows_valid ? static_cast<int>(gate_candidate_rows.size()) : box_num;
        for (int candidate = 0; candidate < candidate_count; ++candidate) {
            const int i = gate_candidate_rows_valid ? gate_candidate_rows[candidate] : candidate;
            int box_index = is_output_transposed ? i * nout : i;
            //transposed output_tensor's last dim: [x, y, w, h, cls_conf0, ..., cls_conf14, rotate_angle]
            float* cls_conf = batch_data_box + box_index + 4 * offset;
#if USE_MULTICLASS_NMS
            // multilabel
            for (int j = 0; j < m_class_num; j++) {
                float cur_value = cls_conf[j * offset];
                if (cur_value > m_confThreshold) {
                    YoloV8Box box;
                    box.score = cur_value;
                    box.class_id = j;
                    float centerX = batch_data_box[box_index];
                    float centerY = batch_data_box[box_index + 1 * offset];
                    float width = batch_data_box[box_index + 2 * offset];
                    float height = batch_data_box[box_index + 3 * offset];

                    int c = agnostic ? 0 : box.class_id * max_wh;
                    box.x1 = centerX - width / 2 + c;
                    box.y1 = centerY - height / 2 + c;
                    box.x2 = box.x1 + width;
                    box.y2 = box.y1 + height;
                    yolobox_vec.push_back(box);
                }
            }
#else
            // best class
            YoloV8Box box;
            if(is_output_transposed){
                box.class_id = argmax(batch_data_box + box_index + 4, m_class_num);
                box.score = batch_data_box[box_index + 4 + box.class_id];
            }else {
                float max_value = 0.0;
                int max_index = 0;
                for(int j = 0; j < m_class_num; j++){
                    float cur_value = cls_conf[i + j * box_num];
                    if(cur_value > max_value){
                        max_value = cur_value;
                        max_index = j;
                    }
                }
                box.class_id = max_index;
                box.score = max_value;
            }

            if(box.score <= m_confThreshold){
                continue;
            }
            int c = agnostic ? 0 : box.class_id * max_wh;
            float centerX = batch_data_box[box_index];
            float centerY = batch_data_box[box_index + 1 * offset];
            float width = batch_data_box[box_index + 2 * offset];
            float height = batch_data_box[box_index + 3 * offset];
            box.x1 = centerX - width / 2 + c;
            box.y1 = centerY - height / 2 + c;
            box.x2 = box.x1 + width;
            box.y2 = box.y1 + height;
            yolobox_vec.push_back(box);
#endif
        }
        NMS(yolobox_vec, m_nmsThreshold);

        if (yolobox_vec.size() > max_det) {
            yolobox_vec.erase(yolobox_vec.begin(), yolobox_vec.begin() + (yolobox_vec.size() - max_det));
        }

        if(!agnostic){
            for (int i = 0; i < yolobox_vec.size(); i++) {
                int c = yolobox_vec[i].class_id * max_wh;
                yolobox_vec[i].x1 = yolobox_vec[i].x1 - c;
                yolobox_vec[i].y1 = yolobox_vec[i].y1 - c;
                yolobox_vec[i].x2 = yolobox_vec[i].x2 - c;
                yolobox_vec[i].y2 = yolobox_vec[i].y2 - c;
            }
        }

        int tx1 = txy_batch[batch_idx].first;
        int ty1 = txy_batch[batch_idx].second;
        float ratio_x = ratios_batch[batch_idx].first;
        float ratio_y = ratios_batch[batch_idx].second;
        float inv_ratio_x = 1.0 / ratio_x;
        float inv_ratio_y = 1.0 / ratio_y;
        for (int i = 0; i < yolobox_vec.size(); i++) {
            yolobox_vec[i].x1 = std::round((yolobox_vec[i].x1 - tx1) * inv_ratio_x);
            yolobox_vec[i].y1 = std::round((yolobox_vec[i].y1 - ty1) * inv_ratio_y);
            yolobox_vec[i].x2 = std::round((yolobox_vec[i].x2 - tx1) * inv_ratio_x);
            yolobox_vec[i].y2 = std::round((yolobox_vec[i].y2 - ty1) * inv_ratio_y);
        }
        clip_boxes(yolobox_vec, frame_width, frame_height);
        detected_boxes.push_back(yolobox_vec);
    }

    m_ts->save("yolov8 cpu postprocess", input_images.size());
    for(int i = 0; i < output_tensors.size(); i++) {
        float* tensor_data = NULL;
        if(output_tensors[i].shape.num_dims == 3){
            tensor_data = data_box;
        }

        if(misc_info.pcie_soc_mode == 1){ // soc
            if(output_tensors[i].dtype != BM_FLOAT32){
                delete [] tensor_data;
            } else {
                int tensor_size = bm_mem_get_device_size(output_tensors[i].device_mem);
                bm_status_t ret = bm_mem_unmap_device_mem(handle, tensor_data, tensor_size);
                if (ret != BM_SUCCESS) {
        throw std::runtime_error("BMRuntime 操作失败");
    }
            }
        } else if (!reuse_output_buffers) {
            delete [] tensor_data;
        }
        if (!reuse_output_buffers) bm_free_device(handle, output_tensors[i].device_mem);
    }
    return 0;
}

int YoloV8_det::argmax(float* data, int num) {
    float max_value = 0.0;
    int max_index = 0;
    for (int i = 0; i < num; ++i) {
        float value = data[i];
        if (value > max_value) {
            max_value = value;
            max_index = i;
        }
    }

    return max_index;
}

void YoloV8_det::clip_boxes(YoloV8BoxVec& yolobox_vec, int src_w, int src_h) {
    for (int i = 0; i < yolobox_vec.size(); i++) {
        yolobox_vec[i].x1 = std::max((float)0.0, std::min(yolobox_vec[i].x1, (float)src_w));
        yolobox_vec[i].y1 = std::max((float)0.0, std::min(yolobox_vec[i].y1, (float)src_h));
        yolobox_vec[i].x2 = std::max((float)0.0, std::min(yolobox_vec[i].x2, (float)src_w));
        yolobox_vec[i].y2 = std::max((float)0.0, std::min(yolobox_vec[i].y2, (float)src_h));
    }
}

void YoloV8_det::xywh2xyxy(YoloV8BoxVec& xyxyboxes, std::vector<std::vector<float>> box) {
    for (int i = 0; i < box.size(); i++) {
        YoloV8Box tmpbox;
        tmpbox.x1 = box[i][0] - box[i][2] / 2;
        tmpbox.y1 = box[i][1] - box[i][3] / 2;
        tmpbox.x2 = box[i][0] + box[i][2] / 2;
        tmpbox.y2 = box[i][1] + box[i][3] / 2;
        xyxyboxes.push_back(tmpbox);
    }
}

void YoloV8_det::NMS(YoloV8BoxVec& dets, float nmsConfidence) {
    int length = dets.size();
    int index = length - 1;

    std::sort(dets.begin(), dets.end(), [](const YoloV8Box& a, const YoloV8Box& b) { return a.score < b.score; });

    std::vector<float> areas(length);
    for (int i = 0; i < length; i++) {
        float width = dets[i].x2 - dets[i].x1;
        float height = dets[i].y2 - dets[i].y1;
        areas[i] = width * height;
    }

    while (index > 0) {
        int i = 0;
        while (i < index) {
            float left = std::max(dets[index].x1, dets[i].x1);
            float top = std::max(dets[index].y1, dets[i].y1);
            float right = std::min(dets[index].x2, dets[i].x2);
            float bottom = std::min(dets[index].y2, dets[i].y2);
            float overlap = std::max(0.0f, right - left) * std::max(0.0f, bottom - top);
            if (overlap / (areas[index] + areas[i] - overlap) > nmsConfidence) {
                areas.erase(areas.begin() + i);
                dets.erase(dets.begin() + i);
                index--;
            } else {
                i++;
            }
        }
        index--;
    }
}

void YoloV8_det::draw_result(cv::Mat& img, YoloV8BoxVec& result) {
    for (int i = 0; i < result.size(); i++) {
        if(result[i].score < 0.25) continue;
        int left, top;
        left = result[i].x1;
        top = result[i].y1;
        int color_num = i;
        cv::Scalar color(colors[result[i].class_id % 25][0], colors[result[i].class_id % 25][1],
                         colors[result[i].class_id % 25][2]);
        cv::Rect bound = {result[i].x1, result[i].y1, result[i].x2 - result[i].x1, result[i].y2 - result[i].y1};

        rectangle(img, bound, color, 2);
        std::string label = std::string(m_class_names[result[i].class_id]) + std::to_string(result[i].score);
        putText(img, label, cv::Point(left, top), cv::FONT_HERSHEY_SIMPLEX, 1, color, 2);
    }
}
