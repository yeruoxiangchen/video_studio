// FFmpeg's documented send/receive API, with per-call ownership and bounded output.
// References and upstream example attribution: native/media/README.md.
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/display.h>
#include <libavutil/error.h>
#include <libswscale/swscale.h>
}
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <memory>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>
namespace py = pybind11;
using Clock = std::chrono::steady_clock;

struct FormatFree { void operator()(AVFormatContext* p) const { avformat_close_input(&p); } };
struct CodecFree { void operator()(AVCodecContext* p) const { avcodec_free_context(&p); } };
struct FrameFree { void operator()(AVFrame* p) const { av_frame_free(&p); } };
struct PacketFree { void operator()(AVPacket* p) const { av_packet_free(&p); } };
struct ScaleFree { void operator()(SwsContext* p) const { sws_freeContext(p); } };
struct Sample { std::vector<std::uint8_t> pixels; int width{}, height{}; double seconds{}; };
static std::string failure(const char* label, int error) {
    char text[AV_ERROR_MAX_STRING_SIZE]; av_strerror(error, text, sizeof(text));
    return std::string(label) + ": " + text;
}

class Decoder {
    Clock::time_point deadline_;
    std::unique_ptr<AVFormatContext, FormatFree> format_;
    std::unique_ptr<AVCodecContext, CodecFree> codec_;
    std::unique_ptr<AVFrame, FrameFree> frame_{av_frame_alloc()};
    std::unique_ptr<AVPacket, PacketFree> packet_{av_packet_alloc()};
    std::unique_ptr<SwsContext, ScaleFree> scale_;
    AVStream* stream_{};
    int stream_index_{}, rotation_{};
    std::int64_t origin_{};
    bool draining_{false}, has_frame_{false};
    double current_{-1};
    static int interrupt(void* owner) {
        return Clock::now() >= static_cast<Decoder*>(owner)->deadline_;
    }
    void check_time() const {
        if (Clock::now() >= deadline_) throw std::runtime_error("native frame sampling timed out");
    }
    bool next() {
        for (;;) {
            check_time();
            av_frame_unref(frame_.get());
            int result = avcodec_receive_frame(codec_.get(), frame_.get());
            if (result == 0) {
                auto pts = frame_->best_effort_timestamp;
                if (pts == AV_NOPTS_VALUE) throw std::runtime_error("decoded frame has no timestamp");
                current_ = (pts - origin_) * av_q2d(stream_->time_base);
                has_frame_ = true;
                return true;
            }
            if (result == AVERROR_EOF) return false;
            if (result != AVERROR(EAGAIN)) throw std::runtime_error(failure("receive frame", result));
            if (draining_) return false;
            for (;;) {
                check_time();
                result = av_read_frame(format_.get(), packet_.get());
                if (result == AVERROR_EOF) {
                    draining_ = true;
                    result = avcodec_send_packet(codec_.get(), nullptr);
                    if (result < 0 && result != AVERROR_EOF)
                        throw std::runtime_error(failure("drain decoder", result));
                    break;
                }
                if (result < 0) throw std::runtime_error(failure("read packet", result));
                if (packet_->stream_index != stream_index_) { av_packet_unref(packet_.get()); continue; }
                result = avcodec_send_packet(codec_.get(), packet_.get());
                av_packet_unref(packet_.get());
                if (result < 0) throw std::runtime_error(failure("send packet", result));
                break;
            }
        }
    }
public:
    Decoder(const std::string& path, int threads, double timeout)
        : deadline_(Clock::now() + std::chrono::milliseconds(static_cast<long long>(timeout * 1000))) {
        if (!frame_ || !packet_) throw std::bad_alloc();
        AVFormatContext* raw = avformat_alloc_context();
        if (!raw) throw std::bad_alloc();
        raw->interrupt_callback = {interrupt, this};
        AVDictionary* options = nullptr;
        av_dict_set(&options, "protocol_whitelist", "file,crypto,data", 0);
        int result = avformat_open_input(&raw, path.c_str(), nullptr, &options);
        av_dict_free(&options);
        format_.reset(raw);
        if (result < 0) throw std::runtime_error(failure("open media", result));
        result = avformat_find_stream_info(format_.get(), nullptr);
        if (result < 0) throw std::runtime_error(failure("read media info", result));
        stream_index_ = av_find_best_stream(format_.get(), AVMEDIA_TYPE_VIDEO, -1, -1, nullptr, 0);
        if (stream_index_ < 0) throw std::runtime_error("media has no decodable video stream");
        stream_ = format_->streams[stream_index_];
        origin_ = stream_->start_time == AV_NOPTS_VALUE ? 0 : stream_->start_time;
        const AVCodec* implementation = avcodec_find_decoder(stream_->codecpar->codec_id);
        if (!implementation) throw std::runtime_error("video decoder is unavailable");
        codec_.reset(avcodec_alloc_context3(implementation));
        if (!codec_) throw std::bad_alloc();
        result = avcodec_parameters_to_context(codec_.get(), stream_->codecpar);
        if (result < 0) throw std::runtime_error(failure("copy codec parameters", result));
        codec_->thread_count = threads;
        result = avcodec_open2(codec_.get(), implementation, nullptr);
        if (result < 0) throw std::runtime_error(failure("open decoder", result));
#if LIBAVFORMAT_VERSION_MAJOR < 59
        int size = 0;
#else
        size_t size = 0;
#endif
        const auto* matrix = av_stream_get_side_data(stream_, AV_PKT_DATA_DISPLAYMATRIX, &size);
        if (matrix && size >= 9 * sizeof(std::int32_t)) {
            double angle = -av_display_rotation_get(reinterpret_cast<const std::int32_t*>(matrix));
            if (!std::isfinite(angle) || std::abs(angle / 90 - std::round(angle / 90)) > 0.001)
                throw std::runtime_error("native sampling requires a right-angle display rotation");
            rotation_ = (static_cast<int>(std::llround(angle)) % 360 + 360) % 360;
        }
    }
    Sample at(double target, int width) {
        check_time();
        auto stamp = origin_ + static_cast<std::int64_t>(std::llround(target / av_q2d(stream_->time_base)));
        bool seek = (!has_frame_ && target > 5) || (has_frame_ && target - current_ > 5);
        // Skip intervening GOPs, but do not restart decoding inside the current GOP.
        int index = av_index_search_timestamp(stream_, stamp, AVSEEK_FLAG_BACKWARD);
        if (index >= 0) {
#if LIBAVFORMAT_VERSION_MAJOR < 59
            const AVIndexEntry* entry = &stream_->index_entries[index];
#else
            const AVIndexEntry* entry = avformat_index_get_entry(stream_, index);
#endif
            if (entry) seek = (entry->timestamp - origin_) * av_q2d(stream_->time_base) >
                (has_frame_ ? current_ : 0) + 0.1;
        }
        if (seek) {
            int result = av_seek_frame(format_.get(), stream_index_, stamp, AVSEEK_FLAG_BACKWARD);
            if (result < 0) throw std::runtime_error(failure("seek video", result));
            avcodec_flush_buffers(codec_.get());
            has_frame_ = draining_ = false;
        }
        while (!has_frame_ || current_ + 1e-8 < target)
            if (!next()) throw std::runtime_error("requested timestamp is past the last decoded frame");
        const int in_width = frame_->width, in_height = frame_->height;
        if (in_width < 1 || in_height < 1 || in_width > 16384 || in_height > 16384)
            throw std::runtime_error("decoded frame dimensions exceed native sampling bounds");
        const bool sideways = rotation_ == 90 || rotation_ == 270;
        int height = std::max(2, static_cast<int>(std::llround(
            static_cast<double>(sideways ? in_width : in_height) * width /
            (sideways ? in_height : in_width) / 2)) * 2);
        if (height > 8192) throw std::runtime_error("scaled frame height exceeds native sampling bounds");
        const int scaled_width = sideways ? height : width;
        const int scaled_height = sideways ? width : height;
        SwsContext* context = sws_getCachedContext(scale_.release(), in_width, in_height,
            static_cast<AVPixelFormat>(frame_->format), scaled_width, scaled_height,
            AV_PIX_FMT_RGB24, SWS_BICUBIC, nullptr, nullptr, nullptr);
        scale_.reset(context);
        if (!scale_) throw std::runtime_error("could not create pixel converter");
        int colorspace = SWS_CS_DEFAULT;
        if (frame_->colorspace == AVCOL_SPC_BT709) colorspace = SWS_CS_ITU709;
        else if (frame_->colorspace == AVCOL_SPC_BT2020_NCL || frame_->colorspace == AVCOL_SPC_BT2020_CL)
            colorspace = SWS_CS_BT2020;
        const int* coefficients = sws_getCoefficients(colorspace);
        if (sws_setColorspaceDetails(scale_.get(), coefficients,
            frame_->color_range == AVCOL_RANGE_JPEG, coefficients, 1, 0, 1 << 16, 1 << 16) < 0)
            throw std::runtime_error("could not set pixel conversion colorspace");
        Sample sample;
        sample.width = width; sample.height = height; sample.seconds = current_;
        sample.pixels.resize(static_cast<size_t>(scaled_width) * scaled_height * 3);
        std::uint8_t* destinations[4] = {sample.pixels.data(), nullptr, nullptr, nullptr};
        int strides[4] = {scaled_width * 3, 0, 0, 0};
        int rows = sws_scale(scale_.get(), frame_->data, frame_->linesize, 0, in_height, destinations, strides);
        if (rows != scaled_height) throw std::runtime_error("incomplete scaled frame");
        if (rotation_) {
            std::vector<std::uint8_t> oriented(sample.pixels.size());
            for (int y = 0; y < scaled_height; ++y) for (int x = 0; x < scaled_width; ++x) {
                int out_x, out_y;
                if (rotation_ == 90) { out_x = scaled_height - 1 - y; out_y = x; }
                else if (rotation_ == 270) { out_x = y; out_y = scaled_width - 1 - x; }
                else { out_x = scaled_width - 1 - x; out_y = scaled_height - 1 - y; }
                std::memcpy(oriented.data() + (static_cast<size_t>(out_y) * width + out_x) * 3,
                    sample.pixels.data() + (static_cast<size_t>(y) * scaled_width + x) * 3, 3);
            }
            sample.pixels = std::move(oriented);
        }
        return sample;
    }
};

static py::list sample_frames(const std::string& path, const std::vector<double>& times,
                             int width, int threads, double timeout) {
    if (times.size() > 128 || width < 2 || width > 4096 || threads < 1 || threads > 8 ||
        !std::isfinite(timeout) || timeout <= 0 || timeout > 300)
        throw std::invalid_argument("invalid native sampling bounds");
    for (double value : times) if (!std::isfinite(value) || value < 0)
        throw std::invalid_argument("sampling times must be finite and nonnegative");
    if (!std::filesystem::is_regular_file(path)) throw std::invalid_argument("media must be a local regular file");
    std::vector<size_t> order(times.size()); std::iota(order.begin(), order.end(), 0);
    std::stable_sort(order.begin(), order.end(), [&](size_t a, size_t b) { return times[a] < times[b]; });
    std::vector<Sample> samples(times.size());
    {
        py::gil_scoped_release release;
        if (!order.empty()) {
            Decoder decoder(path, threads, timeout);
            size_t allocated = 0;
            for (size_t index : order) {
                samples[index] = decoder.at(times[index], width);
                allocated += samples[index].pixels.size();
                if (allocated > 256 * 1024 * 1024) throw std::runtime_error("native frame batch exceeds 256 MiB");
            }
        }
    }
    py::list result;
    for (auto& sample : samples) {
        auto* pixels = new std::vector<std::uint8_t>(std::move(sample.pixels));
        py::capsule owner(pixels, [](void* p) { delete static_cast<std::vector<std::uint8_t>*>(p); });
        py::array_t<std::uint8_t> array({sample.height, sample.width, 3},
            {sample.width * 3, 3, 1}, pixels->data(), owner);
        py::dict item;
        item["pixels"] = std::move(array); item["seconds"] = sample.seconds;
        result.append(std::move(item));
    }
    return result;
}
PYBIND11_MODULE(_camcat_media_native, m) {
    m.doc() = "Owned, bounded FFmpeg frame batches; heavy decoding releases the GIL.";
    m.def("sample_frames", &sample_frames, py::arg("path"), py::arg("times"),
          py::arg("width") = 512, py::arg("threads") = 2, py::arg("timeout") = 60.0);
    m.attr("ffmpeg_version") = av_version_info();
}
