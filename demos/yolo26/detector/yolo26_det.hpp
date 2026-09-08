// Device image handling is adapted from Sophgo SOPHON-DEMO (BSD-2-Clause).
#pragma once
#include "compact_output.hpp"
#include "opencv2/opencv.hpp"
#include "utils.hpp"
#define USE_OPENCV 1
#include "bm_wrapper.hpp"
#include <cstdint>
#include <string>
#include <vector>

class Yolo26_det {
    bm_handle_t handle = nullptr;
    void* runtime = nullptr;
    const bm_net_info_t* net = nullptr;
    bm_tensor_t input{}, output{};
    bm_image resized{}, converted{};
    bool has_input = false, has_output = false, has_resized = false, has_converted = false, attached = false;
    int net_width = 0, net_height = 0, class_count = 0;
    float confidence = 0.25f;
    bmcv_convert_to_attr convert{};
    std::vector<float> host_output;
    TimeStamp timestamps;
    Letterbox letterbox;
    void cleanup();
public:
    int preprocess_csc = -1;
    int batch_size = 1;
    TimeStamp* m_ts = &timestamps;
    uint64_t host_output_allocations = 0, device_output_allocations = 0, output_copy_bytes = 0;
    double inference_submit_ms = 0, inference_sync_ms = 0, input_release_ms = 0;
    double output_allocation_ms = 0, output_copy_ms = 0;
    Yolo26_det(const std::string& model, const std::string& names, int device, float conf);
    ~Yolo26_det() { cleanup(); }
    Yolo26_det(const Yolo26_det&) = delete;
    Yolo26_det& operator=(const Yolo26_det&) = delete;
    int Detect(const std::vector<bm_image>& images, std::vector<Yolo26BoxVec>& boxes);
    void save_frame(const std::string& prefix) const;
    int input_width() const { return net_width; }
    int input_height() const { return net_height; }
    int output_rows() const { return static_cast<int>(host_output.size() / 6); }
    int input_dtype() const { return static_cast<int>(input.dtype); }
    float input_scale() const { return net->input_scales[0]; }
    const Letterbox& last_letterbox() const { return letterbox; }
};
