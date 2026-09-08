#include "detector/compact_output.hpp"
#include <cassert>
#include <limits>
#include <iostream>

int main() {
    for (const int size : {320, 416, 640}) {
        const auto l = yolo26_letterbox(1920, 1080, size, size);
        assert(l.resize_width == size && l.left == 0);
        float rows[] = {l.left + 100*l.gain, l.top + 200*l.gain,
                        l.left + 400*l.gain, l.top + 600*l.gain, .8f, 0,
                        l.left + 100*l.gain, l.top + 200*l.gain,
                        l.left + 400*l.gain, l.top + 600*l.gain, .7f, 1,
                        0, 0, 1, 1, .25f, 79};
        auto boxes = yolo26_parse_compact(rows, 3, 80, .25f, l);
        assert(boxes.size() == 2); // Overlapping rows must survive: there is no CPU NMS.
        assert(boxes[0].class_id == 0 && boxes[1].class_id == 1);
        assert(std::fabs(boxes[0].x1 - 100) < .001f && std::fabs(boxes[0].y2 - 600) < .001f);
        rows[5] = .25f;
        bool rejected = false;
        try { yolo26_parse_compact(rows, 3, 80, .25f, l); } catch (const std::runtime_error&) { rejected = true; }
        assert(rejected);
        rows[5] = 0; rows[4] = std::numeric_limits<float>::quiet_NaN(); rejected = false;
        try { yolo26_parse_compact(rows, 3, 80, .25f, l); } catch (const std::runtime_error&) { rejected = true; }
        assert(rejected);
    }
    const auto odd = yolo26_letterbox(1920, 1088, 416, 416);
    assert(odd.resize_height == 236 && odd.top == 90);
    const float clipped[] = {-100, -100, 10000, 10000, .9f, 79};
    const auto boxes = yolo26_parse_compact(clipped, 1, 80, .25f, odd);
    assert(boxes[0].x1 == 0 && boxes[0].y1 == 0 && boxes[0].x2 == 1920 && boxes[0].y2 == 1088);
    std::cout << "YOLO26 compact contract checks passed\n";
}
