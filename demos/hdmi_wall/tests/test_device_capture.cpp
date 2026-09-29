// On-board integration test; not part of portable CTest. Decode through EOF,
// including delayed B frames, then use a borrowed surface after closing capture.
#include "../../common/device_capture.hpp"
#include <opencv2/core/bmcv.hpp>
#include <iostream>
#include <cstdlib>
int main(int argc, char** argv) {
    if (argc != 5) {
        std::cerr << "Usage: device_capture_test INPUT EXPECTED_FRAMES DEVICE OUTPUT_IMAGE\n";
        return 2;
    }
    try {
        sophon_demo::DeviceCapture capture;
        capture.open(argv[1], std::atoi(argv[3]), "linear", 8);
        auto lease = capture.lease();
        cv::Mat retained, reference;
        int frames = 0;
        for (;;) {
            cv::Mat frame;
            capture >> frame;
            if (frame.empty()) break;
            if (!frame.avOK() || frame.avComp() || !frame.u->addr)
                throw std::runtime_error("Expected linear device YUV");
            if (!frames) {
                retained = frame;
                if (cv::bmcv::toMAT(retained, reference, true) != BM_SUCCESS)
                    throw std::runtime_error("Initial device-to-host conversion failed");
            }
            ++frames;
        }
        // Rewinding while a consumer owns a surface must not invalidate it.
        bool rejected = false;
        try { capture.rewind_local(); } catch (const std::runtime_error&) { rejected = true; }
        if (!rejected) throw std::runtime_error("Outstanding lease was not rejected");
        retained.release(); lease.reset();
        for (int loop = 0; loop < 3; ++loop) {
            if (!capture.rewind_local()) throw std::runtime_error("Linear rewind unsupported");
            int count = 0;
            for (;;) {
                auto owner = capture.lease();
                cv::Mat frame;
                capture >> frame;
                if (frame.empty()) break;
                if (!count) {
                    cv::Mat first;
                    if (cv::bmcv::toMAT(frame, first, true) != BM_SUCCESS ||
                        cv::norm(reference, first, cv::NORM_INF) != 0)
                        throw std::runtime_error("Rewind first-frame content mismatch");
                }
                ++count;
            }
            if (count != frames) throw std::runtime_error("Rewind frame count mismatch");
        }
        if (!capture.rewind_local()) throw std::runtime_error("Final rewind failed");
        lease = capture.lease(); capture >> retained;
        capture.release();
        if (frames != std::atoi(argv[2])) throw std::runtime_error("Decoded frame count differs from reference");
        cv::Mat host;
        if (cv::bmcv::toMAT(retained, host, true) != BM_SUCCESS)
            throw std::runtime_error("Retained device-to-host conversion failed");
        if (cv::norm(reference, host, cv::NORM_INF) != 0)
            throw std::runtime_error("Borrowed surface was changed while retained");
        if (!cv::imwrite(argv[4], host)) throw std::runtime_error("Cannot export retained frame after close");
        std::cout << "PASS " << frames << " frames; device surface valid after capture release\n";
        // retained is destroyed before lease, as required by DeviceCapture.
        return 0;
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
