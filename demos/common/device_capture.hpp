#pragma once

// A capture and its borrowed device surfaces share a decoder lifetime. Linear
// mode configures the vendor FFmpeg decoder directly; no SDK patch or preload.
#include <opencv2/opencv.hpp>
extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/error.h>
}
#include <chrono>
#include <functional>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>

namespace sophon_demo {
class DeviceCapture {
    struct Session {
        cv::VideoCapture legacy;
        AVFormatContext* input = nullptr;
        AVCodecContext* codec = nullptr;
        AVFrame* frame = nullptr;
        AVPacket* packet = nullptr;
        int video = -1, device = 0;
        bool linear = false, draining = false;
        double fps = 0;
        std::function<bool()> cancelled;
        std::chrono::steady_clock::time_point deadline;
        ~Session() {
            av_frame_free(&frame);
            av_packet_free(&packet);
            avcodec_free_context(&codec);
            avformat_close_input(&input);
        }
        void arm() { deadline = std::chrono::steady_clock::now() + std::chrono::seconds(30); }
        static int interrupt(void* opaque) {
            auto& s = *static_cast<Session*>(opaque);
            return (s.cancelled && s.cancelled()) || std::chrono::steady_clock::now() >= s.deadline;
        }
    };
    std::shared_ptr<Session> session_;
    static void check(int result, const char* operation) {
        if (result >= 0) return;
        char message[AV_ERROR_MAX_STRING_SIZE];
        av_strerror(result, message, sizeof(message));
        throw std::runtime_error(std::string(operation) + ": " + message);
    }
public:
    DeviceCapture() = default;
    DeviceCapture(const DeviceCapture&) = delete;
    DeviceCapture& operator=(const DeviceCapture&) = delete;
    void open(const std::string& source, int device, const std::string& mode, int buffers,
              std::function<bool()> cancelled = {}) {
        if ((mode != "opencv" && mode != "linear") || buffers < 2 || buffers > 8)
            throw std::runtime_error("Invalid decoder mode or extra buffer count (supported: 2..8)");
        release();
        auto s = std::make_shared<Session>();
        s->device = device; s->linear = mode == "linear"; s->cancelled = cancelled;
        if (!s->linear) {
            if (!s->legacy.open(source, cv::CAP_FFMPEG, device) ||
                !s->legacy.set(cv::CAP_PROP_OUTPUT_YUV, 1) || s->legacy.get(cv::CAP_PROP_OUTPUT_YUV) != 1)
                throw std::runtime_error("OpenCV decoder cannot open source with device YUV output");
        } else {
            static std::once_flag network;
            std::call_once(network, [] { avformat_network_init(); });
            s->input = avformat_alloc_context();
            if (!s->input) throw std::bad_alloc();
            s->arm();
            s->input->interrupt_callback = {Session::interrupt, s.get()};
            AVDictionary* options = nullptr;
            av_dict_set(&options, "rtsp_flags", "prefer_tcp", 0);
            av_dict_set(&options, "stimeout", "20000000", 0);
            int result = avformat_open_input(&s->input, source.c_str(), nullptr, &options);
            av_dict_free(&options);
            check(result, "Open input");
            check(avformat_find_stream_info(s->input, nullptr), "Probe input");
            s->video = av_find_best_stream(s->input, AVMEDIA_TYPE_VIDEO, -1, -1, nullptr, 0);
            check(s->video, "Find video stream");
            auto* stream = s->input->streams[s->video];
            const auto id = stream->codecpar->codec_id;
            const char* name = id == AV_CODEC_ID_H264 ? "h264_bm" : id == AV_CODEC_ID_HEVC ? "hevc_bm" : nullptr;
            if (!name) throw std::runtime_error("Linear decoder supports H.264/H.265; select --decoder opencv for other codecs");
            auto* decoder = avcodec_find_decoder_by_name(name);
            if (!decoder) throw std::runtime_error(std::string("Missing SOPHON decoder: ") + name);
            s->codec = avcodec_alloc_context3(decoder);
            if (!s->codec) throw std::bad_alloc();
            check(avcodec_parameters_to_context(s->codec, stream->codecpar), "Copy codec parameters");
            s->codec->pkt_timebase = stream->time_base;
            av_dict_set_int(&options, "output_format", 0, 0);
            av_dict_set_int(&options, "extra_frame_buffer_num", buffers, 0);
            av_dict_set_int(&options, "zero_copy", 1, 0);
            av_dict_set_int(&options, "sophon_idx", device, 0);
            av_dict_set_int(&options, "refcounted_frames", 1, 0);
            result = avcodec_open2(s->codec, decoder, &options);
            const bool unused = av_dict_count(options) != 0;
            av_dict_free(&options);
            check(result, "Open linear device decoder");
            if (unused) throw std::runtime_error("Device decoder did not consume all requested options");
            s->fps = av_q2d(av_guess_frame_rate(s->input, stream, nullptr));
            s->frame = av_frame_alloc(); s->packet = av_packet_alloc();
            if (!s->frame || !s->packet) throw std::bad_alloc();
        }
        session_ = std::move(s);
    }
    // Keep this lease before the associated Mat member so Mat dies first.
    std::shared_ptr<void> lease() const { return session_; }
    void release() { session_.reset(); }
    double get(int property) const {
        auto& s = *session_;
        if (!s.linear) return s.legacy.get(property);
        if (property == cv::CAP_PROP_FPS) return s.fps;
        if (property == cv::CAP_PROP_FRAME_WIDTH) return s.codec->width;
        if (property == cv::CAP_PROP_FRAME_HEIGHT) return s.codec->height;
        throw std::runtime_error("Unsupported capture property");
    }
    bool grab() {
        auto& s = *session_;
        if (!s.linear) return s.legacy.grab();
        av_frame_unref(s.frame); s.arm();
        for (;;) {
            if (Session::interrupt(&s)) throw std::runtime_error("Device decode cancelled or timed out");
            int result = avcodec_receive_frame(s.codec, s.frame);
            if (result == 0) return true;
            if (result == AVERROR_EOF) return false;
            if (result != AVERROR(EAGAIN)) check(result, "Receive decoded frame");
            if (s.draining) throw std::runtime_error("Decoder requested packets after drain");
            do {
                av_packet_unref(s.packet);
                result = av_read_frame(s.input, s.packet);
            } while (result >= 0 && s.packet->stream_index != s.video);
            if (result == AVERROR_EOF) {
                s.draining = true;
                check(avcodec_send_packet(s.codec, nullptr), "Drain decoder");
            } else {
                check(result, "Read video packet");
                result = avcodec_send_packet(s.codec, s.packet);
                av_packet_unref(s.packet);
                check(result, "Send video packet");
            }
        }
    }
    bool retrieve(cv::Mat& out) {
        auto& s = *session_;
        if (!s.linear) return s.legacy.retrieve(out);
        if (!s.frame->data[4]) throw std::runtime_error("Decoder returned no device surface");
        AVFrame* owned = av_frame_clone(s.frame);
        if (!owned) throw std::bad_alloc();
        // SOPHON Mat owns and frees this AVFrame. The decoder retains its own
        // reference until the next grab; downstream consumers retain theirs.
        out = cv::Mat(owned, s.device);
        if (out.avComp()) throw std::runtime_error("Linear decoder returned compressed output");
        return !out.empty();
    }
    DeviceCapture& operator>>(cv::Mat& out) {
        out.release();
        if (grab()) retrieve(out);
        return *this;
    }
};
} // namespace sophon_demo
