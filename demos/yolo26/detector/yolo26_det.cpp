// Device image handling is adapted from Sophgo SOPHON-DEMO (BSD-2-Clause).
#include "yolo26_det.hpp"
#include <chrono>
#include <cstring>
#include <fstream>
#include <stdexcept>

namespace {
using Timer = std::chrono::steady_clock;
double ms(Timer::time_point a, Timer::time_point b) {
    return std::chrono::duration<double, std::milli>(b - a).count();
}
void check(bm_status_t status, const char* operation) {
    if (status != BM_SUCCESS) throw std::runtime_error(std::string(operation) + " failed, status=" + std::to_string(status));
}
void binary(const std::string& path, const void* data, size_t bytes) {
    std::ofstream file(path, std::ios::binary);
    file.exceptions(std::ios::failbit | std::ios::badbit);
    file.write(static_cast<const char*>(data), bytes);
}
}

Yolo26_det::Yolo26_det(const std::string& model, const std::string& names, int device, float conf)
    : confidence(conf) {
    try {
        std::ifstream class_file(names);
        if (!class_file) throw std::runtime_error("Cannot open class names");
        for (std::string line; std::getline(class_file, line);) {
            if (!line.empty() && line.back() == '\r') line.pop_back();
            if (line.empty()) throw std::runtime_error("Blank class-name line");
            ++class_count;
        }
        if (!class_count) throw std::runtime_error("Empty class names");
        check(bm_dev_request(&handle, device), "bm_dev_request");
        runtime = bmrt_create(handle);
        if (!runtime || !bmrt_load_bmodel(runtime, model.c_str())) throw std::runtime_error("Cannot load YOLO26 bmodel");
        if (bmrt_get_network_number(runtime) != 1) throw std::runtime_error("YOLO26 requires exactly one network");
        const char** network_names = nullptr;
        bmrt_get_network_names(runtime, &network_names);
        if (!network_names || !network_names[0]) throw std::runtime_error("No network name");
        net = bmrt_get_network_info(runtime, network_names[0]);
        free(network_names);
        if (!net || net->is_dynamic || net->stage_num != 1 || net->input_num != 1 || net->output_num != 1)
            throw std::runtime_error("YOLO26 pipeline requires a static single-stage, single-input/output model");
        const auto& shape_in = net->stages[0].input_shapes[0];
        const auto& shape_out = net->stages[0].output_shapes[0];
        if (shape_in.num_dims != 4 || shape_in.dims[0] != 1 || shape_in.dims[1] != 3 || shape_in.dims[2] <= 0 || shape_in.dims[3] <= 0)
            throw std::runtime_error("YOLO26 input must be [1,3,H,W]");
        if (shape_out.num_dims != 3 || shape_out.dims[0] != 1 || shape_out.dims[1] <= 0 || shape_out.dims[2] != 6 || net->output_dtypes[0] != BM_FLOAT32)
            throw std::runtime_error("YOLO26 end-to-end output must be FP32 [1,N,6] = xyxy, score, class");
        if (net->input_dtypes[0] != BM_FLOAT32 && net->input_dtypes[0] != BM_INT8 && net->input_dtypes[0] != BM_UINT8)
            throw std::runtime_error("Unsupported input dtype: use FP32, INT8, or UINT8 model boundary");
        if (!std::isfinite(net->input_scales[0]) || net->input_scales[0] <= 0)
            throw std::runtime_error("Invalid input scale");
        net_height = shape_in.dims[2]; net_width = shape_in.dims[3];
        host_output.resize(bmrt_shape_count(&shape_out)); ++host_output_allocations;
        if (!bmrt_tensor(&input, runtime, net->input_dtypes[0], shape_in)) throw std::runtime_error("Cannot allocate input tensor");
        has_input = true;
        if (!bmrt_tensor(&output, runtime, BM_FLOAT32, shape_out)) throw std::runtime_error("Cannot allocate output tensor");
        has_output = true; ++device_output_allocations;
        int stride[3] = {FFALIGN(net_width, 64), FFALIGN(net_width, 64), FFALIGN(net_width, 64)};
        check(bm_image_create(handle, net_height, net_width, FORMAT_RGB_PLANAR, DATA_TYPE_EXT_1N_BYTE, &resized, stride), "create resized");
        has_resized = true;
        check(bm_image_alloc_dev_mem(resized, BMCV_IMAGE_FOR_IN), "allocate resized");
        const auto dtype = net->input_dtypes[0] == BM_INT8 ? DATA_TYPE_EXT_1N_BYTE_SIGNED : net->input_dtypes[0] == BM_UINT8 ? DATA_TYPE_EXT_1N_BYTE : DATA_TYPE_EXT_FLOAT32;
        check(bm_image_create(handle, net_height, net_width, FORMAT_RGB_PLANAR, dtype, &converted), "create converted");
        has_converted = true;
        check(bm_image_attach_contiguous_mem(1, &converted, input.device_mem), "attach input tensor"); attached = true;
        convert.alpha_0 = convert.alpha_1 = convert.alpha_2 = net->input_scales[0] / 255.0f;
    } catch (...) { cleanup(); throw; }
}

void Yolo26_det::cleanup() {
    if (attached) {
#if BMCV_VERSION_MAJOR > 1
        bm_image_detach_contiguous_mem(1, &converted);
#else
        bm_image_dettach_contiguous_mem(1, &converted);
#endif
        attached = false;
    }
    if (has_converted) { bm_image_destroy(converted); has_converted = false; }
    if (has_resized) { bm_image_destroy(resized); has_resized = false; }
    if (has_output) { bm_free_device(handle, output.device_mem); has_output = false; }
    if (has_input) { bm_free_device(handle, input.device_mem); has_input = false; }
    if (runtime) { bmrt_destroy(runtime); runtime = nullptr; }
    if (handle) { bm_dev_free(handle); handle = nullptr; }
}

int Yolo26_det::Detect(const std::vector<bm_image>& images, std::vector<Yolo26BoxVec>& boxes) {
    if (images.size() != 1) throw std::runtime_error("Exactly one decoded image is required");
    bm_image source = images[0];
    if (source.width % 64) throw std::runtime_error("Decoded source width must be divisible by 64");
    letterbox = yolo26_letterbox(source.width, source.height, net_width, net_height);
    m_ts->save("yolo26 preprocess", 1);
    bmcv_padding_atrr_t padding{};
    padding.dst_crop_stx = letterbox.left; padding.dst_crop_sty = letterbox.top;
    padding.dst_crop_w = letterbox.resize_width; padding.dst_crop_h = letterbox.resize_height;
    padding.padding_r = padding.padding_g = padding.padding_b = 114; padding.if_memset = 1;
    bmcv_rect_t crop{0, 0, source.width, source.height};
    if (preprocess_csc >= 0) {
        int crop_count = 1;
        check(bmcv_image_vpp_basic(handle, 1, &source, &resized, &crop_count, &crop, &padding, BMCV_INTER_LINEAR,
            static_cast<csc_type_t>(preprocess_csc), nullptr), "VPP letterbox/CSC");
    } else {
        check(bmcv_image_vpp_convert_padding(handle, 1, source, &resized, &padding, &crop), "VPP letterbox");
    }
    check(bmcv_image_convert_to(handle, 1, convert, &resized, &converted), "normalize");
    m_ts->save("yolo26 preprocess", 1);
    m_ts->save("yolo26 inference", 1);
    // Launch receives descriptor copies. Immutable owning descriptors are freed once.
    bm_tensor_t launch_input = input, launch_output = output;
    const auto before = Timer::now();
    if (!bmrt_launch_tensor_ex(runtime, net->name, &launch_input, 1, &launch_output, 1, true, false))
        throw std::runtime_error("YOLO26 model launch failed");
    const auto submitted = Timer::now();
    check(bm_thread_sync(handle), "inference sync");
    const auto synced = Timer::now();
    if (launch_output.dtype != BM_FLOAT32 || launch_output.st_mode != BM_STORE_1N ||
        !bmrt_shape_is_same(&launch_output.shape, &output.shape) ||
        bm_mem_get_device_addr(launch_output.device_mem) != bm_mem_get_device_addr(output.device_mem))
        throw std::runtime_error("Runtime changed the preallocated output contract");
    inference_submit_ms = ms(before, submitted); inference_sync_ms = ms(submitted, synced);
    m_ts->save("yolo26 inference", 1);
    m_ts->save("yolo26 postprocess", 1);
    m_ts->save("yolo26 transfer wait", 1); m_ts->save("yolo26 transfer wait", 1);
    m_ts->save("yolo26 output transfer", 1);
    const auto transfer = Timer::now();
    const size_t bytes = host_output.size() * sizeof(float);
    check(bm_memcpy_d2s_partial(handle, host_output.data(), output.device_mem, bytes), "compact output copy");
    output_copy_ms = ms(transfer, Timer::now()); output_copy_bytes += bytes;
    m_ts->save("yolo26 output transfer", 1);
    m_ts->save("yolo26 cpu postprocess", 1);
    boxes.clear(); boxes.push_back(yolo26_parse_compact(host_output.data(), host_output.size() / 6, class_count, confidence, letterbox));
    m_ts->save("yolo26 cpu postprocess", 1);
    m_ts->save("yolo26 postprocess", 1);
    return 0;
}

void Yolo26_det::save_frame(const std::string& prefix) const {
    // Export the exact VPP RGB pixels and tensor sent to the network. This allows
    // ONNX Runtime to isolate conversion/compiler differences from decode/resize.
    int sizes[4] = {};
    check(bm_image_get_byte_size(resized, sizes), "get resized sizes");
    int strides[3] = {};
    check(bm_image_get_stride(resized, strides), "get resized stride");
    // FORMAT_RGB_PLANAR normally occupies one SDK plane containing R, G, B
    // consecutively. It is not FORMAT_RGBP_SEPARATE. Honor SDK byte sizes if
    // a runtime exposes separate planes, but never index three empty planes.
    const bool separate = sizes[1] > 0 && sizes[2] > 0;
    if (sizes[0] <= 0 || strides[0] < net_width ||
        (!separate && static_cast<size_t>(sizes[0]) < static_cast<size_t>(strides[0]) * net_height * 3))
        throw std::runtime_error("Unexpected RGB_PLANAR byte layout while saving frame");
    std::vector<unsigned char> planes[3]; void* pointers[3] = {};
    for (int c = 0; c < (separate ? 3 : 1); ++c) { planes[c].resize(sizes[c]); pointers[c] = planes[c].data(); }
    check(bm_image_copy_device_to_host(resized, pointers), "copy saved RGB image");
    cv::Mat channels[3];
    for (int c = 0; c < 3; ++c) {
        unsigned char* pixels = separate ? planes[c].data() : planes[0].data() + static_cast<size_t>(c) * net_height * strides[0];
        const int stride = separate ? strides[c] : strides[0];
        channels[c] = cv::Mat(net_height, net_width, CV_8UC1, pixels, stride);
    }
    std::vector<cv::Mat> bgr_channels{channels[2], channels[1], channels[0]};
    cv::Mat bgr; cv::merge(bgr_channels, bgr);
    if (!cv::imwrite(prefix + ".preprocessed.png", bgr)) throw std::runtime_error("Cannot write saved preprocessed image");
    std::vector<unsigned char> tensor(bmrt_tensor_bytesize(&input));
    check(bm_memcpy_d2s_partial(handle, tensor.data(), input.device_mem, tensor.size()), "copy saved input");
    binary(prefix + ".input.bin", tensor.data(), tensor.size());
    binary(prefix + ".output.bin", host_output.data(), host_output.size() * sizeof(float));
}
