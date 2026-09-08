// YOLO26 end-to-end output contract. This file has no SDK dependencies.
#pragma once
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <stdexcept>
#include <vector>

struct Yolo26Box {
    float x1, y1, x2, y2, score;
    int class_id;
};
using Yolo26BoxVec = std::vector<Yolo26Box>;

struct Letterbox {
    int source_width = 0, source_height = 0;
    int resize_width = 0, resize_height = 0, left = 0, top = 0;
    float gain = 0;
};

inline Letterbox yolo26_letterbox(int width, int height, int net_width, int net_height) {
    if (width <= 0 || height <= 0 || net_width <= 0 || net_height <= 0)
        throw std::runtime_error("Invalid source/model dimensions");
    Letterbox box;
    box.source_width = width; box.source_height = height;
    box.gain = std::min(static_cast<float>(net_width) / width, static_cast<float>(net_height) / height);
    // Ultralytics LetterBox: rounded resize size, odd padding biased to right/bottom.
    box.resize_width = static_cast<int>(std::round(width * box.gain));
    box.resize_height = static_cast<int>(std::round(height * box.gain));
    box.left = (net_width - box.resize_width) / 2;
    box.top = (net_height - box.resize_height) / 2;
    return box;
}

inline Yolo26BoxVec yolo26_parse_compact(const float* rows, size_t count, int classes,
                                        float confidence, const Letterbox& letterbox) {
    if (!rows || classes <= 0 || !std::isfinite(confidence) || confidence < 0 || confidence > 1 ||
        !std::isfinite(letterbox.gain) || letterbox.gain <= 0)
        throw std::runtime_error("Invalid compact-output parser parameters");
    Yolo26BoxVec detections;
    detections.reserve(count);
    for (size_t i = 0; i < count; ++i) {
        const float* p = rows + i * 6;
        for (int j = 0; j < 6; ++j)
            if (!std::isfinite(p[j])) throw std::runtime_error("YOLO26 output contains nonfinite data");
        if (p[4] < 0 || p[4] > 1.00001f) throw std::runtime_error("YOLO26 score is outside probability range");
        if (p[4] <= confidence) continue;
        if (p[5] < 0 || p[5] >= classes || std::fabs(p[5] - std::round(p[5])) > 0.001f)
            throw std::runtime_error("YOLO26 output class index violates [xyxy, score, class] contract");
        if (p[2] < p[0] || p[3] < p[1]) throw std::runtime_error("YOLO26 output has reversed xyxy corners");
        const auto x = [&](float value) { return std::max(0.0f, std::min(static_cast<float>(letterbox.source_width), (value - letterbox.left) / letterbox.gain)); };
        const auto y = [&](float value) { return std::max(0.0f, std::min(static_cast<float>(letterbox.source_height), (value - letterbox.top) / letterbox.gain)); };
        detections.push_back({x(p[0]), y(p[1]), x(p[2]), y(p[3]), p[4], static_cast<int>(std::round(p[5]))});
    }
    // The exported end-to-end graph selects detections. Do not run a second NMS,
    // convert xywh, multiply by objectness, or take a second sigmoid here.
    return detections;
}
