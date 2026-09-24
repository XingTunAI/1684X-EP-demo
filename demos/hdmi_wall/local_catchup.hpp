#ifndef HDMI_LOCAL_CATCHUP_HPP
#define HDMI_LOCAL_CATCHUP_HPP
#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <vector>

namespace local_catchup {
struct Target { uint64_t sequence=0, loop=0; size_t segment=0; bool available=false; };
// Return an actual indexed keyframe at/after wall-clock time plus preparation
// headroom. Never relabel an old decoded frame or shift the source clock.
inline Target plan(const std::vector<uint64_t>& starts, uint64_t frames, double fps,
                   uint64_t next_sequence, double elapsed, double lead_seconds) {
    if(starts.empty() || starts.front()!=0 || frames==0 || !std::isfinite(fps) || fps<=0 ||
       !std::isfinite(elapsed) || !std::isfinite(lead_seconds) || elapsed<0 || lead_seconds<0)
        throw std::invalid_argument("Invalid catchup timeline");
    const long double wanted=std::ceil((static_cast<long double>(elapsed)+lead_seconds)*fps);
    if(wanted>=static_cast<long double>(std::numeric_limits<uint64_t>::max()-frames))
        throw std::overflow_error("Catchup clock overflow");
    const uint64_t frame=static_cast<uint64_t>(wanted);
    Target result; result.loop=frame/frames;
    const auto position=frame%frames;
    for(size_t i=0;i<starts.size();++i) {
        if(starts[i]>=frames || (i && starts[i]<=starts[i-1]))
            throw std::invalid_argument("Unordered catchup segments");
        if(starts[i]>=position && !result.available) { result.segment=i; result.available=true; }
    }
    if(!result.available) { ++result.loop; result.segment=0; result.available=true; }
    result.sequence=result.loop*frames+starts[result.segment];
    if(result.sequence<=next_sequence) result.available=false;
    return result;
}
}
#endif
