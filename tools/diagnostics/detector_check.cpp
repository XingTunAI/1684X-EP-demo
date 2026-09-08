// Compare this detector with the official image example before accepting a model.
#include "yolov8_det.hpp"
#include "json.hpp"
#include <stdexcept>
#include <unistd.h>

using json = nlohmann::json;
int main(int argc, char** argv) {
    try {
        if (argc < 6) throw std::runtime_error("Usage: detector_check device model names output.json image...");
        const int device = std::stoi(argv[1]);
        if (device < 0 || access(argv[2], R_OK) || access(argv[3], R_OK))
            throw std::runtime_error("Invalid device or unreadable model/names");
        if (access(argv[4], F_OK) == 0) throw std::runtime_error("Refusing to overwrite results");
        YoloV8_det net(argv[2], argv[3], device, 0.25, 0.7);
        if (net.batch_size != 1) throw std::runtime_error("Requires batch 1");
        json results = json::array();
        for (int i = 5; i < argc; ++i) {
            const std::string path = argv[i];
            auto mat = cv::imread(path, cv::IMREAD_COLOR, device);
            if (mat.empty()) throw std::runtime_error("Image decode failed: " + path);
            bm_image image;
            if (cv::bmcv::toBMI(mat, &image) != BM_SUCCESS) throw std::runtime_error("Image bridge failed");
            struct Cleanup { bm_image& image; ~Cleanup() { bm_image_destroy(image); } } cleanup{image};
            std::vector<YoloV8BoxVec> boxes;
            if (net.Detect({image}, boxes) != 0 || boxes.size() != 1)
                throw std::runtime_error("Detection failed");
            json list = json::array();
            for (const auto& b : boxes[0])
                list.push_back({{"category_id", b.class_id}, {"score", b.score},
                    {"bbox", {b.x1, b.y1, b.x2 - b.x1, b.y2 - b.y1}}});
            results.push_back({{"image_name", path.substr(path.find_last_of("/\\") + 1)}, {"bboxes", list}});
            for (auto& item : net.m_ts->records_) item.second->clear();
            for (auto& item : net.m_ts->records_bs) item.second->clear();
        }
        std::ofstream out(argv[4]);
        out << results.dump(2) << '\n';
        if (!out) throw std::runtime_error("Cannot save results");
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "DETECTOR_CHECK_ERROR: " << e.what() << '\n';
        return 1;
    }
}
