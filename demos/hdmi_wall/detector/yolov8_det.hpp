//===----------------------------------------------------------------------===//
//
// Copyright (C) 2022 Sophgo Technologies Inc.  All rights reserved.
//
// SOPHON-DEMO is licensed under the 2-Clause BSD License except for the
// third-party components.
//
//===----------------------------------------------------------------------===//

#ifndef YOLOV8_DET_H
#define YOLOV8_DET_H

#include <iostream>
#include <fstream>
#include <vector>
#include <cstdint>
#include <cstddef>
#include "opencv2/opencv.hpp"
#include "utils.hpp"
// Define USE_OPENCV for enabling OPENCV related funtions in bm_wrapper.hpp
#define USE_OPENCV 1
#include "bm_wrapper.hpp"
#define DEBUG 0

struct YoloV8Box {
    float x1, y1, x2, y2;
    float score;
    int class_id;
};

using YoloV8BoxVec = std::vector<YoloV8Box>;

struct ScoreGateMetrics {
    uint64_t kernel_calls = 0, score_calls = 0, row_calls = 0, full_calls = 0;
    uint64_t selected_rows = 0, ranges = 0, score_bytes = 0, row_bytes = 0, full_bytes = 0;
    uint64_t planned_read_ranges = 0, copied_rows = 0, extra_row_bytes = 0;
    double kernel_ms = 0, submit_ms = 0, sync_ms = 0, score_ms = 0, row_ms = 0, full_ms = 0, host_zero_ms = 0;
    bool fallback = false;
    std::string fallback_reason;
    uint64_t total_bytes() const { return score_bytes + row_bytes + full_bytes; }
};

class YoloV8_det {
    bm_handle_t handle;
    void *bmrt = NULL;
    const bm_net_info_t *netinfo = NULL;
    std::vector<std::string> network_names;
    bm_misc_info misc_info;

    // configuration
    bool agnostic = false;
    float m_confThreshold = 0.25;
    float m_nmsThreshold = 0.7;
    std::vector<std::string> m_class_names;
    int m_class_num = -1;
    int m_net_h, m_net_w;
    int max_det = 300;
    int max_wh = 7680;  // (pixels) maximum box width and height
    bmcv_convert_to_attr converto_attr;
    TimeStamp tmp_ts;
    bool is_output_transposed = true;
    std::vector<bm_tensor_t> resident_outputs;
    std::vector<float> host_output_cache;
    void prepare_output_buffers();
    // Each detector is used serially by its channel's inference thread. The
    // fixed network-size images borrow one persistent input tensor allocation.
    std::vector<bm_image> resized_images;
    std::vector<bm_image> converted_images;
    int resized_images_created = 0, converted_images_created = 0;
    bool resized_memory_allocated = false, converted_memory_attached = false;
    bm_tensor_t resident_input{};
    bm_device_mem_t resident_input_owned{};
    bool input_memory_allocated = false, preprocess_buffers_ready = false;
    void prepare_preprocess_buffers();
    void release_preprocess_buffers() noexcept;
    void* gate_runtime = nullptr;
    const bm_net_info_t* gate_netinfo = nullptr;
    std::string gate_network_name;
    bm_tensor_t gate_output{};
    bm_device_mem_t gate_output_owned{}; // Immutable owning allocation; launch receives only a descriptor copy.
    bool gate_output_allocated = false;
    std::vector<float> gate_host_maxima;
    std::vector<int> gate_candidate_rows;
    bool gate_candidate_rows_valid = false;
    void prepare_score_gate();
    bm_status_t read_score_gated_output(bm_tensor_t* tensor, float* host);

private:
    int pre_process(const std::vector<bm_image>& images,
                    bm_tensor_t& input_tensor,
                    std::vector<std::pair<int, int>>& txy_batch,
                    std::vector<std::pair<float, float>>& ratios_batch);
    int forward(bm_tensor_t& input_tensor, std::vector<bm_tensor_t>& output_tensors);
    float* get_cpu_data(bm_tensor_t* tensor, float scale);
    int post_process(const std::vector<bm_image>& input_images,
                     std::vector<bm_tensor_t>& output_tensors,
                     const std::vector<std::pair<int, int>>& txy_batch,
                     const std::vector<std::pair<float, float>>& ratios_batch,
                     std::vector<YoloV8BoxVec>& boxes);
    static float get_aspect_scaled_ratio(int src_w, int src_h, int dst_w, int dst_h, bool* alignWidth);
    int argmax(float* data, int num);
    void xywh2xyxy(YoloV8BoxVec& xyxyboxes, std::vector<std::vector<float>> box);
    void NMS(YoloV8BoxVec& dets, float nmsConfidence);
    void clip_boxes(YoloV8BoxVec& yolobox_vec, int src_w, int src_h);
public:
    int preprocess_csc = -1; // -1 retains the reference BGR path; otherwise an explicit SDK CSC.
    bool score_gate_enabled = false;
    bool score_gate_sparse_cpu = false;
    std::size_t score_gate_merge_budget_kib = 0; // Additional D2H KiB allowed when bridging candidate ranges.
    std::string score_gate_model;
    void initialize_score_gate() { prepare_score_gate(); }
    ScoreGateMetrics score_gate_metrics;
    uint64_t score_gate_buffer_allocations = 0;
    bool reuse_output_buffers = false;
    size_t host_output_allocations = 0, device_output_allocations = 0;
    size_t output_copy_bytes = 0;
    double output_allocation_ms = -1, output_copy_ms = -1;
    double inference_submit_ms = -1, inference_sync_ms = -1, input_release_ms = -1;
    std::string transfer_lock_path; // Empty disables the per-run transfer gate.
    int batch_size = -1;
    TimeStamp* m_ts = NULL;
    YoloV8_det(const YoloV8_det&) = delete;
    YoloV8_det& operator=(const YoloV8_det&) = delete;
    YoloV8_det(std::string bmodel_file, std::string coco_names_file, int dev_id = 0, float confThresh = 0.25, float nmsThresh = 0.7){
        std::ifstream ifs(coco_names_file);
        if (ifs.is_open()) {
            std::string line;
            while (std::getline(ifs, line)) {
                line = line.substr(0, line.length() - 1);
                m_class_names.push_back(line);
            }
        }

        // set thresh
        m_confThreshold = confThresh;
        m_nmsThreshold = nmsThresh;

        // get handle
        auto ret = bm_dev_request(&handle, dev_id);
        assert(BM_SUCCESS == ret);

        // judge now is pcie or soc
        ret = bm_get_misc_info(handle, &misc_info);
        assert(BM_SUCCESS == ret);

        // create bmrt
        bmrt = bmrt_create(handle);
        if (!bmrt_load_bmodel(bmrt, bmodel_file.c_str())) {
            std::cout << "load bmodel(" << bmodel_file << ") failed" << std::endl;
        }

        // get network names from bmodel
        const char **names;
        int num = bmrt_get_network_number(bmrt);
        if (num > 1){
            std::cout << "This bmodel have " << num << " networks, and this program will only take network 0." << std::endl;
        }
        bmrt_get_network_names(bmrt, &names);
        for(int i = 0; i < num; ++i) {
            network_names.push_back(names[i]);
        }
        free(names);

        // get netinfo by netname
        netinfo = bmrt_get_network_info(bmrt, network_names[0].c_str());
        if (netinfo->stage_num > 1){
            std::cout << "This bmodel have " << netinfo->stage_num << " stages, and this program will only take stage 0." << std::endl;
        }
        batch_size = netinfo->stages[0].input_shapes[0].dims[0];
        m_net_h = netinfo->stages[0].input_shapes[0].dims[2];
        m_net_w = netinfo->stages[0].input_shapes[0].dims[3];

        for (int i = 0; i < netinfo->output_num; i++) {
            auto& shape = netinfo->stages[0].output_shapes[i];
            if (shape.num_dims == 3) {
                m_class_num = shape.dims[2] - 4;
                if (shape.dims[1] < shape.dims[2]) {
                    std::cout << "Your model's output is not efficient for cpp, please refer to the docs/YOLOv8_Export_Guide.md to export model which has transposed output." << std::endl;
                    m_class_num = shape.dims[1] - 4;
                    is_output_transposed = false;
                }
            }
        }
        if (m_class_num == -1) {
            throw std::runtime_error("Invalid model output shape.");
        }

        float input_scale = netinfo->input_scales[0] / 255.f;
        converto_attr.alpha_0 = input_scale;
        converto_attr.beta_0 = 0;
        converto_attr.alpha_1 = input_scale;
        converto_attr.beta_1 = 0;
        converto_attr.alpha_2 = input_scale;
        converto_attr.beta_2 = 0;

        // set temp timestamp
        m_ts = &tmp_ts;
    }
    ~YoloV8_det(){
        release_preprocess_buffers();
        if (gate_output_allocated) bm_free_device(handle, gate_output_owned);
        if (gate_runtime) bmrt_destroy(gate_runtime);
        for (auto& tensor : resident_outputs) bm_free_device(handle, tensor.device_mem);
        if (bmrt!=NULL) {
            bmrt_destroy(bmrt);
            bmrt = NULL;
        }
        bm_dev_free(handle);
    };
    int Detect(const std::vector<bm_image>& images, std::vector<YoloV8BoxVec>& boxes, bool readback = true);
    void draw_result(cv::Mat& img, YoloV8BoxVec& result);
};

#endif  //! YOLOV8_DET_H
