#pragma once
#include <condition_variable>
#include <mutex>
#include <cstdlib>
#include <stdexcept>
#include <cstdint>
namespace vpp_admission {
class Gate {
    std::mutex mutex_; std::condition_variable changed_;
    unsigned limit_, active_=0; uint64_t next_=0, serving_=0;
public:
    explicit Gate(unsigned limit):limit_(limit){}
    void enter(){if(!limit_)return;std::unique_lock<std::mutex> lock(mutex_);auto ticket=next_++;
        changed_.wait(lock,[&]{return ticket==serving_ && active_<limit_;});++serving_;++active_;changed_.notify_all();}
    void leave(){if(!limit_)return;std::lock_guard<std::mutex> lock(mutex_);--active_;changed_.notify_all();}
};
inline Gate& global(){static Gate gate([]{const char*p=std::getenv("HDMI_VPP_LIMIT");if(!p)return 0u;char*end=nullptr;long n=std::strtol(p,&end,10);if(!*p||*end||n<0||n>32)throw std::runtime_error("HDMI_VPP_LIMIT must be 0..32");return unsigned(n);}());return gate;}
class Lease {
    Gate* gate_;
public:
    explicit Lease(Gate& gate=global()):gate_(&gate){gate_->enter();}
    ~Lease(){release();}
    void release(){if(gate_){gate_->leave();gate_=nullptr;}}
    Lease(const Lease&)=delete;Lease&operator=(const Lease&)=delete;
};
}
